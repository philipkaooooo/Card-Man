"""Turn one stored valuation into a plain call: what to do, at what price, and what could go wrong.

All arithmetic is Decimal in the valuation's home currency, using the exact cost model the ceiling
was computed with (economics.AcquisitionCosts / ExitCosts), so every number here can be checked
against the snapshot. Money is formatted only at the end through the `money` callable, which is
where the display currency is applied.

Definitions (B = price paid to the seller, G = resale price):
  all-in cost   C(B) = a*B + b                    (buying costs, from AcquisitionCosts.linear)
  net resale    N(G) = G*(1 - p) - f              (p = % selling costs, f = fixed selling costs)
  profit        N(G) - C(B);  return = profit / C(B)
  required      r + k*m                           (target return + monthly capital cost x months)
  pay up to     buy ceiling: largest B with return >= required at the conservative resale
  walk away     largest B with profit >= 0 at the conservative resale = (N(G_cons) - b) / a
  break-even    resale G at which profit = 0 for a given B = (C(B) + f) / (1 - p)
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import datetime
from decimal import ROUND_FLOOR, Decimal
from typing import Callable

from .economics import AcquisitionCosts, ExitCosts

SALE_WORD = {"MARKETPLACE_MARKED_SOLD": "sold", "ACCEPTED_OFFER": "accepted offer",
             "INDEPENDENT_TRANSACTION": "private sale"}

Money = Callable[..., str]   # money(amount, rnd="near"|"down"|"up")


@dataclass
class Outcome:
    label: str
    resale: Decimal
    profit: Decimal
    ret: Decimal | None


@dataclass
class Advice:
    call: str                       # BUY | CHECK | OFFER | SKIP | HOLD | NONE
    title: str
    because: list[str] = field(default_factory=list)
    todo: list[str] = field(default_factory=list)
    confidence: str = "—"
    confidence_why: list[str] = field(default_factory=list)
    flags: list[tuple[str, str]] = field(default_factory=list)       # (severity: high|medium|info, text)
    # numbers (home currency)
    pay_up_to: Decimal | None = None
    walk_away: Decimal | None = None
    all_in_at_ask: Decimal | None = None
    breakeven_resale: Decimal | None = None
    required: Decimal | None = None
    cushion_pct: Decimal | None = None      # how far resale can fall below the conservative exit before a loss
    at_ask: list[Outcome] = field(default_factory=list)        # conservative / base / upside
    per_sale: list[Outcome] = field(default_factory=list)      # each recent sale as the resale price
    downside: list[Outcome] = field(default_factory=list)
    wins: int = 0
    hits: int = 0
    sales_per_month: Decimal | None = None
    steps: list[tuple[str, str, str]] = field(default_factory=list)   # (step, formula, value) for the math card


def _pc(r: Decimal) -> str:
    v = float(r) * 100
    return (f"{v:.1f}%" if abs(v) < 10 else f"{v:.0f}%").replace("-", "−")


def _floor(v: Decimal) -> Decimal:
    return v.quantize(Decimal("1"), rounding=ROUND_FLOOR)


def advise(*, ci: dict | None, ask: Decimal | None, ceiling: Decimal | None, scen: dict,
           comps: list[dict], valued_at: datetime, gates: list[dict], identity_status: str | None,
           money: Money, gate_todo: dict[str, str]) -> Advice:
    """comps: [{"date": datetime, "home": Decimal, "type": str, "source": str, "synthetic": bool}]"""
    failed = [g for g in gates if not g["passed"]]
    adv = Advice(call="NONE", title="")
    cons = scen.get("conservative")

    # --- evidence quality ------------------------------------------------------------------
    n = len(comps)
    prices = [c["home"] for c in comps]
    reasons_low, reasons_med = [], []
    if n:
        newest = max(c["date"] for c in comps)
        age = (valued_at - newest).days
        mean = statistics.fmean(float(p) for p in prices)
        cv = statistics.pstdev(float(p) for p in prices) / mean if n > 1 and mean else 0.0
        in90 = [c for c in comps if (valued_at - c["date"]).days <= 90]
        adv.sales_per_month = Decimal(len(in90)) / 3 if in90 else Decimal(0)
        if n < 5:
            reasons_low.append(f"only {n} sale{'s' if n != 1 else ''}")
        elif n < 10:
            reasons_med.append(f"{n} sales")
        if any(c["synthetic"] for c in comps):
            reasons_low.append("the sales are synthetic test data")
        if age > 90:
            reasons_low.append(f"newest sale is {age} days old")
        elif age > 30:
            reasons_med.append(f"newest sale is {age} days old")
        if cv > 0.35:
            reasons_low.append(f"prices swing ±{cv * 100:.0f}% around the average")
        elif cv > 0.2:
            reasons_med.append(f"prices swing ±{cv * 100:.0f}% around the average")
        if len({c["source"] for c in comps}) == 1 and n >= 3:
            reasons_med.append("all sales from one source")
        adv.confidence = "Low" if reasons_low else ("Medium" if reasons_med else "High")
        adv.confidence_why = reasons_low + reasons_med or [f"{n} recent sales that agree with each other"]
        if cv > 0.2:
            adv.flags.append(("medium" if cv <= 0.35 else "high",
                              f"Prices are spread out (±{cv * 100:.0f}%). The low end matters more than the average."))
        if age > 30:
            adv.flags.append(("medium" if age <= 90 else "high",
                              f"The newest sale is {age} days old; the market may have moved."))
    else:
        adv.confidence, adv.confidence_why = "None", ["no usable sales"]

    # --- price math --------------------------------------------------------------------------
    if ci:
        acq, ex = AcquisitionCosts(**ci["acquisition"]), ExitCosts(**ci["exit"])
        a, b = acq.linear()
        p = ex.platform_fee_pct + ex.payment_fee_pct + ex.returns_reserve_pct + ex.payout_fx_spread_pct
        f = ex.platform_fee_fixed + ex.outbound_shipping
        req = ci["target_return"] + ci["capital_cost_monthly"] * ci["holding_months"]
        adv.required = req
        net = ex.net
        ret = lambda prof, cost: (prof / cost) if cost > 0 else None
        adv.pay_up_to = ceiling
        if cons is not None:
            wa = (net(cons) - b) / a
            adv.walk_away = _floor(wa) if wa > 0 else None
            # step-by-step math for the "pay up to" number
            mult = 1 + req
            adv.steps = [
                ("Conservative resale", "low end of the usable sales", money(cons)),
                ("minus selling costs", f"{p * 100:.2f}% + {money(f)} fixed", "− " + money(cons - net(cons))),
                ("= net from the resale", "", money(net(cons))),
                ("÷ target multiple", f"1 + {ci['target_return'] * 100:.0f}% target + "
                                      f"{ci['capital_cost_monthly'] * 100:.1f}%/mo × {ci['holding_months']} mo",
                 f"÷ {mult:.3f}"),
                ("= most you can spend all-in", "", money(net(cons) / mult)),
                ("minus fixed buying costs", "shipping in, insurance, fixed fees", "− " + money(b)),
                ("÷ price-linked buying costs", "premium, duty, proxy %, payment FX spread", f"÷ {a:.4f}"),
                ("= pay up to", "rounded down", money(ceiling, "down") if ceiling is not None else "—"),
            ]
        if ask is not None:
            cost = acq.all_in(ask)
            adv.all_in_at_ask = cost
            adv.breakeven_resale = (cost + f) / (1 - p) if p < 1 else None
            for k in ("conservative", "base", "upside"):
                g = scen.get(k)
                if g is not None:
                    adv.at_ask.append(Outcome(k, g, net(g) - cost, ret(net(g) - cost, cost)))
            for c in sorted(comps, key=lambda c: c["date"]):
                prof = net(c["home"]) - cost
                adv.per_sale.append(Outcome(c["date"].strftime("%b %d") + " · " + SALE_WORD.get(c["type"], c["type"].lower()),
                                            c["home"], prof, ret(prof, cost)))
            adv.wins = sum(o.profit > 0 for o in adv.per_sale)
            adv.hits = sum(o.ret is not None and o.ret >= req for o in adv.per_sale)
            if cons is not None:
                for label, mult_ in (("10% under the low end", Decimal("0.9")), ("20% under the low end", Decimal("0.8")),
                                     ("30% under the low end", Decimal("0.7"))):
                    g = cons * mult_
                    adv.downside.append(Outcome(label, g, net(g) - cost, ret(net(g) - cost, cost)))
                if adv.breakeven_resale is not None and cons > 0:
                    adv.cushion_pct = (cons - adv.breakeven_resale) / cons
        if ci["holding_months"] and adv.sales_per_month is not None and adv.sales_per_month > 0:
            months_between = 1 / adv.sales_per_month
            if months_between > ci["holding_months"]:
                adv.flags.append(("medium", f"About one sale every {months_between:.1f} months, but the math assumes "
                                            f"you sell within {ci['holding_months']}. Holding longer costs "
                                            f"{ci['capital_cost_monthly'] * 100:.1f}% a month."))
        if ex.returns_reserve_pct == 0:
            adv.flags.append(("info", "No returns reserve is set; a return or chargeback comes straight out of profit."))
        if acq.payment_fx_spread_pct and acq.inbound_shipping and not acq.shipping_fx_spread_pct:
            adv.flags.append(("info", "If you pay the shipping in a foreign currency, set shipping_fx_spread_pct in the "
                                      "listing costs; right now the FX spread is charged on the item price only."))

    # --- the call ------------------------------------------------------------------------------
    cons_s = money(cons) if cons is not None else "—"
    if identity_status not in (None, "RESOLVED"):
        adv.call, adv.title = "HOLD", "Hold off: it is not clear which exact printing this is."
        adv.because.append("Different printings of the same card sell for very different prices, so no price can be "
                           "trusted until the printing is pinned down.")
    elif ceiling is None:
        adv.call, adv.title = "HOLD", "Hold off: there are not enough sales to price it."
        adv.because.append(f"A buy price needs at least 3 recent sales of this exact printing; there are {n}.")
    elif ask is None:
        adv.call, adv.title = "NONE", f"Pay up to {money(ceiling, 'down')}."
        adv.because.append("No asking price is recorded for this listing.")
    else:
        wa = adv.walk_away
        cons_out = adv.at_ask[0] if adv.at_ask else None
        if wa is None or ask > wa:
            adv.call = "SKIP"
            adv.title = f"Skip it at {money(ask)}."
            adv.because.append(f"If it resells at the low end ({cons_s}) you lose "
                               f"{money(-cons_out.profit) if cons_out else '—'} after costs.")
            if wa is not None:
                adv.because.append(f"It only breaks even at {money(wa, 'down')} or less, and hits your target at {money(ceiling, 'down')} or less.")
        elif ask > ceiling:
            adv.call = "OFFER"
            adv.title = f"Make an offer: {money(ceiling, 'down')} or less."
            adv.because.append(f"At the ask ({money(ask)}) you make {money(cons_out.profit)} "
                               f"({_pc(cons_out.ret)}) if it resells at the low end, under your "
                               f"{_pc(adv.required)} target.")
            adv.because.append(f"At {money(ceiling, 'down')} you hit the target; above {money(wa, 'down')} you start losing money.")
        else:
            adv.call = "BUY" if not failed else "CHECK"
            adv.title = (f"Buy it. Pay up to {money(ceiling, 'down')}." if not failed
                         else f"Good price. Buy once the checks below are done; pay up to {money(ceiling, 'down')}.")
            adv.because.append(f"At {money(ask)} you make {money(cons_out.profit)} ({_pc(cons_out.ret)}) even if "
                               f"it resells at the low end of recent sales ({cons_s}).")
        if adv.breakeven_resale is not None and adv.cushion_pct is not None and adv.call in ("BUY", "CHECK", "OFFER"):
            if adv.cushion_pct > 0:
                adv.because.append(f"You only lose money if it resells under {money(adv.breakeven_resale, 'up')}, "
                                   f"{adv.cushion_pct * 100:.0f}% below the cheapest recent sale.")
        if adv.per_sale:
            adv.because.append(f"Against each of the last {len(adv.per_sale)} sales: profit in {adv.wins}, "
                               f"target hit in {adv.hits}.")
    for g in failed:
        if g["name"] == "ceiling":
            continue
        if g["name"] == "comps" and ceiling is None:
            adv.todo.append(gate_todo["comps"])
            continue
        adv.todo.append(gate_todo.get(g["name"], g["detail"]))
    if adv.cushion_pct is not None and adv.cushion_pct < Decimal("0.1") and adv.call in ("BUY", "CHECK", "OFFER"):
        adv.flags.insert(0, ("high", f"Thin cushion: a resale just {adv.cushion_pct * 100:.0f}% under the low end "
                                     "wipes out the profit."))
    if adv.confidence == "Low" and adv.call in ("BUY", "CHECK", "OFFER"):
        adv.flags.insert(0, ("high", "Low confidence: " + "; ".join(reasons_low) + ". Treat the numbers as a rough read."))
    return adv
