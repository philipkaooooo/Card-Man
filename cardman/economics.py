"""Scenario economics and conservative buy ceiling (CLAUDE.md section 5).

Everything is in the trader's home currency (TWD by default) after timestamped FX.
Costs are labelled known / estimated / reserve so nothing is double counted.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from decimal import Decimal, ROUND_FLOOR
from statistics import median

CALC_VERSION = "econ-0.1.0"
D0 = Decimal("0")


@dataclass(frozen=True)
class AcquisitionCosts:
    """Costs of buying one item at price B (all home currency, per item)."""
    buyer_premium_pct: Decimal = D0      # auction house premium, % of hammer
    proxy_fee_pct: Decimal = D0          # proxy/buyer service, % of item
    proxy_fee_fixed: Decimal = D0        # known
    payment_fx_spread_pct: Decimal = D0  # card/PayPal FX spread on item price
    inbound_shipping: Decimal = D0       # this item's share of combined shipping
    import_duty_pct: Decimal = D0        # on (item + premium + shipping)
    insurance: Decimal = D0
    grading: Decimal = D0                # fee + shipping both ways, if grading is part of the plan
    shipping_fx_spread_pct: Decimal = D0  # FX spread on inbound shipping when it is paid in a foreign currency

    def linear(self) -> tuple[Decimal, Decimal]:
        """All-in cost = a * B + b."""
        # Everything paid to the seller and proxy abroad (hammer, premium, proxy fees) goes through the card's FX spread;
        # duty is paid at home, so it does not.
        fx = self.payment_fx_spread_pct
        a = (1 + self.buyer_premium_pct) * (1 + self.import_duty_pct) + self.proxy_fee_pct + fx * (1 + self.buyer_premium_pct + self.proxy_fee_pct)
        b = (self.proxy_fee_fixed * (1 + fx) + self.inbound_shipping * (1 + self.import_duty_pct + self.shipping_fx_spread_pct)
             + self.insurance + self.grading)
        return a, b

    def all_in(self, price: Decimal) -> Decimal:
        a, b = self.linear()
        return a * price + b


@dataclass(frozen=True)
class ExitCosts:
    platform_fee_pct: Decimal = D0
    platform_fee_fixed: Decimal = D0
    payment_fee_pct: Decimal = D0
    outbound_shipping: Decimal = D0
    returns_reserve_pct: Decimal = D0    # reserve, not a known cost
    payout_fx_spread_pct: Decimal = D0

    def net(self, gross: Decimal) -> Decimal:
        pct = self.platform_fee_pct + self.payment_fee_pct + self.returns_reserve_pct + self.payout_fx_spread_pct
        return gross * (1 - pct) - self.platform_fee_fixed - self.outbound_shipping


@dataclass(frozen=True)
class ExitScenarios:
    conservative: Decimal | None
    base: Decimal | None
    upside: Decimal | None
    n_comps: int
    method: str
    abstain_reason: str | None = None


def scenarios_from_comps(prices: list[Decimal], *, min_comps: int = 3) -> ExitScenarios:
    """Sparse data widens the range or abstains. No interpolation beyond observed sales."""
    n = len(prices)
    if n < min_comps:
        return ExitScenarios(None, None, None, n, "abstain", f"only {n} verifiable sale(s); need {min_comps}")
    s = sorted(prices)
    if n < 6:
        # Few comps: conservative = lowest observed sale, upside = median (not the max).
        return ExitScenarios(s[0], Decimal(median(s)), Decimal(median(s)), n, "sparse:min/median")
    q = lambda p: s[int((n - 1) * p)]
    return ExitScenarios(q(Decimal("0.2")), Decimal(median(s)), q(Decimal("0.75")), n, "quantile:p20/p50/p75")


@dataclass(frozen=True)
class CeilingInputs:
    exit_gross: Decimal
    acquisition: AcquisitionCosts
    exit: ExitCosts
    target_return: Decimal             # on all-in deployed capital
    holding_months: Decimal = D0
    capital_cost_monthly: Decimal = D0


@dataclass
class CeilingResult:
    buy_ceiling: Decimal | None
    net_exit: Decimal
    required_multiple: Decimal
    breakdown: dict = field(default_factory=dict)
    calc_version: str = CALC_VERSION


def buy_ceiling(inp: CeilingInputs) -> CeilingResult:
    """Largest item price B with  net_exit - C(B) >= (r + k*m) * C(B),  C(B) = a*B + b."""
    net = inp.exit.net(inp.exit_gross)
    mult = 1 + inp.target_return + inp.capital_cost_monthly * inp.holding_months
    a, b = inp.acquisition.linear()
    raw = (net / mult - b) / a
    ceiling = raw.quantize(Decimal("1"), rounding=ROUND_FLOOR) if raw > 0 else None
    return CeilingResult(ceiling, net, mult, {
        "exit_gross": inp.exit_gross, "net_exit": net, "acq_a": a, "acq_b": b,
        "max_all_in_cost": net / mult, "target_return": inp.target_return,
        "holding_months": inp.holding_months,
    })


def profit_at(price: Decimal, inp: CeilingInputs) -> Decimal:
    return inp.exit.net(inp.exit_gross) - inp.acquisition.all_in(price)


def sensitivities(inp: CeilingInputs) -> dict[str, Decimal | None]:
    """Marginal effect on the ceiling of common bad surprises."""
    from dataclasses import replace
    base = buy_ceiling(inp).buy_ceiling
    cases = {
        "exit_minus_10pct": replace(inp, exit_gross=inp.exit_gross * Decimal("0.9")),
        "lost_combined_shipping_x2": replace(inp, acquisition=replace(inp.acquisition, inbound_shipping=inp.acquisition.inbound_shipping * 2)),
        "platform_fee_plus_3pt": replace(inp, exit=replace(inp.exit, platform_fee_pct=inp.exit.platform_fee_pct + Decimal("0.03"))),
        "holding_plus_3_months": replace(inp, holding_months=inp.holding_months + 3),
    }
    out = {"base": base}
    for k, v in cases.items():
        c = buy_ceiling(v).buy_ceiling
        out[k] = None if c is None or base is None else c - base
    return out
