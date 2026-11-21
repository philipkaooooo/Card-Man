"""The Card Pile: listings a person pulled aside to price properly.

A pile is not a watchlist with a nicer name. Every row is re-decided here from what the store already knows -
the ask, the shipping, the sales that card actually made, the spread those sales imply - and the page ranks
them against each other on capital and return. Nothing on this page invents a price; it reads the same
snapshots the rest of the site reads. Amounts in a payload are home-currency, same as the engine wrote them.
"""
from __future__ import annotations

import json
import re
from datetime import datetime

_ITEM = re.compile(r"/itm/(?:[^/?#]+/)?(\d{9,})")
_BARE = re.compile(r"^(\d{9,})$")


def ensure(db) -> None:
    db.execute("CREATE TABLE IF NOT EXISTS card_pile (key TEXT PRIMARY KEY, added_at TEXT NOT NULL)")


def add(st, key: str, now: datetime) -> bool:
    """True when the key was not already in the pile."""
    ensure(st.db)
    had = st.db.execute("SELECT 1 FROM card_pile WHERE key=?", (key,)).fetchone()
    with st.db:
        st.db.execute("INSERT OR REPLACE INTO card_pile VALUES (?,?)", (key, now.isoformat()))
    return had is None


def remove(st, key: str) -> None:
    ensure(st.db)
    with st.db:
        st.db.execute("DELETE FROM card_pile WHERE key=?", (key,))


def keys(st) -> list[str]:
    ensure(st.db)
    return [k for (k,) in st.db.execute("SELECT key FROM card_pile ORDER BY added_at DESC")]


def resolve(st, text: str) -> str | None:
    """A pasted eBay link, an item number, or a stored key -> the key the store knows, if it knows one."""
    t = (text or "").strip()
    if not t:
        return None
    if st.db.execute("SELECT 1 FROM listing WHERE key=?", (t,)).fetchone():
        return t
    m = _ITEM.search(t) or _BARE.match(t)
    if not m:
        return None
    row = st.db.execute("SELECT key FROM listing WHERE listing_id=? ORDER BY last_seen DESC LIMIT 1",
                        (m.group(1),)).fetchone()
    return row[0] if row else None


def _num(x):
    try:
        return float(x) if x not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _items(st) -> list[dict]:
    from .photo_match import apply
    from .ui_listing import verdict
    ensure(st.db)
    out = []
    for (key,) in st.db.execute("SELECT key FROM card_pile ORDER BY added_at DESC"):
        row = st.db.execute(
            "SELECT l.title, l.url, l.data_json, o.status, o.payload_json "
            "FROM listing l LEFT JOIN opportunity o ON o.key=l.key WHERE l.key=?", (key,)).fetchone()
        if row is None:
            continue
        title, url, data_json, status, payload = row
        base = json.loads(data_json or "{}")
        d = json.loads(payload) if payload else {
            "title": title, "url": url, "game": base.get("game"), "kinds": [], "scenarios": []}
        d = apply(d, key, st.db)
        v = _VerdictProxy(verdict(d, status or "OPEN", _NullMoney))
        top = v.top or {}
        out.append({
            "key": key, "title": d.get("title") or title or key, "url": d.get("url") or url,
            "printing_id": d.get("printing_id") or "", "code": v.code,
            "ask": _num(d.get("ask_home")) if d.get("ask_home") is not None else _num(top.get("all_in_home")),
            "resale": _num(top.get("resale_mid")), "profit": _num(top.get("profit_mid")),
            "basis": top.get("basis") or "none", "n_sales": top.get("n_sales") or 0,
            "kinds": d.get("kinds") or [],
        })
    return out


class _VerdictProxy:
    def __init__(self, v):
        self.code, self.top = v.code, v.top


class _NullMoney:
    def __call__(self, v, sign=False):
        return "—"


