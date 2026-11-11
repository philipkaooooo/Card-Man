"""Grading economics: is sending this raw card in worth it, given what you have actually seen come back?

Grade probabilities come from your own recorded results (ledger) shrunk toward a prior YOU supply. With neither
there is no answer: public population reports are not your submission quality and are not assumed here.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_FLOOR


@dataclass(frozen=True)
class GradingResult:
    expected_profit: float
    p_loss: float
    expected_value: float
    probabilities: dict[str, float]
    max_raw_buy: Decimal | None
    breakeven_top_probability: float | None
    n_own_results: int
    basis: str


def distribution(own: dict[str, int], prior: dict[str, float] | None, prior_strength: float = 5.0) -> tuple[dict[str, float], str] | None:
    n = sum(own.values())
    if n == 0 and not prior:
        return None
    grades = set(own) | set(prior or {})
    weight = {g: own.get(g, 0) + ((prior or {}).get(g, 0.0) * prior_strength) for g in grades}
    t = sum(weight.values())
    if t <= 0:
        return None
    basis = f"{n} of your own results" + (f" + your prior (worth {prior_strength:g} results)" if prior else "")
    return {g: w / t for g, w in weight.items()}, basis


def evaluate(*, raw_all_in: float, grading_cost: float, net_by_grade: dict[str, float], own_results: dict[str, int],
             prior: dict[str, float] | None = None, raw_price_to_acq: tuple[float, float] = (1.0, 0.0),
             target_return: float = 0.2) -> GradingResult | None:
    """`net_by_grade`: net resale per grade after exit costs, from graded sales only. Grades with no price count as 0
    only if you have no sale of them, so missing a price is always the cautious side."""
    d = distribution(own_results, prior)
    if d is None or not net_by_grade:
        return None
    probs, basis = d
    cost = raw_all_in + grading_cost
    ev = sum(p * net_by_grade.get(g, 0.0) for g, p in probs.items())
    p_loss = sum(p for g, p in probs.items() if net_by_grade.get(g, 0.0) < cost)
    a, b = raw_price_to_acq
    top = max(net_by_grade, key=net_by_grade.get)
    rest = {g: p for g, p in probs.items() if g != top}
    rest_total = sum(rest.values())
    rest_ev = (sum(p * net_by_grade.get(g, 0.0) for g, p in rest.items()) / rest_total) if rest_total else 0.0
    gap = net_by_grade[top] - rest_ev
    breakeven = ((cost - rest_ev) / gap) if gap > 0 else None
    raw = (ev / (1 + target_return) - grading_cost - b) / a
    max_raw = Decimal(str(round(raw, 2))).quantize(Decimal("1"), rounding=ROUND_FLOOR) if raw > 0 else None
    return GradingResult(ev - cost, p_loss, ev, probs, max_raw,
                         min(1.0, max(0.0, breakeven)) if breakeven is not None else None, sum(own_results.values()), basis)
