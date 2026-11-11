"""Turn one analyzed listing into an opportunity, or decide it is not worth anyone's time.

Scenarios are the printings the listing could be. Value of information is computed from them: if learning which printing
it is could change the decision by more than the check costs, the check goes in the queue. Otherwise the listing is
shelved until its inputs change.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from datetime import datetime
from decimal import Decimal

from . import grading, pricing
from .economics import AcquisitionCosts, ExitCosts
from .evidence import select_comps
from .fx import StaleRateError
from .intel import BLOCK, ListingAnalysis, lot_contents
from .market_sources import RawListing
from .models import IdentityStatus, MarketObservation
from .verify import assess, filter_comps

MAX_SCENARIOS = 40
UNDERPRICED_SHARE = 0.8
MIN_PRICED_SHARE = 0.5        # below this, a priced minority of the possibilities does not speak for the listing
COMPETING_WINDOW_DAYS = 3      # share of identity probability that must be under max buy to call a listing underpriced
COLLECTION_CUES = ("引退", "コレクション", "バインダー", "binder", "collection", "retired", "attic", "estate")
OBSERVE, SPECTATE, QUALIFIED = "OBSERVE", "SPECTATE", "QUALIFIED"
OPEN, SHELVED, REJECTED, ESCALATED = "OPEN", "SHELVED", "REJECTED", "ESCALATED"


@dataclass
class Valuation:
    printing_id: str
    sales: list[pricing.Sale]
    comps: list[MarketObservation]
    estimate: pricing.PriceEstimate | None
    competing: int = 0
    competing_ids: frozenset = frozenset()


class PriceBook:
    """Per-tick view of what each printing is worth. Sold evidence only; FX failures abstain."""

    def __init__(self, store, fx_store, profile: dict, now: datetime, bias: float = 1.0, reference=None):
        self.store, self.fx, self.p, self.now, self.bias, self.reference = store, fx_store, profile, now, bias, reference
        self.home = profile["home_currency"]
        self.cache: dict[tuple, Valuation] = {}
        self.notes: list[str] = []

    def to_home(self, amount: Decimal, currency: str, at: datetime) -> Decimal:
        if currency == self.home:
            return amount
        table = self.fx.table([(currency, self.home)], [at])
        return table.convert(amount, currency, self.home, at)

    def value(self, printing_id: str, grade_company: str | None = None, grade: str | None = None) -> Valuation:
        k = (printing_id, grade_company, grade)
        if k in self.cache:
            return self.cache[k]
        pool = self.store.observations_for(printing_id, as_of=self.now)
        comps, _ = select_comps(pool, printing_id, grade_company=grade_company, grade=grade, as_of=self.now)
        comps, _ = filter_comps(comps, pool, self.now)
        trust = assess(pool, self.now)
        sales = []
        for c in comps:
            try:
                sales.append(pricing.Sale(c.event_time, float(self.to_home(c.original_amount, c.currency, c.event_time)),
                                          trust[c.id].trust if c.id in trust else 1.0))
            except StaleRateError as x:
                self.notes.append(f"no FX for {c.id}: {x}")
        est = pricing.estimate(sales, self.now, bias=self.bias)
        ids = self._competing(pool, est) if est else frozenset()
        v = Valuation(printing_id, sales, comps, est, len(ids), ids)
        self.cache[k] = v
        return v


    def _competing(self, pool: list[MarketObservation], est: pricing.PriceEstimate) -> frozenset:
        """Live asks for exactly this printing, seen in the last few days, at or under the price we would resell at.
        Buyers take the cheapest copy first, so each of these sells before ours does (the queue in months_to_sell)."""
        from .models import EvidenceType
        out = set()
        for o in pool:
            if (o.evidence_type == EvidenceType.ACTIVE_ASKING and len(o.printing_candidates) == 1 and o.listing_id
                    and (self.now - o.capture_time).days <= COMPETING_WINDOW_DAYS and not o.synthetic_fixture):
                try:
                    if float(self.to_home(o.original_amount, o.currency, o.capture_time)) <= est.mid:
                        out.add(o.listing_id)
                except StaleRateError:
                    pass
        return frozenset(out)


@dataclass
class Scenario:
    printing_id: str
    label: str
    weight: float
    basis: str                 # "sales" | "reference" | "none"
    n_sales: int = 0
    resale_low: float | None = None
    resale_mid: float | None = None
    profit_low: float | None = None
    profit_mid: float | None = None
    max_buy: float | None = None
    p_loss: float | None = None
    risk_adjusted: float | None = None
    months_to_sell: float | None = None


@dataclass
class ResearchAction:
    what: str
    minutes: float
    cost_home: float
    automatic: bool = False


@dataclass
class Opportunity:
    listing_key: str
    title: str
    url: str
    market: str
    stage: str
    status: str
    kinds: list[str]
    ask_home: float | None
    all_in_home: float | None
    scenarios: list[Scenario]
    expected_profit: float | None
    best_profit: float | None
    evpi: float
    priority: float
    priced_share: float
    findings: list[dict]
    research: list[ResearchAction]
    reasons: list[str]
    printing_id: str | None = None
    ends_at: str | None = None
    lot: list[dict] = field(default_factory=list)
    grading: dict | None = None
    confirm_codes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def costs_for(profile: dict, market: str, shipping_home: Decimal = Decimal(0)) -> tuple[AcquisitionCosts, ExitCosts]:
    route = dict(profile["routes"].get(market) or profile["routes"]["INTL"])
    acq = AcquisitionCosts(**{k: Decimal(str(v)) for k, v in route.items()})
    if shipping_home:
        acq = AcquisitionCosts(**{**acq.__dict__, "inbound_shipping": acq.inbound_shipping + shipping_home})
    return acq, ExitCosts(**{k: Decimal(str(v)) for k, v in profile["exit"].items()})


def _label(p) -> str:
    bits = [p.set_code or "no code", p.rarity]
    if p.edition:
        bits.append(p.edition)
    if p.product_name:
        bits.append(p.product_name)
    return " · ".join(bits)


def evaluate_listing(raw: RawListing, an: ListingAnalysis, book: PriceBook, catalog, ledger=None, now: datetime | None = None) -> Opportunity:
    now = now or book.now
    P = book.p
    findings = [asdict(f) for f in an.findings]
    reasons: list[str] = []
    kinds: list[str] = []

    def result(stage, status, **kw):
        base = dict(listing_key=raw.key, title=raw.title, url=raw.url, market=raw.market, stage=stage, status=status, kinds=kinds, ask_home=None,
                    all_in_home=None, scenarios=[], expected_profit=None, best_profit=None, evpi=0.0, priority=0.0, priced_share=0.0,
                    findings=findings, research=[], reasons=reasons, ends_at=raw.ends_at.isoformat() if raw.ends_at else None)
        base.update(kw)
        return Opportunity(**base)

    if an.blocked:
        reasons += [f.text for f in an.findings if f.severity == BLOCK]
        return result(OBSERVE, REJECTED)
    if raw.kind in ("sold", "accepted_offer", "transaction", "unsold"):
        reasons.append("A completed sale is evidence, not something to buy.")
        return result(OBSERVE, SHELVED)

    try:
        ask_home = book.to_home(raw.price, raw.currency, now)
        ship_home = book.to_home(raw.shipping, raw.currency, now) if raw.shipping else Decimal(0)
    except StaleRateError as x:
        reasons.append(f"Cannot price: {x}")
        return result(OBSERVE, OPEN, research=[ResearchAction("Update FX rates", 1, 0.0, True)])
    acq, ex = costs_for(P, raw.market, ship_home)
    all_in = float(acq.all_in(ask_home))
    tgt, cap = Decimal(str(P["target_return"])), Decimal(str(P["capital_cost_monthly"]))
    rate = float(P.get("research_value_per_hour", 600)) / 60

    # -- scenarios ------------------------------------------------------------------------------------------------
    from .identity import edition_variants
    by_id, weights = {}, {}
    base_w = an.weights or {}
    for c in an.identity.candidates:
        variants = edition_variants(c, catalog) if catalog is not None else [c]
        for v in variants:
            by_id[v.id] = v
            # The seller's edition claim tilts the weights but is not proof: 2/3 to the claimed edition.
            claimed = [e for e in getattr(an.parsed, "editions", ()) if e in {x.edition for x in variants}]
            if len(variants) > 1 and len(claimed) == 1:
                share = 2 / 3 if v.edition == claimed[0] else (1 / 3) / (len(variants) - 1)
            else:
                share = 1 / len(variants)
            weights[v.id] = base_w.get(c.id, 1 / max(1, len(an.identity.candidates))) * share
    ranked = sorted(by_id, key=lambda i: -weights.get(i, 0))[:MAX_SCENARIOS]
    all_w = sum(weights.values()) or 1.0
    low_all = f"{raw.title} {raw.description}".lower()
    graded = bool(raw.grade_company or getattr(an.parsed, "grade_company", None))
    japanese = (getattr(an.parsed, "language", None) == "ja" or raw.market == "JP" or "japan" in low_all
                or any("\u3040" <= ch <= "\u30ff" for ch in raw.title))
    scenarios: list[Scenario] = []
    for pid in ranked:
        p = by_id[pid]
        v = book.value(pid, raw.grade_company, raw.grade)
        sc = Scenario(pid, _label(p), weights.get(pid, 1 / max(1, len(by_id))), "none", n_sales=len(v.sales))
        if v.estimate:
            rv = pricing.risk_view(v.sales, v.estimate, ask_home, acq, ex, target_return=tgt, capital_cost_monthly=cap,
                                   # the listing being valued is the copy we would buy, not a competitor
                                   competing_listings=len(v.competing_ids - {raw.listing_id}), confidence=float(P.get("confidence", 0.8)),
                                   as_of=book.now)
            sc.basis, sc.resale_low, sc.resale_mid = "sales", v.estimate.low, v.estimate.mid
            # Money tied up until the card sells is a cost, the same one the max-buy price already charges.
            carry = all_in * float(cap) * rv.months_held
            sc.profit_low = float(ex.net(Decimal(str(round(v.estimate.low, 2))))) - all_in - carry
            sc.profit_mid = float(ex.net(Decimal(str(round(v.estimate.mid, 2))))) - all_in - carry
            sc.max_buy, sc.p_loss, sc.risk_adjusted, sc.months_to_sell = (float(rv.max_buy) if rv.max_buy else None), rv.p_loss, rv.risk_adjusted_profit, rv.months_held
        elif (book.reference and p.set_code and p.region.value == "EN_TCG" and len(scenarios) < 5 and not graded
              and not japanese):
            # A guide price is for an ungraded English card: never for a slab or a Japanese listing.
            ref = book.reference.price(p.set_code, now)
            if ref and ref[1].lower() == p.rarity.lower():
                try:
                    usd = book.to_home(ref[0], "USD", now)
                    sc.basis, sc.resale_mid, sc.resale_low = "reference", float(usd), float(usd) * 0.6
                    sc.profit_mid = float(ex.net(usd)) - all_in
                    sc.profit_low = float(ex.net(usd * Decimal("0.6"))) - all_in
                except StaleRateError:
                    pass
        scenarios.append(sc)

    # Money figures come from sold evidence only. A guide price is a lead (below), never an expected profit.
    priced = [s for s in scenarios if s.profit_mid is not None and s.basis == "sales"]
    leads = [s for s in scenarios if s.profit_mid is not None and s.basis == "reference"]
    share = sum(s.weight for s in priced) / all_w
    expected, best, evpi, cond = None, None, 0.0, 0.0
    if priced:
        # Conditional on being one of the priced prints, and only stated when they are most of the probability:
        # a 4% priced slice must not stand for the whole listing.
        cond = sum(s.weight * s.profit_mid for s in priced) / (share * all_w)
        expected = cond if share >= MIN_PRICED_SHARE else None
        best = max(s.profit_mid for s in priced)
        # Value of knowing which print it is, over absolute weights: unpriced possibilities add nothing to it.
        evpi = (sum(s.weight * max(0.0, s.profit_mid) for s in priced) - max(0.0, sum(s.weight * s.profit_mid for s in priced))) / all_w

    # -- what kind of edge is this? -------------------------------------------------------------------------------
    sold_backed = [s for s in priced if s.basis == "sales"]
    total_w = sum(s.weight for s in scenarios) or 1.0
    cheap_w = sum(s.weight for s in sold_backed if s.max_buy is not None and float(ask_home) <= s.max_buy) / total_w
    if cheap_w >= UNDERPRICED_SHARE:
        kinds.append("UNDERPRICED")
        reasons.append(f"Ask is under the max-buy price for {cheap_w:.0%} of what this listing could be, on verified sales.")
    elif cheap_w > 0:
        # Cheap only if it is one particular print: a bet on identity, which the check below can settle.
        kinds.append("RIGHT_PRINT_ONLY")
        reasons.append(f"Under max buy only if it is the right print ({cheap_w:.0%} of the probability). Confirm before buying.")
    if an.has("CODE_NAME_MISMATCH", "RARITY_NOT_ON_CODE", "PHOTO_CONTRADICTS_SELLER"):
        kinds.append("MISIDENTIFIED")
    if an.has("RARITY_UNSTATED", "PHOTO_ADDS_CODE") and best is not None and best > 0:
        kinds.append("HIDDEN_VARIANT")
    if an.has("SELLER_UNSURE"):
        kinds.append("SELLER_UNSURE")
    lead = max((s.profit_low / all_in for s in leads if (s.profit_low or 0) > 0), default=0.0)
    if lead > 0 and not sold_backed:
        kinds.append("REFERENCE_GAP")
        reasons.append("Ask is well under a public aggregate price. An aggregate is a lead only: no sale backs it yet.")

    lot_rows, gr = [], None
    if an.parsed.is_lot:
        lot_rows = lot_contents(f"{raw.title}\n{raw.description}", catalog)
        total = 0.0
        net_total = 0.0
        for row in lot_rows:
            vals = [book.value(c) for c in row["candidates"][:MAX_SCENARIOS]]
            est = [v.estimate.low for v in vals if v.estimate]
            # A row is only worth its cheapest possible print, so every print it could be needs a price. Pricing it
            # from the one print that happens to have sales would value "Blue-Eyes" at the LOB price.
            complete = bool(est) and len(est) == len(vals) and len(row["candidates"]) <= MAX_SCENARIOS
            row["value_low"] = min(est) if complete else None
            row["priced"] = complete
            if complete:
                total += row["value_low"]
                # Each card is sold on its own: its own fixed fee and postage, not one fee for the whole lot.
                net_total += float(ex.net(Decimal(str(round(row["value_low"], 2)))))
        if total and net_total > all_in:
            kinds.append("LOT_UNDERPRICED")
            expected = net_total - all_in
            best = max(best or 0.0, expected)
            reasons.append(f"Itemized cards resell above the whole lot's cost, on {sum(r['priced'] for r in lot_rows)} priced card(s).")
        else:
            kinds.append("LOT")

    # Grading only makes sense on a card we can name, with graded sales and your own results.
    resolved = an.identity.printing
    if resolved and not raw.grade_company and ledger is not None:
        g = P.get("grading") or {}
        company = g.get("company", "PSA")
        net_by = {}
        for grade_name in ("10", "9.5", "9", "8", "7"):
            gv = book.value(resolved.id, company, grade_name)
            if gv.estimate:
                net_by[grade_name] = float(ex.net(Decimal(str(round(gv.estimate.low, 2)))))
        if net_by:
            res = grading.evaluate(raw_all_in=all_in, grading_cost=float(g.get("cost_home", 0)), net_by_grade=net_by,
                                   own_results=ledger.grade_counts(company=company), prior=g.get("prior"),
                                   target_return=float(tgt))
            if res:
                gr = asdict(res)
                gr["max_raw_buy"] = str(res.max_raw_buy) if res.max_raw_buy is not None else None
                if res.expected_profit > 0:
                    kinds.append("GRADING")
                    reasons.append(f"Grading route expects {res.expected_profit:,.0f} ({res.basis}).")

    # -- research worth doing -------------------------------------------------------------------------------------
    actions: list[ResearchAction] = []
    if an.parsed.is_lot and not lot_rows:
        actions.append(ResearchAction("Get a per-card list or binder photos, then read them", 8, 8 * rate))
    if an.identity.status != IdentityStatus.RESOLVED:
        for need in an.identity.evidence_needed[:3]:
            actions.append(ResearchAction(f"Confirm: {need}", 3, 3 * rate))
    missing = [s for s in scenarios[:5] if s.basis != "sales"]
    if missing and not an.parsed.is_lot:
        actions.append(ResearchAction("Import recent sold prices for: " + "; ".join(s.label for s in missing[:3]), 10, 10 * rate))
    if an.identity.status == IdentityStatus.RESOLVED:
        actions.append(ResearchAction("Review photos for authenticity", 5, 5 * rate))

    hours = max(0.0, (raw.ends_at - now).total_seconds() / 3600) if raw.ends_at else None
    if raw.ends_at and raw.ends_at <= now:
        reasons.append("Listing already ended.")
        return result(OBSERVE, SHELVED, ask_home=float(ask_home), all_in_home=all_in, scenarios=scenarios, kinds=kinds)
    urgency = 1 + 3 * math.exp(-hours / 24) if hours is not None else 1.0
    # Expected value of acting, scaled by how much of the listing the sold evidence covers.
    # The priced part counts for exactly its probability; what is unpriced adds nothing until it has sales.
    upside = share * max(0.0, cond) + evpi
    # "It is a lot" is a format, not an edge. An unpriced lot is worth a look only when something says it is
    # underpriced bulk: a collection or binder being cleared out, or early-era cards.
    low_text = f"{raw.title} {raw.description}".lower()
    if "LOT" in kinds and (getattr(an.parsed, "vintage_cues", ()) or any(c in low_text for c in COLLECTION_CUES)):
        kinds.append("COLLECTION")
    edge = [k for k in kinds if k != "LOT"]
    look = 60 * rate * 0.25          # what one quick look is worth when something is off but nothing is priced
    if not priced and edge:
        upside = look
    if "REFERENCE_GAP" in kinds:
        # A lead ranks by how far under the guide it is, capped: a guide price can be wildly wrong (prize cards, typos).
        upside = max(upside, look * min(2.0, lead))
    if "LOT" in kinds and not priced and edge:
        # Triage, not value: more named cards, a seller who does not know what they have, early-era cards and a
        # low ask per card make a lot more worth opening.
        named = len(lot_rows)
        per_card = (float(ask_home) / an.parsed.lot_size) if getattr(an.parsed, "lot_size", None) else None
        # Vintage bulk resells around TWD 5-10 a card: a lot near or under that per card is cheap, far above it is not.
        cheap = min(3.0, max(0.5, 10.0 / per_card)) if per_card else 1.0
        upside = look * (0.5 + 0.3 * min(named, 10) + (1.0 if an.has("SELLER_UNSURE") else 0)
                         + (0.5 if getattr(an.parsed, "vintage_cues", ()) else 0)) * cheap
    cheapest = min((a.cost_home for a in actions), default=0.0)
    priority = max(0.0, urgency * upside * (0.5 + 0.5 * share) - cheapest)

    # -- stage ---------------------------------------------------------------------------------------------------
    stage, status = OBSERVE, SHELVED
    if edge or (best is not None and best > 0):
        stage, status = SPECTATE, OPEN
    if status == OPEN and upside < cheapest:
        status = SHELVED
        reasons.append("Further checks would cost more than they could change the outcome.")
    if not edge and best is not None and best <= 0:
        reasons.append("Even the best priced scenario loses money at this ask.")
    if not priced and not edge:
        reasons.append("No sales or reference for any printing it could be; nothing here says it is special."
                       if "LOT" not in kinds else "Lot with no priced cards and no sign of underpriced bulk: shelved until a card list arrives.")
    return result(stage, status, ask_home=float(ask_home), all_in_home=all_in, scenarios=scenarios, expected_profit=expected, best_profit=best,
                  evpi=evpi, priority=priority, priced_share=share, research=actions if status == OPEN else [],
                  printing_id=resolved.id if resolved else None, lot=lot_rows, grading=gr)
