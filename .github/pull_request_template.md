<!-- The agent may draft the first section; the human ticks the checklist. -->
## What and why
Three sentences written by the human after reading the diff (explanation test).

## Checklist
- [ ] `make check` green; `make e2e` too if templates changed
- [ ] No test deleted or weakened (`uv run rumbo check-tests`); a human-authored commit says `[tests-change-approved]` if one had to change
- [ ] RUMBO.md, README.md and docs/method.md kept consistent (if templates or the method changed)
- [ ] CHANGELOG.md updated under [Unreleased]
- [ ] Structural and behavioral commits separated; Conventional Commits
- [ ] Reviewed with `make review` (guided tour)

## Risks