def view(st, M, now: datetime, token: str) -> str:
    from .ui import e
    items = _items(st)
    live = [i for i in items if i["code"] in ("BUY", "CHECK", "WATCH")]
    backed = [i for i in items if i["basis"] == "sales" and i["profit"] is not None]
    capital = sum(i["ask"] or 0 for i in items)
    upside = sum(i["profit"] or 0 for i in backed)
    ranked = sorted((i for i in items if i["profit"] is not None), key=lambda i: -(i["profit"] or 0))
    rest = [i for i in items if i["profit"] is None]

    def roas(i):
        return None if not i["ask"] or i["profit"] is None else i["profit"] / i["ask"]

    def row(n, i):
        r = roas(i)
        basis = e(i["basis"]) + (f" · {i['n_sales']} sales" if i["n_sales"] else "")
        chips = "".join(f'<span class="chip">{e(k)}</span>' for k in i["kinds"][:2])
        if i["profit"] is None:
            chips += '<span class="chip warn">no sale price yet</span>'
        return (f'<tr><td class="n">{n}</td>'
                f'<td><a href="/opp/{e(i["key"])}">{e(i["title"][:90])}</a>{chips}</td>'
                f'<td class="n">{M(i["ask"])}</td><td class="n">{M(i["resale"])}</td>'
                f'<td class="n"><b>{M(i["profit"])}</b></td>'
                f'<td class="n">{"—" if r is None else f"{r:.2f}×"}</td>'
                f'<td>{basis}</td>'
                f'<td><span class="call {_cls(i["code"])}"><span class="ci">{_glyph(i["code"])}</span>{e(i["code"])}</span></td>'
                f'<td><form method="post" action="/pile/remove"><input type="hidden" name="t" value="{e(token)}">'
                f'<input type="hidden" name="key" value="{e(i["key"])}"><button class="mini">Remove</button></form></td></tr>')

    rows = [row(n, i) for n, i in enumerate(ranked, 1)]
    rows += [row("—", i) for i in rest]

    assists = []
    if items:
        assists.append(f"{len(backed)} of {len(items)} price from real sales; the rest wait on a comp before they get a profit number.")
        if len(backed) < len(items):
            assists.append("A card with no sales stays an ask. Pulling it into the pile does not make its resale real.")
        seen: dict[str, int] = {}
        for i in items:
            if i["printing_id"]:
                seen[i["printing_id"]] = seen.get(i["printing_id"], 0) + 1
        dupes = [p for p, n in seen.items() if n > 1]
        if dupes:
            assists.append(f"{len(dupes)} print{'s are' if len(dupes) != 1 else ' is'} in the pile more than once — one of them is spare capital.")
        best = next((i for i in ranked if roas(i) is not None), None)
        if best is not None:
            assists.append(f"Best return per dollar: {best['title'][:70]} at {roas(best):.2f}× on {M(best['profit'])}.")
        acts = [i for i in live if i["code"] in ("BUY", "CHECK")]
        if acts:
            assists.append(f"{len(acts)} can move now: {', '.join(i['title'][:40] for i in acts[:3])}.")

    table = ('<table class="compact"><thead><tr><th class="n">#</th><th>Listing</th><th class="n">All-in</th>'
             '<th class="n">Resale mid</th><th class="n">Profit</th><th class="n">Per $</th><th>Basis</th>'
             '<th>Call</th><th></th></tr></thead><tbody>' + "".join(rows) + "</tbody></table>"
             if items else '<p class="empty">The pile is empty. Paste a listing above, or open one from Listings.</p>')
    said = ('<h2>What the pile says</h2><ul class="moves">' + "".join(f"<li>{e(a)}</li>" for a in assists) + "</ul>"
            if assists else "")
    return ('<section><h1>Card Pile</h1>'
            f'<p class="sub">{len(items)} listing{"s" if len(items) != 1 else ""} set aside and priced against their own sales. '
            'Add an eBay link or item number; the pile reads the ask, the shipping and the sold comps the board already holds.</p>'
            '<form class="pileadd" method="post" action="/pile">'
            f'<input type="hidden" name="t" value="{e(token)}">'
            '<input name="url" placeholder="Paste an eBay listing link or item number" autocomplete="off">'
            '<button>Add to pile</button></form>'
            '<div class="stats">'
            f'<div class="stat"><small>All-in capital</small><b>{M(capital)}</b></div>'
            f'<div class="stat"><small>Expected profit, sale-backed</small><b>{M(upside)}</b></div>'
            f'<div class="stat"><small>Can move now</small><b>{len(live)}</b></div>'
            f'<div class="stat"><small>Waiting on a comp</small><b>{len(items) - len(backed)}</b></div></div>'
            + table + said + "</section>")


def _cls(code: str) -> str:
    return "good" if code == "BUY" else "warn" if code == "CHECK" else ""


def _glyph(code: str) -> str:
    return "✓" if code == "BUY" else "!" if code == "CHECK" else "·"
