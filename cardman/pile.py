"""The Card Pile: listings a person pulled aside, and chases on cards they want at a price.

A pile is not a watchlist with a nicer name. Every row is re-decided here from what the store already knows -
the ask, the shipping, the sales that card actually made, the spread those sales imply - and the page ranks
them against each other on capital and return. Nothing on this page invents a price; it reads the same
snapshots the rest of the site reads. Amounts in a payload are home-currency, same as the engine wrote them.

A chase is a card (any version, or one printing) with a threshold. The engine's own passes refresh the
cheapest live ask per printing from rows the board already holds - no extra scraping, nothing polled blind -
and a chase fires the moment an ask lands at or under the threshold. Sold comps ride along so the threshold
is set against where the market really is, not where the ask sits today.
"""
from __future__ import annotations

import json
import re
from datetime import datetime

_ITEM = re.compile(r"/itm/(?:[^/?#]+/)?(\d{9,})")
_BARE = re.compile(r"^(\d{9,})$")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS card_pile (key TEXT PRIMARY KEY, added_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS pile_price (
    key TEXT NOT NULL, at TEXT NOT NULL, ask REAL, resale REAL, PRIMARY KEY (key, at));
CREATE TABLE IF NOT EXISTS chase (
    id INTEGER PRIMARY KEY AUTOINCREMENT, passcode TEXT NOT NULL, printing_id TEXT,
    label TEXT NOT NULL, threshold REAL NOT NULL, cur TEXT NOT NULL, created_at TEXT NOT NULL,
    last_ask REAL, last_fair REAL, seen_at TEXT);
"""


def ensure(db) -> None:
    db.executescript(_SCHEMA)


def _tables(st) -> set[str]:
    return {r[0] for r in st.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}


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
    if "listing" in _tables(st) and st.db.execute("SELECT 1 FROM listing WHERE key=?", (t,)).fetchone():
        return t
    m = _ITEM.search(t) or _BARE.match(t)
    if not m or "listing" not in _tables(st):
        return None
    row = st.db.execute("SELECT key FROM listing WHERE listing_id=? ORDER BY last_seen DESC LIMIT 1",
                        (m.group(1),)).fetchone()
    return row[0] if row else None


# -- chases -----------------------------------------------------------------------------------------------------------

def chase_add(st, passcode: str, printing_id: str, label: str, threshold: float, cur: str, now: datetime) -> int:
    ensure(st.db)
    with st.db:
        cur = st.db.execute(
            "INSERT INTO chase (passcode, printing_id, label, threshold, cur, created_at) VALUES (?,?,?,?,?,?)",
            (passcode, printing_id or None, label[:120], threshold, cur, now.isoformat()))
    return cur.lastrowid


def chase_set(st, chase_id: int, threshold: float) -> None:
    ensure(st.db)
    with st.db:
        st.db.execute("UPDATE chase SET threshold=? WHERE id=?", (threshold, chase_id))


def chase_remove(st, chase_id: int) -> None:
    ensure(st.db)
    with st.db:
        st.db.execute("DELETE FROM chase WHERE id=?", (chase_id,))


def chases(st) -> list[dict]:
    ensure(st.db)
    return [{"id": i, "passcode": pc, "printing_id": pid, "label": lb, "threshold": th, "cur": c,
             "created_at": at, "last_ask": la, "last_fair": lf, "seen_at": s}
            for i, pc, pid, lb, th, c, at, la, lf, s in
            st.db.execute("SELECT id, passcode, printing_id, label, threshold, cur, created_at, last_ask, last_fair, seen_at "
                          "FROM chase ORDER BY created_at DESC")]


def _pids_of(st, chase: dict) -> list[str]:
    """The printings a chase watches: its one printing, or every printing the board knows for its card."""
    if chase["printing_id"]:
        return [chase["printing_id"]]
    if "card_listing" not in _tables(st):
        return []
    return [r[0] for r in st.db.execute(
        "SELECT DISTINCT printing_id FROM card_listing WHERE passcode=? AND printing_id IS NOT NULL",
        (chase["passcode"],))]


def _live(st, now: datetime, home: str, pids: list[str]):
    """Cheapest live ask and its sold typical per printing, straight from the store's own index."""
    from . import alerts
    if not pids:
        return {}, {}
    try:
        return alerts._asks(st, now, home, pids), alerts._typicals(st, pids)
    except Exception:
        return {}, {}


def tick(st, now: datetime, home: str) -> int:
    """Runs inside the engine's own pass, after the sweeps land: refresh pile prices and fire chases.

    Reads only rows the engine already holds, so nothing here scrapes or polls. Returns how many rows changed.
    """
    ensure(st.db)
    have = _tables(st)
    n = 0
    if "card_pile" in have and {"opportunity", "listing"} <= have:
        for (key,) in st.db.execute("SELECT key FROM card_pile"):
            row = st.db.execute("SELECT payload_json FROM opportunity WHERE key=?", (key,)).fetchone()
            if not row:
                continue
            try:
                d = json.loads(row[0] or "{}")
            except ValueError:
                continue
            top = None
            for s in d.get("scenarios") or []:
                if s.get("weight"):
                    top = s
                    break
            ask = d.get("all_in_home", d.get("ask_home"))
            resale = (top or {}).get("resale_mid")
            last = st.db.execute("SELECT ask, resale FROM pile_price WHERE key=? ORDER BY at DESC LIMIT 1",
                                 (key,)).fetchone()
            if last and (last[0], last[1]) == (ask, resale):
                continue
            with st.db:
                st.db.execute("INSERT INTO pile_price VALUES (?,?,?,?)", (key, now.isoformat(), ask, resale))
            n += 1
    if "chase" in have:
        from . import alerts as A
        try:
            from .games import card_href
        except Exception:
            card_href = None
        st.db.executescript(A._SCHEMA)
        for c in chases(st):
            pids = _pids_of(st, c)
            asks, typ = _live(st, now, home, pids)
            best_pid = min(pids, key=lambda p: asks[p]["ask"]) if asks else None
            ask = asks[best_pid]["ask"] if best_pid else None
            fair = None
            for pid in pids:
                if typ.get(pid) and typ[pid].get("fair"):
                    fair = typ[pid]["fair"] if fair is None else min(fair, typ[pid]["fair"])
            with st.db:
                st.db.execute("UPDATE chase SET last_ask=?, last_fair=?, seen_at=? WHERE id=?",
                              (ask, fair, now.isoformat(), c["id"]))
            if ask is not None and ask <= c["threshold"] and (c["last_ask"] is None or ask < c["last_ask"] - 0.005):
                disc = (c["threshold"] - ask) / c["threshold"] * 100 if c["threshold"] else 0.0
                href = card_href(c["passcode"]) if card_href is not None else ""
                detail = {"label": c["label"], "ask": round(ask, 2), "typical": round(c["threshold"], 2),
                          "pct": round(disc, 1), "cur": home,
                          "game": "pkm" if str(c["passcode"]).startswith("pkm-") else "ygo",
                          "day": now.date().isoformat(), "href": href or "",
                          "key": asks[best_pid]["key"], "url": asks[best_pid]["url"],
                          "text": f"{c['label']} asked at {ask:,.0f} {home} - under your {c['threshold']:,.0f} {home}."}
                sid = f"CHASE_UNDER:{c['id']}:{ask:.2f}"
                dj = json.dumps(detail, ensure_ascii=False)
                try:
                    with st.db:
                        got = st.db.execute("INSERT OR IGNORE INTO market_signal VALUES (?,?,?,?,?)",
                                            (sid, "CHASE_UNDER", "", now.isoformat(), dj)).rowcount
                    n += got
                except Exception:
                    pass
    return n


# -- page -------------------------------------------------------------------------------------------------------------

def _num(x):
    try:
        return float(x) if x not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _spark(points: list[float], w: int = 84, h: int = 22) -> str:
    pts = [p for p in points if p is not None]
    if len(pts) < 2:
        return ""
    lo, hi = min(pts), max(pts)
    span = (hi - lo) or 1.0
    step = w / (len(pts) - 1)
    xy = " ".join(f"{i * step:.1f},{h - 3 - (p - lo) / span * (h - 6):.1f}" for i, p in enumerate(pts))
    return (f'<svg class="pspark" viewBox="0 0 {w} {h}" width="{w}" height="{h}" aria-hidden="true">'
            f'<polyline points="{xy}" fill="none" stroke="var(--accent)" stroke-width="1.6"/></svg>')


def _items(st, M) -> list[dict]:
    from .photo_match import apply
    from .ui_listing import item_id_of, verdict
    ensure(st.db)
    have = _tables(st)
    out = []
    for (key,) in st.db.execute("SELECT key FROM card_pile ORDER BY added_at DESC"):
        row = None
        if "listing" in have:
            row = st.db.execute(
                "SELECT l.title, l.url, l.data_json, o.status, o.payload_json "
                "FROM listing l LEFT JOIN opportunity o ON o.key=l.key WHERE l.key=?", (key,)).fetchone()
        if row is None:
            continue
        title, url, data_json, status, payload = row
        base = json.loads(data_json or "{}")
        try:
            d = json.loads(payload) if payload else {
                "title": title, "url": url, "game": base.get("game"), "kinds": [], "scenarios": []}
        except ValueError:
            d = {"title": title, "url": url, "game": base.get("game"), "kinds": [], "scenarios": []}
        d = apply(d, key, st.db)
        code = "OPEN"
        try:
            v = verdict(d, status or "OPEN", M)
            code, top = v.code, v.top or {}
        except Exception:
            top = {}
        hist = [r[0] for r in st.db.execute(
            "SELECT ask FROM pile_price WHERE key=? ORDER BY at DESC LIMIT 8", (key,))]
        hist.reverse()
        out.append({
            "key": key, "title": d.get("title") or title or key, "url": d.get("url") or url,
            "printing_id": d.get("printing_id") or "", "code": code,
            "ask": _num(d.get("all_in_home", d.get("ask_home"))),
            "resale": _num(top.get("resale_mid")), "resale_low": _num(top.get("resale_low")),
            "profit": _num(top.get("profit_mid")),
            "basis": top.get("basis") or "none", "n_sales": top.get("n_sales") or 0,
            "kinds": d.get("kinds") or [], "game": d.get("game") or "ygo",
            "iid": item_id_of(key, d), "hist": hist,
        })
    return out


def _chase_rows(st, M) -> list[dict]:
    from .ui_home import img
    all_pids: list[str] = []
    for c in chases(st):
        all_pids += _pids_of(st, c)
    asks, typ = _live(st, M.now, M.home, all_pids) if all_pids else ({}, {})
    out = []
    for c in chases(st):
        pids = _pids_of(st, c)
        best_pid = min(pids, key=lambda p: asks[p]["ask"]) if asks and pids else None
        ask = asks[best_pid]["ask"] if best_pid else c["last_ask"]
        fair = None
        for pid in pids:
            if typ.get(pid) and typ[pid].get("fair"):
                fair = typ[pid]["fair"] if fair is None else min(fair, typ[pid]["fair"])
        under = ask is not None and ask <= c["threshold"]
        sold_under = fair is not None and fair <= c["threshold"]
        out.append({**c, "ask": ask, "fair": fair, "under": under, "sold_under": sold_under,
                    "pic": img(c["passcode"], "small", c["label"], cls="card")})
    return out


def view(st, idx, M, now: datetime, token: str, game: str = "ygo", back: str = "/pile") -> str:
    from .ui import e
    from .ui_listing import pair_art, photo_counts
    items = _items(st, M)
    live = [i for i in items if i["code"] in ("BUY", "CHECK", "WATCH")]
    backed = [i for i in items if i["basis"] == "sales" and i["profit"] is not None]
    capital = sum(i["ask"] or 0 for i in items)
    upside = sum(i["profit"] or 0 for i in backed)
    chs = _chase_rows(st, M)
    firing = sum(1 for c in chs if c["under"])

    def roas(i):
        return None if not i["ask"] or i["profit"] is None else i["profit"] / i["ask"]

    ph = photo_counts(st, [i["iid"] for i in items if i["iid"]])
    tiles = []
    for i in items:
        photos = {i["iid"]: 1} if i["iid"] and ph.get(i["iid"]) else None
        art = pair_art(i["key"], {"title": i["title"], "printing_id": i["printing_id"], "kinds": i["kinds"]},
                       photos, big=True)
        op = f"/opp/{e(i['key'])}"
        info = (f'<b>{e(i["title"][:90])}</b>'
                f'<div class="pi-line">All-in <b>{M(i["ask"])}</b> · Resale mid <b>{M(i["resale"])}</b></div>'
                f'<div class="pi-line">Low end {M(i["resale_low"])} · {e(i["basis"])} · {i["n_sales"]} sales</div>'
                + (f'<div class="pi-line">Profit {M(i["profit"])} ({roas(i):.2f}× per $)</div>' if roas(i) is not None else "")
                + (f'<div class="pi-line">{" → ".join(str(M(h)) for h in i["hist"])}{_spark(i["hist"])}</div>'
                   if len(i["hist"]) > 1 else "")
                + f'<div class="pi-go"><a href="{op}">Open in CardMan</a>'
                + (f'<a href="{e(i["url"])}" target="_blank" rel="noreferrer noopener">Site ↗</a>'
                   if (i["url"] or "").startswith("http") else "")
                + '</div>')
        form = (f'<form class="pcard-form" method="post" action="/pile/remove">'
                f'<input type="hidden" name="t" value="{e(token)}"><input type="hidden" name="key" value="{e(i["key"])}">'
                f'<input type="hidden" name="back" value="{e(back)}">'
                f'<button class="pbtn on" title="In the pile - remove">✓</button></form>')
        tiles.append(f'<div class="pcard"><div class="ppic">{art}</div>{form}'
                     f'<a class="ptitle" href="{op}">{e(i["title"][:70])}</a>'
                     f'<div class="pprice">{M(i["ask"])}<span class="call {_cls(i["code"])}">{_glyph(i["code"])} {e(i["code"])}</span></div>'
                     f'<div class="pinfo">{info}</div></div>')

    chase_tiles = []
    for c in chs:
        if c["under"]:
            state = '<span class="cchip fire">under now</span>'
        elif c["ask"] is not None:
            state = '<span class="cchip watch">watching</span>'
        else:
            state = '<span class="cchip idle">no live ask</span>'
        if c["sold_under"]:
            state += '<span class="cchip sold">sold at/below</span>'
        scope = "any version" if not c["printing_id"] else "one version"
        info = (f'<b>{e(c["label"])}</b>'
                f'<div class="pi-line">Chase under <b>{M(c["threshold"])}</b> · {e(scope)}</div>'
                f'<div class="pi-line">Live ask {M(c["ask"])} · Sold typical {M(c["fair"])}</div>')
        forms = (f'<form class="cset" method="post" action="/pile/chase/set">'
                 f'<input type="hidden" name="t" value="{e(token)}"><input type="hidden" name="id" value="{c["id"]}">'
                 f'<input type="hidden" name="back" value="{e(back)}">'
                 f'<input type="number" name="threshold" step="0.01" min="0.01" value="{c["threshold"]:g}" aria-label="New threshold">'
                 f'<button class="mini">Save</button></form>'
                 f'<form class="pcard-form" method="post" action="/pile/chase/remove">'
                 f'<input type="hidden" name="t" value="{e(token)}"><input type="hidden" name="id" value="{c["id"]}">'
                 f'<input type="hidden" name="back" value="{e(back)}">'
                 f'<button class="pbtn" title="Stop chasing">×</button></form>')
        chase_tiles.append(f'<div class="pcard chase"><div class="ppic">{c["pic"]}</div>{forms}'
                           f'<div class="ptitle">{e(c["label"][:70])}</div>'
                           f'<div class="pprice">{state}</div>'
                           f'<div class="pinfo">{info}</div></div>')

    pile_grid = (f'<div class="pgrid">{"".join(tiles)}</div>' if tiles
                 else '<p class="empty">Nothing set aside yet. Paste a listing above, or press ＋ on any listing card.</p>')
    chase_grid = (f'<div class="pgrid">{"".join(chase_tiles)}</div>' if chase_tiles
                  else '<p class="empty">No chases. Open any card page and press Chase - the app watches its live asks '
                       'against your number and the sold data behind them.</p>')
    assists = []
    if items:
        assists.append(f"{len(backed)} of {len(items)} price from real sales; the rest wait on a comp before they get a profit number.")
        best = max((i for i in items if roas(i) is not None), key=roas, default=None)
        if best is not None:
            assists.append(f"Best return per dollar: {best['title'][:70]} at {roas(best):.2f}× on {M(best['profit'])}.")
        acts = [i for i in live if i["code"] in ("BUY", "CHECK")]
        if acts:
            assists.append(f"{len(acts)} can move now: {', '.join(i['title'][:40] for i in acts[:3])}.")
    if chs:
        under = [c for c in chs if c["under"]]
        if under:
            assists.append(f"{len(under)} chase{'s' if len(under) != 1 else ''} under threshold right now - the bell has the link.")
        sold = [c for c in chs if c["sold_under"]]
        if sold:
            assists.append(f"Sold comps sit at or under your number for {len(sold)} chase{'s' if len(sold) != 1 else ''}: "
                           "the market may have already moved to you.")
    said = ('<section><h2>What the pile says</h2><ul class="moves">' + "".join(f"<li>{e(a)}</li>" for a in assists)
            + "</ul></section>" if assists else "")
    return ('<section><h1>Card Pile</h1>'
            f'<p class="sub">{len(items)} listing{"s" if len(items) != 1 else ""} set aside and {len(chs)} chase{"s" if len(chs) != 1 else ""} '
            'running. Set-asides price against their own sales; chases watch the live ask against your number, and the sold '
            'data behind it says whether your number is real.</p>'
            '<form class="pileadd" method="post" action="/pile">'
            f'<input type="hidden" name="t" value="{e(token)}">'
            f'<input type="hidden" name="back" value="{e(back)}">'
            '<input name="url" placeholder="Paste an eBay listing link or item number" autocomplete="off">'
            '<button>Add to pile</button></form>'
            '<div class="stats">'
            f'<div class="stat"><small>All-in capital</small><b>{M(capital)}</b></div>'
            f'<div class="stat"><small>Expected profit, sale-backed</small><b>{M(upside)}</b></div>'
            f'<div class="stat"><small>Can move now</small><b>{len(live)}</b></div>'
            f'<div class="stat"><small>Chases under threshold</small><b>{firing}</b></div></div></section>'
            '<section><h2>Set aside</h2>' + pile_grid + '</section>'
            '<section><h2>Chases</h2>' + chase_grid + '</section>'
            + said)


def _cls(code: str) -> str:
    return "good" if code == "BUY" else "warn" if code == "CHECK" else ""


def _glyph(code: str) -> str:
    return "✓" if code == "BUY" else "!" if code == "CHECK" else "·"


PILE_CSS = """
.pgrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(215px,1fr));gap:18px;margin:6px 0 4px}
.pcard{position:relative;display:flex;flex-direction:column;gap:6px;background:var(--surface);border:1px solid var(--line);
border-radius:14px;padding:10px;transition:transform .15s,box-shadow .15s}
.pcard:hover{transform:translateY(-3px);box-shadow:0 10px 26px rgba(0,0,0,.16);z-index:12}
.ppic{display:flex;justify-content:center;align-items:center;min-height:150px;background:var(--bg);border-radius:9px;
padding:6px;overflow:hidden}
.ppic img.card{max-height:168px;object-fit:contain}
.ptitle{font-size:13px;font-weight:600;color:var(--fg);text-decoration:none;display:-webkit-box;-webkit-line-clamp:2;
-webkit-box-orient:vertical;overflow:hidden;min-height:2.4em;line-height:1.3}
.ptitle:hover{text-decoration:underline}
.pprice{display:flex;align-items:center;justify-content:space-between;gap:8px;font-size:16px;font-weight:750;color:var(--accent);
font-variant-numeric:tabular-nums}
.pcard-form{position:absolute;top:14px;right:14px;z-index:3}
.pbtn{width:26px;height:26px;border-radius:999px;border:1.5px solid var(--line);background:var(--surface);color:var(--fg2);
font-weight:800;font-size:13px;line-height:1;cursor:pointer;padding:0;display:inline-flex;align-items:center;justify-content:center}
.pbtn:hover{border-color:var(--accent);color:var(--accent)}
.pbtn.on{background:var(--accent);border-color:var(--accent);color:#fff}
.pinfo{display:none;position:absolute;left:-1px;right:-1px;top:calc(100% - 3px);z-index:20;background:var(--surface);
border:1.5px solid var(--fg2);border-radius:12px 10px 13px 9px/10px 13px 9px 12px;box-shadow:6px 8px 0 rgba(0,0,0,.14);
padding:11px 12px;font-size:12px;color:var(--fg)}
.pcard:hover .pinfo{display:block}
.pinfo b{display:block;font-size:13px;margin-bottom:5px}
.pi-line{margin:3px 0;color:var(--fg2);font-variant-numeric:tabular-nums}
.pi-line b{display:inline;font-size:12px;color:var(--fg);margin:0}
.pi-go{display:flex;gap:10px;margin-top:7px}
.pi-go a{font-size:12px;font-weight:700;color:var(--teal-deep);text-decoration:none}
.pi-go a:hover{text-decoration:underline}
.pspark{display:inline-block;vertical-align:middle;margin-left:6px}
.cchip{display:inline-block;padding:1px 8px;border-radius:999px;font-size:11px;font-weight:800;letter-spacing:.03em;
text-transform:uppercase;border:1px solid var(--line);color:var(--fg2)}
.cchip.fire{color:#c4553b;border-color:#c4553b;background:color-mix(in srgb,#c4553b 10%,var(--surface))}
.cchip.watch{color:#9a6a15;border-color:#9a6a15;background:color-mix(in srgb,#9a6a15 9%,var(--surface))}
.cchip.sold{color:#1d6b4f;border-color:#1d6b4f;background:color-mix(in srgb,#1d6b4f 10%,var(--surface))}
.cchip.idle{color:var(--muted)}
.pcard.chase .cset{display:flex;gap:6px;margin-top:auto}
.pcard.chase .cset input{width:100%;min-width:0;padding:6px 8px;border:1px solid var(--axis);border-radius:8px;
background:var(--bg);color:var(--fg);font-variant-numeric:tabular-nums}
.chaseform{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin:12px 0 4px;padding:10px;border:1px dashed var(--axis);
border-radius:12px;background:var(--surface)}
.chaseform select,.chaseform input{padding:8px 10px;border:1px solid var(--axis);border-radius:9px;background:var(--bg);color:var(--fg)}
.chaseform input[type=number]{width:150px}
.chaseform button{padding:8px 16px;border:0;border-radius:9px;background:var(--accent);color:#fff;font-weight:700;cursor:pointer}
.chaseform button:hover{filter:brightness(1.06)}
.hun-item{position:relative;display:flex;min-width:0}
.hun-item>.hun{flex:1}
.hun-item .pilebtn{position:absolute;top:8px;right:8px;z-index:5;opacity:0;transition:opacity .15s}
.hun-item:hover .pilebtn{opacity:1}
.hun-item .pilebtn .pbtn{background:color-mix(in srgb,var(--surface) 88%,transparent)}
.hungrid>.hun-item{animation:hunin .5s cubic-bezier(.2,.7,.3,1) backwards}
.hungrid>.hun-item:nth-child(2){animation-delay:40ms}.hungrid>.hun-item:nth-child(3){animation-delay:80ms}
.hungrid>.hun-item:nth-child(4){animation-delay:120ms}.hungrid>.hun-item:nth-child(5){animation-delay:160ms}
.hungrid>.hun-item:nth-child(6){animation-delay:200ms}.hungrid>.hun-item:nth-child(7){animation-delay:240ms}
.hungrid>.hun-item:nth-child(8){animation-delay:280ms}.hungrid>.hun-item:nth-child(9){animation-delay:320ms}
.hungrid>.hun-item:nth-child(10){animation-delay:360ms}.hungrid>.hun-item:nth-child(11){animation-delay:400ms}
.hungrid>.hun-item:nth-child(12){animation-delay:440ms}
@media(prefers-reduced-motion:reduce){.hungrid>.hun-item{animation:none}}
.dpile{display:inline-flex;align-items:center;gap:7px;margin-left:4px}
.dpile form{display:inline-flex}
.dpile .pbtn{width:24px;height:24px;font-size:12px}
"""
