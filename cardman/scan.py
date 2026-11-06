"""Triage one raw listing: what could it be, what is each possibility worth, and what did the seller miss.

This is the opportunity hunter in front of the deal page. It does not decide anything a human must
decide; it ranks what to look at and names the one piece of evidence that would settle the most money.

Valuation per candidate printing uses the store's clean sales (same rules as the deal page: exact
printing, sold evidence only, no lots, grade and condition matched, nothing after the decision time).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_FLOOR
from statistics import median

from .economics import AcquisitionCosts, CeilingInputs, ExitCosts, buy_ceiling, scenarios_from_comps
from .evidence import select_comps
from .fx import FxTable, StaleRateError
from .identity import Catalog, IdentityResult, resolve
from .listing_text import ParsedListing, parse_listing
from .models import IdentityStatus, Printing, Region

SCAN_VERSION = "scan-0.1.0"
MIN_SALES = 3
# Flags that mean the thing for sale is not a tradable genuine single in scope.
BLOCKING = {"proxy", "asian_english", "korean", "unopened_lottery", "graded_slab_only"}

DEFAULT_COSTS = {
    # Resale on a US marketplace, buying from a domestic seller. Every number is shown on the page.
    "target_return": "0.20", "holding_months": "2", "capital_cost_monthly": "0.01",
    "acquisition": {"payment_fx_spread_pct": "0.0", "inbound_shipping": "5"},
    "exit": {"platform_fee_pct": "0.1325", "outbound_shipping": "5", "returns_reserve_pct": "0.02"},
}


@dataclass
class CandidateValue:
    printing: Printing
    sales: int
    low: Decimal | None          # conservative resale (home currency)
    mid: Decimal | None
    newest: datetime | None
    per_month: float | None      # clean sales per month over the window they span
    claimed: bool                # matches what the seller says it is
    ceiling: Decimal | None      # most you can pay for this printing and still hit the target
    profit_at_ask: Decimal | None
    by_source: dict[str, Decimal] = field(default_factory=dict)   # median sale per marketplace
    skipped_fx: int = 0


@dataclass
class Finding:
    kind: str        # "upside" | "risk" | "mismatch" | "info"
    title: str
    detail: str
    money: Decimal | None = None


@dataclass
class ScanResult:
    parsed: ParsedListing
    identity: IdentityResult
    candidates: list[CandidateValue]
    home: str
    ask: Decimal | None
    ask_currency: str | None
    ask_home: Decimal | None
    all_in: Decimal | None
    verdict: str                 # LOOK | LOTTERY | PASS | NEED_DATA | BLOCKED
    headline: str
    findings: list[Finding]
    worst: Decimal | None        # profit at the ask if it is the least valuable plausible printing
    best: Decimal | None
    expected: Decimal | None     # equal weight across plausible printings that have prices
    swing: Decimal | None        # best - worst: what settling the identity is worth
    next_evidence: list[str]
    priority: Decimal
    costs: dict
    scanned_at: datetime
    version: str = SCAN_VERSION


def _d(x) -> Decimal:
    return x if isinstance(x, Decimal) else Decimal(str(x))


def ceiling_inputs(exit_gross: Decimal, costs: dict) -> CeilingInputs:
    return CeilingInputs(exit_gross, AcquisitionCosts(**{k: _d(v) for k, v in costs.get("acquisition", {}).items()}),
                         ExitCosts(**{k: _d(v) for k, v in costs.get("exit", {}).items()}), _d(costs["target_return"]),
                         _d(costs.get("holding_months", "0")), _d(costs.get("capital_cost_monthly", "0")))


def plausible_printings(parsed: ParsedListing, ident: IdentityResult, catalog: Catalog) -> list[Printing]:
    """Printings this listing could be, before photos. Wider than the resolver on purpose."""
    if ident.candidates:
        return list(ident.candidates)
    fams, _ = catalog.family_matches(parsed.title)
    out = [p for f in sorted(fams) for p in catalog.by_family.get(f, [])]
    code = parsed.signals.seller_set_code
    if code and any(p.set_code and p.set_code.upper() == code for p in out):
        return [p for p in out if p.set_code and p.set_code.upper() == code]
    # Without a code the language narrows it: Japanese text means an OCG print is likely, not certain.
    langs = set(parsed.language_claims) or ({parsed.language_hint} if parsed.language_hint == "ja" else set())
    want = {r for l in langs for r in ({"ja": [Region.JP_OCG], "en": [Region.EN_TCG]}[l])}
    narrowed = [p for p in out if p.region in want] if want else out
    return [p for p in (narrowed or out) if p.region != Region.ASIAN_EN_OCG_QUARANTINE]


def _value(store, p: Printing, parsed: ParsedListing, fx: FxTable, home: str, now: datetime, costs: dict,
           ask_home: Decimal | None, include_synthetic: bool) -> CandidateValue:
    pool = store.observations_for(p.id, as_of=now)
    comps, _ = select_comps(pool, p.id, as_of=now, include_synthetic=include_synthetic,
                            grade_company=parsed.grade_company, grade=parsed.grade)
    prices, skipped, src = [], 0, {}
    for c in comps:
        try:
            v = fx.convert(c.original_amount, c.currency, home, c.event_time)
        except StaleRateError:
            skipped += 1
            continue
        prices.append(v)
        src.setdefault(c.source, []).append(v)
    sc = scenarios_from_comps(prices, min_comps=MIN_SALES)
    newest = comps[0].event_time if comps else None
    per_month = None
    if len(comps) >= 2:
        span = (comps[0].event_time - comps[-1].event_time).days or 1
        per_month = round((len(comps) - 1) * 30 / span, 1)
    ceiling = profit = None
    if sc.conservative is not None:
        inp = ceiling_inputs(sc.conservative, costs)
        ceiling = buy_ceiling(inp).buy_ceiling
        if ask_home is not None:
            profit = inp.exit.net(sc.conservative) - inp.acquisition.all_in(ask_home)
    claimed = bool(parsed.rarity_claims) and p.rarity in parsed.rarity_claims
    return CandidateValue(p, len(prices), sc.conservative, sc.base, newest, per_month, claimed, ceiling, profit,
                          {k: Decimal(median(v)) for k, v in src.items()}, skipped)


def _money(v: Decimal | None, cur: str) -> str:
    if v is None:
        return "—"
    from .charts import fmt
    return fmt(v.quantize(Decimal("1"), rounding=ROUND_FLOOR) if abs(v) >= 100 else v, cur)


def scan(*, title: str, body: str = "", ask: Decimal | None, ask_currency: str | None, store, catalog: Catalog,
         now: datetime, home: str = "USD", costs: dict | None = None, fx: FxTable | None = None,
         include_synthetic: bool = False) -> ScanResult:
    costs = costs or DEFAULT_COSTS
    fx = fx or FxTable(store.fx_rates(), max_age=timedelta(days=7))
    parsed = parse_listing(title, body)
    ident = resolve(parsed.signals, catalog)
    findings: list[Finding] = []
    ask_home = all_in = None
    if ask is not None and ask_currency:
        try:
            ask_home = fx.convert(ask, ask_currency.upper(), home, now)
        except StaleRateError as x:
            findings.append(Finding("risk", "No exchange rate for the ask", f"{x}. Add a rate to compare prices."))
    plaus = plausible_printings(parsed, ident, catalog)
    cands = [_value(store, p, parsed, fx, home, now, costs, ask_home, include_synthetic) for p in plaus]
    cands.sort(key=lambda c: (c.low is None, -(c.low or 0)))
    if ask_home is not None:
        all_in = ceiling_inputs(Decimal(0), costs).acquisition.all_in(ask_home)

    codes = set(parsed.flag_codes)
    for f in parsed.flags:
        kind = "risk" if f.code in BLOCKING | {"stock_photo", "no_returns", "damage_hint", "language_conflict",
                                               "cert_as_code", "graded_unclear", "reprint_hint"} else "info"
        if f.code == "collection":
            kind = "upside"
        findings.append(Finding(kind, f.meaning.split(":")[0], f"{f.meaning} (listing says \"{f.text}\")"))

    # -- misidentification: what the seller says vs what was ever printed -----------------------------
    code = parsed.signals.seller_set_code
    on_code = [p for p in plaus if code and p.set_code and p.set_code.upper() == code]
    priced = [c for c in cands if c.low is not None]
    claim = parsed.signals.seller_rarity
    if code and claim and on_code and not any(p.rarity == claim for p in on_code):
        printed_as = sorted({p.rarity for p in on_code})
        findings.append(Finding("mismatch", f"{code} was never printed as {claim}",
                                f"The catalog lists {code} only as {', '.join(printed_as)}. Either the rarity or the "
                                "code is wrong, or the card is not genuine. Ask for a photo of the code and an angled "
                                "photo of the foil."))
    if code and claim and len({p.rarity for p in on_code}) > 1:
        claimed_v = [c for c in priced if c.printing.rarity == claim]
        others = [c for c in priced if c.printing.rarity != claim and c.printing.set_code
                  and c.printing.set_code.upper() == code]
        if claimed_v and others:
            hi = max(others, key=lambda c: c.low)
            lo_claim = min(c.low for c in claimed_v)
            if hi.low > lo_claim * Decimal("1.3"):
                findings.append(Finding("upside", f"Same code also exists as {hi.printing.rarity}",
                                        f"{code} was printed as {claim} and as {hi.printing.rarity}. The seller says "
                                        f"{claim} (low-end resale {_money(lo_claim, home)}); the {hi.printing.rarity} "
                                        f"resells from {_money(hi.low, home)}. Rarities get mixed up; check the foil "
                                        "on the name and card number in the photos.", hi.low - lo_claim))
    if not code and len(priced) >= 2:
        lo, hi = min(c.low for c in priced), max(c.low for c in priced)
        if hi > lo * Decimal("1.5"):
            findings.append(Finding("mismatch", "Title does not say which print",
                                    f"{len(plaus)} printings match this name; the priced ones resell from "
                                    f"{_money(lo, home)} to {_money(hi, home)}. The set code on the card decides which.",
                                    hi - lo))
    if parsed.edition_claim and any(p.region == Region.EN_TCG and p.edition is None for p in plaus):
        findings.append(Finding("info", f"Edition claim ({parsed.edition_claim}) is unverified",
                                "The identity source has no edition data for these English prints; only a photo of "
                                "the edition stamp settles it."))

    # -- price signals ---------------------------------------------------------------------------
    as_described = [c for c in priced if c.claimed] or priced
    if ask_home is not None and as_described:
        floor = min(c.low for c in as_described)
        if ask_home < floor * Decimal("0.5"):
            findings.append(Finding("risk", "Far under every recent sale",
                                    f"The ask is {int((1 - ask_home / floor) * 100)}% under the cheapest recent sale of "
                                    "what it is described as. That happens with misdescribed cards, but also with "
                                    "fakes, damage and scams. Verify before getting excited."))
    for c in priced:
        if len(c.by_source) >= 2:
            (s_lo, v_lo), (s_hi, v_hi) = min(c.by_source.items(), key=lambda kv: kv[1]), max(c.by_source.items(), key=lambda kv: kv[1])
            if v_hi > v_lo * Decimal("1.25"):
                findings.append(Finding("upside", f"Price gap between markets ({c.printing.set_code or c.printing.product_name})",
                                        f"Median sale {_money(v_lo, home)} on {s_lo} vs {_money(v_hi, home)} on {s_hi}. "
                                        "Buy where it is cheap only if the gap survives fees and shipping both ways.",
                                        v_hi - v_lo))
    for c in priced:
        if c.newest and now - c.newest > timedelta(days=90):
            findings.append(Finding("risk", "Newest sale is old",
                                    f"{c.printing.set_code or c.printing.product_name}: last clean sale "
                                    f"{(now - c.newest).days} days ago. The price may have moved."))
            break

    # -- verdict ---------------------------------------------------------------------------------
    profits = [c.profit_at_ask for c in priced if c.profit_at_ask is not None]
    worst_pool = [c.profit_at_ask for c in as_described if c.profit_at_ask is not None]
    worst = min(worst_pool) if worst_pool else None
    best = max(profits) if profits else None
    expected = (sum(profits) / len(profits)) if profits else None
    swing = (best - worst) if best is not None and worst is not None else None
    target_profit = (_d(costs["target_return"]) * all_in) if all_in is not None else None
    best_c = max((c for c in priced if c.profit_at_ask is not None), key=lambda c: c.profit_at_ask, default=None)

    if codes & BLOCKING:
        verdict, headline = "BLOCKED", "Not a genuine in-scope single: skip it."
    elif parsed.is_lot and parsed.quantity > 1:
        verdict, headline = "NEED_DATA", (f"A lot of {parsed.quantity}: list the cards, then scan the valuable ones "
                                          "one by one.")
    elif not plaus:
        verdict, headline = "NEED_DATA", "No card in the catalog matches this title."
    elif not priced:
        verdict, headline = "NEED_DATA", (f"No printing it could be has {MIN_SALES}+ clean sales yet: import sales "
                                          "before pricing it.")
    elif ask_home is None:
        verdict, headline = "NEED_DATA", "No ask price: values below are what each print resells for."
    elif worst is not None and target_profit is not None and worst >= target_profit:
        verdict, headline = "LOOK", (f"Worth a close look: even as the cheapest plausible print you make "
                                     f"{_money(worst, home)} at the ask.")
    elif best is not None and target_profit is not None and best >= target_profit:
        verdict = "LOTTERY"
        headline = (f"Only pays if it is the {best_c.printing.rarity} {best_c.printing.set_code or ''}".rstrip()
                    + f" ({_money(best, home)} profit). As described it "
                    + ("loses money." if worst is not None and worst < 0 else "misses the target.")
                    + " Get the photo below before anything else.")
    else:
        verdict, headline = "PASS", "Pass: no print it could be resells for enough at this ask."

    needed = list(ident.evidence_needed)
    if verdict in ("LOOK", "LOTTERY") and "stock_photo" in codes:
        needed.insert(0, "photos of the actual card (front, back, corners)")
    if verdict in ("LOOK", "LOTTERY") and not needed:
        needed = ["front and back photos in good light", "close-up of the set code and edition area"]

    # Priority for the research queue: money at stake now, plus what one photo would settle.
    pr = Decimal(0)
    if verdict in ("LOOK", "LOTTERY"):
        pr = max(expected or Decimal(0), Decimal(0)) + max(swing or Decimal(0), Decimal(0)) / 4
    elif verdict == "NEED_DATA" and parsed.is_collection:
        pr = Decimal(1)
    return ScanResult(parsed, ident, cands, home, ask, ask_currency, ask_home, all_in, verdict, headline, findings,
                      worst, best, expected, swing, list(dict.fromkeys(needed)), pr.quantize(Decimal("0.01")), costs, now)
