"""Puertas (gates) G1–G5: chequeos automáticos + checklist humano.

Los chequeos automáticos son sensores baratos sobre los artefactos; no sustituyen la
firma humana. Un gate solo "pasa" cuando los automáticos están en verde **y** el humano
marca los manuales.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from rumbo.proyecto import Proyecto, documentos_numerados, es_plantilla, seccion

MAX_DIFF = 300
MIN_CASOS_EVAL = 20
EARS = re.compile(r"\bdeber[áa]\b|\bshall\b", re.I)


@dataclass
class Resultado:
    descripcion: str
    ok: bool
    detalle: str = ""


Chequeo = Callable[[Proyecto, bool], list[Resultado]]


@dataclass(frozen=True)
class Gate:
    codigo: str
    fase: str
    manuales: tuple[str, ...]
    automaticos: tuple[Chequeo, ...]


def _doc(p: Proyecto, rel: str, nombre: str) -> Resultado:
    texto = p.leer(rel)
    if texto is None:
        return Resultado(f"{nombre} existe", False, f"falta {rel}")
    return Resultado(
        f"{nombre} completo",
        not es_plantilla(texto),
        "" if not es_plantilla(texto) else f"{rel} aún tiene la marca de plantilla",
    )


# ---------------------------------------------------------------- G1 Reconocer
def _g1(p: Proyecto, _ejecutar: bool) -> list[Resultado]:
    res = [_doc(p, "docs/product/brief.md", "Brief")]
    src = p.raiz / "src"
    culpables = []
    if src.is_dir():
        patron = re.compile(r"^\s*(from|import)\s+spikes\b", re.M)
        culpables = [
            f.relative_to(p.raiz).as_posix()
            for f in src.rglob("*.py")
            if patron.search(f.read_text(encoding="utf-8", errors="replace"))
        ]
    res.append(
        Resultado("nada de spikes/ se importa desde src/", not culpables, ", ".join(culpables))
    )
    return res


# ---------------------------------------------------------------- G2 Unificar
def _casos_eval(p: Proyecto) -> int:
    carpeta = p.raiz / "evals" / "datasets"
    n = 0
    for f in carpeta.glob("*.jsonl") if carpeta.is_dir() else []:
        for linea in f.read_text(encoding="utf-8").splitlines():
            if not linea.strip():
                continue
            try:
                caso = json.loads(linea)
            except json.JSONDecodeError:
                continue
            if "ejemplo" not in caso.get("tags", []):
                n += 1
    return n


def _g2(p: Proyecto, _ejecutar: bool) -> list[Resultado]:
    res = [_doc(p, "docs/product/prd.md", "PRD")]
    specs = documentos_numerados(p.raiz / "specs")
    res.append(Resultado("al menos una spec en specs/", bool(specs), "usa 'rumbo new spec'"))
    for s in specs:
        t = s.read_text(encoding="utf-8")
        rel = s.relative_to(p.raiz).as_posix()
        res.append(Resultado(f"{rel}: completa", not es_plantilla(t)))
        res.append(
            Resultado(f"{rel}: requisitos EARS", bool(EARS.search(seccion(t, "Requisitos"))))
        )
        res.append(
            Resultado(
                f"{rel}: criterios de aceptación", bool(seccion(t, "Criterios de aceptación"))
            )
        )
    if p.llm:
        n = _casos_eval(p)
        res.append(
            Resultado(
                f"≥{MIN_CASOS_EVAL} casos reales en evals/datasets/",
                n >= MIN_CASOS_EVAL,
                f"hay {n} (los tagueados 'ejemplo' no cuentan)",
            )
        )
        res.append(_doc(p, "docs/evals/plan.md", "Plan de evals"))
    return res


# ---------------------------------------------------------------- G3 Modelar
def _g3(p: Proyecto, _ejecutar: bool) -> list[Resultado]:
    adrs = documentos_numerados(p.raiz / "docs" / "adr")
    res = [Resultado("al menos un ADR", bool(adrs), "usa 'rumbo new adr'")]
    hay_workflow = False
    for a in adrs:
        t = a.read_text(encoding="utf-8")
        rel = a.relative_to(p.raiz).as_posix()
        res.append(Resultado(f"{rel}: completo", not es_plantilla(t)))
        res.append(
            Resultado(f"{rel}: tiene fitness function", bool(seccion(t, "Fitness function")))
        )
        if re.search(r"workflow", t, re.I) and re.search(r"agente", t, re.I):
            hay_workflow = True
    if p.llm:
        res.append(Resultado("ADR 'workflow vs. agente'", hay_workflow))
    res.append(_doc(p, "docs/security/threat-model.md", "Threat model"))
    tm = p.leer("docs/security/threat-model.md") or ""
    res.append(
        Resultado("threat model evalúa la trifecta letal", bool(seccion(tm, "Trifecta letal")))
    )
    res.append(_doc(p, "docs/architecture/c4.md", "C4 niveles 1–2"))
    c4 = p.leer("docs/architecture/c4.md") or ""
    res.append(Resultado("C4 con diagramas Mermaid", c4.count("```mermaid") >= 2))
    py = p.leer("pyproject.toml") or ""
    res.append(
        Resultado("contratos import-linter", py.count("[[tool.importlinter.contracts]]") >= 2)
    )
    ci = p.leer(".github/workflows/ci.yml") or ""
    res.append(Resultado("CI ejecuta make check", "make check" in ci))
    return res


# ---------------------------------------------------------------- G4 Build (por PR)
def _correr(cmd: list[str], raiz: Path) -> tuple[bool, str]:
    proc = subprocess.run(cmd, cwd=raiz, capture_output=True, text=True, check=False)
    salida = (proc.stdout + proc.stderr).strip().splitlines()
    return proc.returncode == 0, "\n".join(salida[-15:])


def _tamano_diff(raiz: Path) -> int | None:
    for base in ("origin/main", "origin/master", "main", "master"):
        mb = subprocess.run(
            ["git", "merge-base", "HEAD", base],
            cwd=raiz,
            capture_output=True,
            text=True,
            check=False,
        )
        if mb.returncode == 0:
            diff = subprocess.run(
                ["git", "diff", "--numstat", mb.stdout.strip()],
                cwd=raiz,
                capture_output=True,
                text=True,
                check=False,
            )
            total = 0
            for linea in diff.stdout.splitlines():
                partes = linea.split("\t")
                if len(partes) >= 2 and partes[0].isdigit() and partes[1].isdigit():
                    total += int(partes[0]) + int(partes[1])
            return total
    return None


def _g4(p: Proyecto, ejecutar: bool) -> list[Resultado]:
    res: list[Resultado] = []
    n = _tamano_diff(p.raiz)
    if n is not None:
        res.append(Resultado(f"diff pequeño (≤{MAX_DIFF} líneas)", n <= MAX_DIFF, f"{n} líneas"))
    if not ejecutar:
        return res
    sensor = p.raiz / "scripts" / "check_tests_not_weakened.py"
    ok, out = _correr([sys.executable, str(sensor)], p.raiz)
    res.append(Resultado("ningún test debilitado", ok, out))
    ok, out = _correr(["make", "check"], p.raiz)
    res.append(Resultado("make check en verde", ok, "" if ok else out))
    return res


# ---------------------------------------------------------------- G5 Operar
def _g5(p: Proyecto, _ejecutar: bool) -> list[Resultado]:
    res = [
        _doc(p, "docs/ops/runbook.md", "Runbook"),
        _doc(p, "docs/ops/slos.md", "SLOs"),
        Resultado("Dockerfile en infra/", (p.raiz / "infra" / "Dockerfile").exists()),
        Resultado(
            "actualización automática de dependencias",
            (p.raiz / ".github" / "dependabot.yml").exists() or (p.raiz / "renovate.json").exists(),
        ),
    ]
    return res


GATES: dict[str, Gate] = {
    "G1": Gate(
        "G1",
        "R — Reconocer",
        (
            "Brief escrito primero por el humano",
            "Problema validado con ≥5 usuarios o datos reales",
            "Métrica de éxito y criterio de abandono escritos",
            "Prototipos solo en spikes/",
        ),
        (_g1,),
    ),
    "G2": Gate(
        "G2",
        "U — Unificar",
        (
            "PRD con no-objetivos",
            "Specs EARS con criterios ejecutables, firmadas por el humano",
            "RNF con números (p95, costo por solicitud, disponibilidad)",
            "Cada requisito tiene un test o eval planeado",
            "Plan de evals y dataset de preguntas reales (si hay LLM)",
        ),
        (_g2,),
    ),
    "G3": Gate(
        "G3",
        "M — Modelar",
        (
            "ADR workflow vs. agente (si hay LLM)",
            "Contratos de import-linter en CI",
            "C4 niveles 1–2",
            "Threat model con trifecta y OWASP LLM/Agentic revisado por el humano",
            "Herramientas tipadas y de mínimo privilegio",
        ),
        (_g3,),
    ),
    "G4": Gate(
        "G4",
        "B — Build con ANCLA (por PR)",
        (
            "Test en rojo previo, revisado por el humano",
            "Plan aprobado antes de codificar",
            "CI verde (con evals smoke si hay LLM)",
            "Ningún test borrado o debilitado",
            "Diff pequeño; commits estructurales y de comportamiento separados",
            "Dependencias nuevas verificadas (rumbo verify-dep)",
            "Segundo modelo o sesión limpia revisó el diff",
            "Prueba de explicación: el humano explica el cambio en 3 frases sin leer el resumen",
        ),
        (_g4,),
    ),
    "G5": Gate(
        "G5",
        "O — Operar, Observar, Optimizar",
        (
            "Rollback ensayado",
            "SLOs y alertas activas",
            "Trazas GenAI con costo (si hay LLM)",
            "Límites de costo configurados",
            "Runbook con una entrada por modo de fallo",
            "Ritual semanal de revisión de trazas agendado",
            "Actualización automática de dependencias",
        ),
        (_g5,),
    ),
}


def evaluar(codigo: str, proyecto: Proyecto, ejecutar: bool = True) -> tuple[Gate, list[Resultado]]:
    gate = GATES[codigo.upper()]
    resultados: list[Resultado] = []
    for chequeo in gate.automaticos:
        resultados.extend(chequeo(proyecto, ejecutar))
    return gate, resultados


def informe(gate: Gate, resultados: list[Resultado]) -> tuple[str, bool]:
    ok = all(r.ok for r in resultados)
    lineas = [f"🛑 Gate {gate.codigo} · {gate.fase}", "", "Chequeos automáticos (sensores):"]
    for r in resultados:
        marca = "✅" if r.ok else "❌"
        lineas.append(f"  {marca} {r.descripcion}")
        if r.detalle and not r.ok:
            lineas += [f"       {d}" for d in r.detalle.splitlines()]
    lineas += ["", "Checklist humano (lo firma el humano, no el agente):"]
    lineas += [f"  [ ] {m}" for m in gate.manuales]
    lineas += [
        "",
        "Automáticos: " + ("en verde." if ok else "HAY FALLOS: el gate no puede pasar."),
    ]
    return "\n".join(lineas), ok
