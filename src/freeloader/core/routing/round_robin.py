# Round-robin selection: cycle through the registered providers in
# the order Router was given them. Each `pick(order)` call advances
# the index; the cycle wraps at the end of the order list.
#
# This is the simplest non-trivial selection policy. Phase 4 will
# add a quota-aware strategy that consumes the journal's rate-limit
# events; both implement the same shape (`pick(order) -> str`) so
# the Router doesn't need to know which one is in use.
from __future__ import annotations


class RoundRobinStrategy:
    """Stateful round-robin picker. Holds the cycle index across
    calls. The `order` argument is passed at pick-time rather than
    construct-time so the Router can change its provider pool
    without rebuilding the strategy."""

    def __init__(self) -> None:
        self._idx: int = 0

    def pick(self, order: list[str]) -> str:
        if not order:
            raise ValueError("pick() requires a non-empty provider order")
        provider = order[self._idx % len(order)]
        self._idx += 1
        return provider

    @property
    def cursor(self) -> int:
        """Read-only view of the internal cycle index. Tests use
        this to assert that pick() advances and bind() does not."""
        return self._idx
