# Quota-aware selection: pick the first non-pressured provider in
# the registered order, falling back to the first if all are
# pressured. PLAN principle #5 — quota is an event stream, not a
# counter — and this strategy turns the stream into routing.
#
# Pressure model:
#   - Per provider, per `rate_limit_type` (claude's "five_hour" /
#     "seven_day", inferred path's "inferred_window", future 4.2b's
#     "429"): remember the latest `status` seen.
#   - "Pressured" = ANY rate_limit_type's latest status == "exceeded".
#   - Pressure clears only when a later quota_signal arrives with
#     status="allowed" for the same rate_limit_type.
#
# No clock, no decay, no `resets_at` consultation — that would couple
# routing to wall time and break 4.5's replay determinism. The
# inferred path naturally decays as new windowed events arrive; the
# claude path clears on the next allowed observation.
from __future__ import annotations


class QuotaAwareStrategy:
    """Stateful per-provider pressure tracker. Same `pick(order)`
    shape as `RoundRobinStrategy` so the Router stays strategy-
    agnostic. Adds an `observe(event)` hook that the Router calls
    after every quota_signal write."""

    def __init__(self) -> None:
        # {provider → {rate_limit_type → latest_status}}
        self._latest_status: dict[str, dict[str, str]] = {}
        # Round-robin tiebreaker among NON-pressured providers — phase
        # 3 callers rely on first-fresh-conv goes-to-first-provider,
        # but once the pool grows past 1 a quota-aware strategy that
        # always picks the first non-pressured one would starve the
        # rest. Keep a cursor that advances on each pick and is used
        # to start the scan.
        self._cursor: int = 0

    def observe(self, event: dict) -> None:
        """Fold one quota_signal event into pressure state. Silently
        ignores events of other kinds so the Router can pipe its
        whole event stream through this method without filtering."""
        if event.get("kind") != "quota_signal":
            return
        provider = event.get("provider")
        rate_limit_type = event.get("rate_limit_type")
        status = event.get("status")
        if not provider or not rate_limit_type or status is None:
            # Malformed event — defensive, since 4.1/4.2a builders
            # always populate these. Skip rather than raise; a journal
            # write is forensic, breaking the router on a single
            # malformed record would be a worse failure mode than
            # ignoring it.
            return
        bucket = self._latest_status.setdefault(provider, {})
        bucket[rate_limit_type] = status

    def is_pressured(self, provider: str) -> bool:
        """True if any rate_limit_type's latest status for this
        provider is "exceeded". Public for tests and 4.5 replay
        introspection; not used by `pick()` directly (which inlines
        the check while scanning)."""
        bucket = self._latest_status.get(provider, {})
        return any(status == "exceeded" for status in bucket.values())

    def pick(self, order: list[str]) -> str:
        """Scan `order` starting from the cursor. Return the first
        non-pressured provider; if all are pressured, return the one
        at the cursor (deterministic fallback — no starvation, the
        user wants service even under universal pressure). Cursor
        advances either way so the next pick rotates."""
        if not order:
            raise ValueError("pick() requires a non-empty provider order")
        n = len(order)
        start = self._cursor % n
        # First pass: look for a non-pressured provider, starting at
        # the cursor and wrapping around.
        for offset in range(n):
            idx = (start + offset) % n
            candidate = order[idx]
            if not self.is_pressured(candidate):
                self._cursor = idx + 1
                return candidate
        # All pressured: pick the one at cursor and move on.
        chosen = order[start]
        self._cursor = start + 1
        return chosen

    @property
    def cursor(self) -> int:
        """Read-only view of the internal cycle index. Mirrors
        RoundRobinStrategy.cursor for tests that introspect router
        state via the same property."""
        return self._cursor
