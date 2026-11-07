"""Example property-based test with Hypothesis.

Use Hypothesis for domain invariants (e.g. "the union of date blocks covers exactly the
range, with no overlap"). This example checks a trivial property; replace it.
"""

from hypothesis import given
from hypothesis import strategies as st


@given(st.lists(st.integers()))
def test_sorting_is_idempotent(xs: list[int]) -> None:
    once = sorted(xs)
    assert sorted(once) == once
