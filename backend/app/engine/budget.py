"""Per-run spend tracking against ``RUN_COST_CAP_USD`` (plan §4.3, A4).

Actual spend is recorded only from provider-reported ``costDollars.total``.
Estimates are LABELLED estimates computed from the documented ``/search``
price range ($0.007–$0.015 per request, docs/p0-openapi-notes.md) — the
upper bound is used so an estimate is never below the actual spend in
mock runs. An estimate is never presented as a quote.
"""

from dataclasses import dataclass

ESTIMATED_SEARCH_COST_USD = 0.015
ESTIMATED_CELL_COST_USD = 0.015  # one /search + outputSchema per cell
ESTIMATED_IDENTITY_COST_USD = 2 * ESTIMATED_SEARCH_COST_USD  # 1–2 searches/row

_EPSILON = 1e-9


def estimate_run_cost(pending_identity_rows: int, cell_count: int) -> float:
    """Labelled estimate for a run: identity (≤2 searches per new row) plus
    one search per queued cell."""
    return (
        pending_identity_rows * ESTIMATED_IDENTITY_COST_USD + cell_count * ESTIMATED_CELL_COST_USD
    )


@dataclass
class Budget:
    """Spend accumulator for one run. ``cap=None`` means uncapped."""

    cap: float | None
    spent: float = 0.0

    @property
    def remaining(self) -> float | None:
        return None if self.cap is None else max(0.0, self.cap - self.spent)

    def affords(self, estimate: float) -> bool:
        """True when one more estimated unit cannot exceed the cap."""
        if self.cap is None:
            return True
        return self.spent + estimate <= self.cap + _EPSILON

    def record(self, cost: float | None) -> None:
        """Add provider-reported spend; ``None``/zero costs are ignored."""
        if cost:
            self.spent += cost
