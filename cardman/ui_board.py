"""The Listings board: every public listing, scanned and labeled, with a working memory.

One rail of live facets, a sort studio you can drag, pins and review stamps, colored labels,
saved views, bulk handling with undo, a peek drawer, and keys for the whole flow.
The engine's own verdicts are never touched - everything here is your overlay on top.
"""
from __future__ import annotations

import json
from datetime import datetime
from html import escape as _esc
from urllib.parse import parse_qs, quote, urlencode


def e(s) -> str:
    return _esc(str(s if s is not None else ""), quote=True)


SCHEMA = """
CREATE TABLE IF NOT EXISTS board_mark (key TEXT PRIMARY KEY, mark TEXT NOT NULL, at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS board_label (key TEXT NOT NULL, label TEXT NOT NULL, color TEXT NOT NULL,
    at TEXT NOT NULL, PRIMARY KEY (key, label));
CREATE TABLE IF NOT EXISTS board_view (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
    qs TEXT NOT NULL, game TEXT NOT NULL, created TEXT NOT NULL, uses INTEGER NOT NULL DEFAULT 0, last_used TEXT);
"""

MARKS = ("pinned", "reviewed", "hidden")
COLORS = (("red", "#e05252"), ("blue", "#2f6ce0"), ("gold", "#d4af6a"), ("green", "#2fbf71"),
          ("amber", "#e6a23c"), ("ink", ""), ("violet", "#b05fd6"), ("teal", "#3fb8c9"))
COLOR_NAMES = {c for c, _ in COLORS}
SORTS = (("lane", "Best lane", "a"), ("profit", "Profit", "d"), ("ask", "Price", "a"),
         ("value", "Value", "d"), ("fresh", "Newness", "d"), ("ends", "Ending", "a"),
         ("pc", "Under PC", "a"))
SORT_KEYS = {k for k, _, _ in SORTS}
SORT_LABEL = {k: lab for k, lab, _ in SORTS}
SORT_DEFAULT_DIR = {k: d for k, _, d in SORTS}
FRESH = (("1h", "Past hour", 3600), ("24h", "Past day", 86400), ("7d", "Past week", 604800))
PER_PAGE = 48
NEW = "NEW"
COND = (("MT", "Mint", "green"), ("NM", "Near Mint", "blue"), ("EX", "Excellent", "blue"),
        ("LP", "Lightly Played", "gold"), ("MP", "Moderately Played", "amber"),
        ("HP", "Heavily Played", "red"), ("DMG", "Damaged", "ink"))
COND_LABEL = {c: lab for c, lab, _ in COND}
_GEM = "M7 3h10l4 6-9 12L3 9z"
COND_ICON = {
    "MT": f'<svg viewBox="0 0 24 24" width="13" height="13" aria-hidden="true"><path d="{_GEM}" fill="currentColor"/>'
          '<path d="M8.6 7.6l1.8-1.8" stroke="#fff" stroke-width="1.7" stroke-linecap="round"/></svg>',
    "NM": f'<svg viewBox="0 0 24 24" width="13" height="13" aria-hidden="true"><path d="{_GEM}" fill="currentColor"/>'
          '<path d="M3 9h18M12 3v18" stroke="var(--bg)" stroke-width="1.1" opacity=".45"/></svg>',
    "EX": f'<svg viewBox="0 0 24 24" width="13" height="13" aria-hidden="true"><path d="{_GEM}" fill="currentColor" opacity=".8"/>'
          '<path d="M3 9h18" stroke="var(--bg)" stroke-width="1.6"/></svg>',
    "LP": f'<svg viewBox="0 0 24 24" width="13" height="13" aria-hidden="true"><path d="{_GEM}" fill="none" stroke="currentColor" stroke-width="2"/>'
          '<path d="M5.4 12h13.2L12 20.6z" fill="currentColor"/></svg>',
    "MP": f'<svg viewBox="0 0 24 24" width="13" height="13" aria-hidden="true"><path d="{_GEM}" fill="none" stroke="currentColor" stroke-width="2"/>'
          '<path d="M8.6 16h6.8L12 20.6z" fill="currentColor"/></svg>',
    "HP": f'<svg viewBox="0 0 24 24" width="13" height="13" aria-hidden="true"><path d="{_GEM}" fill="none" stroke="currentColor" stroke-width="2"/>'
          '<path d="M12 4l-1.6 5 3.2 3-2.2 5.4" fill="none" stroke="currentColor" stroke-width="1.8"/></svg>',
    "DMG": '<svg viewBox="0 0 24 24" width="13" height="13" aria-hidden="true"><path d="M7 3h4.4l-1.2 7.4-4.6.8z" fill="currentColor"/>'
           '<path d="M13.6 3H17l4 6-7.4 9.6-1.8-5.4z" fill="currentColor" opacity=".5"/></svg>',
}
GRADE_ICON = ('<svg viewBox="0 0 24 24" width="13" height="13" aria-hidden="true"><rect x="5" y="2.5" width="14" height="19" rx="2" '
              'fill="none" stroke="currentColor" stroke-width="2"/><rect x="8" y="6" width="8" height="5" rx="1" fill="currentColor"/>'
              '<path d="M8 14h8M8 17h5" stroke="currentColor" stroke-width="1.6"/></svg>')
NEW_ICON = ('<svg viewBox="0 0 24 24" width="15" height="15" aria-hidden="true"><circle cx="12" cy="12" r="9" fill="none" '
            'stroke="currentColor" stroke-width="2"/><path d="M9.6 9.4a2.5 2.5 0 1 1 3.3 2.4c-.8.3-.9.9-.9 1.6" fill="none" '
            'stroke="currentColor" stroke-width="2" stroke-linecap="round"/><circle cx="12" cy="16.8" r="1.2" fill="currentColor"/></svg>')


def ensure(st) -> None:
    st.db.executescript(SCHEMA)


def _ep(s):
    try:
        return datetime.fromisoformat(s).timestamp()
    except Exception:
        return None


def _ago(now: datetime, iso: str | None) -> str:
    t = _ep(iso or "")
    if t is None:
        return ""
    s = max(0, now.timestamp() - t)
    if s < 3600:
        return f"{max(1, int(s // 60))}m ago"
    if s < 86400:
        return f"{int(s // 3600)}h ago"
    return f"{int(s // 86400)}d ago"


def marks_of(st) -> dict[str, str]:
    return {k: m for k, m in st.db.execute("SELECT key, mark FROM board_mark")}


def labels_of(st) -> dict[str, list[tuple[str, str]]]:
    out: dict[str, list[tuple[str, str]]] = {}
    for k, lab, c in st.db.execute("SELECT key, label, color FROM board_label ORDER BY at"):
        out.setdefault(k, []).append((lab, c))
    return out


def views_of(st, game: str) -> list[tuple]:
    return st.db.execute("SELECT id, name, qs, uses FROM board_view WHERE game=? ORDER BY uses DESC, id DESC LIMIT 12",
                         (game,)).fetchall()


def apply_mark(st, keys: list[str], mark: str, now: datetime) -> dict[str, str]:
    """Set or clear one mark on many listings. Returns what each key had before, for undo."""
    prev = {}
    keys = [k for k in dict.fromkeys(keys) if k][:500]
    with st.db:
        for k in keys:
            row = st.db.execute("SELECT mark FROM board_mark WHERE key=?", (k,)).fetchone()
            prev[k] = row[0] if row else ""
            if mark in MARKS:
                st.db.execute("INSERT INTO board_mark (key, mark, at) VALUES (?,?,?) "
                              "ON CONFLICT(key) DO UPDATE SET mark=excluded.mark, at=excluded.at",
                              (k, mark, now.isoformat()))
            else:
                st.db.execute("DELETE FROM board_mark WHERE key=?", (k,))
    return prev


def restore_marks(st, prev: dict[str, str], now: datetime) -> None:
    with st.db:
        for k, m in list(prev.items())[:500]:
            if m in MARKS:
                st.db.execute("INSERT INTO board_mark (key, mark, at) VALUES (?,?,?) "
                              "ON CONFLICT(key) DO UPDATE SET mark=excluded.mark, at=excluded.at",
                              (k, m, now.isoformat()))
            else:
                st.db.execute("DELETE FROM board_mark WHERE key=?", (k,))


def apply_label(st, keys: list[str], label: str, color: str, now: datetime) -> list[str]:
    label = label.strip()[:40]
    if not label:
        return []
    color = color if color in COLOR_NAMES else "ink"
    added = []
    with st.db:
        for k in [k for k in dict.fromkeys(keys) if k][:500]:
            cur = st.db.execute("SELECT 1 FROM board_label WHERE key=? AND label=?", (k, label)).fetchone()
            if not cur:
                st.db.execute("INSERT INTO board_label (key, label, color, at) VALUES (?,?,?,?)",
                              (k, label, color, now.isoformat()))
                added.append(k)
    return added


def remove_label(st, keys: list[str], label: str) -> None:
    with st.db:
        for k in [k for k in dict.fromkeys(keys) if k][:500]:
            st.db.execute("DELETE FROM board_label WHERE key=? AND label=?", (k, label))


def save_view(st, name: str, qs: str, game: str, now: datetime, vid: int | None = None) -> int:
    name = name.strip()[:40] or "Saved view"
    qs = qs.lstrip("?")[:2000]
    with st.db:
        if vid:
            st.db.execute("INSERT OR REPLACE INTO board_view (id, name, qs, game, created, uses, last_used) "
                          "VALUES (?,?,?,?,?,?,?)", (vid, name, qs, game, now.isoformat(), 0, None))
            return vid
        cur = st.db.execute("INSERT INTO board_view (name, qs, game, created) VALUES (?,?,?,?)",
                            (name, qs, game, now.isoformat()))
        n = st.db.execute("SELECT COUNT(*) FROM board_view WHERE game=?", (game,)).fetchone()[0]
        if n > 12:
            st.db.execute("DELETE FROM board_view WHERE game=? AND id NOT IN "
                          "(SELECT id FROM board_view WHERE game=? ORDER BY uses DESC, id DESC LIMIT 12)", (game, game))
        return cur.lastrowid


def del_view(st, vid: int) -> dict | None:
    row = st.db.execute("SELECT id, name, qs, game FROM board_view WHERE id=?", (vid,)).fetchone()
    if not row:
        return None
    with st.db:
        st.db.execute("DELETE FROM board_view WHERE id=?", (vid,))
    return {"id": row[0], "name": row[1], "qs": row[2], "game": row[3]}


def parse_sort(s: str) -> list[tuple[str, str]]:
    out = []
    for part in (s or "").split(","):
        k, _, d = part.partition(":")
        if k in SORT_KEYS and k not in [a for a, _ in out]:
            out.append((k, "a" if d == "a" else "d"))
    return out or [("lane", "a"), ("profit", "d")]


def sort_str(stack: list[tuple[str, str]]) -> str:
    return ",".join(f"{k}:{d}" for k, d in stack)


# -- the view ---------------------------------------------------------------------------------------------------------

def view_board(st, cards, now: datetime, M, *, qs: dict | None = None, home_cur: str = "TWD",
               game: str = "ygo", token: str = "", back: str = "/opportunities") -> str:
    from .ui_decide import deal_card, first_seen, rail
    from .ui_listing import ORDER, open_listings, photo_counts, item_id_of, site_of, SITES
    from .ui_home import _pc_grade, _listing_art
    from .pile import keys as pile_keys
    from .history import sparks as _sparks
    from . import conditions
    from .fx import FxTable
    from decimal import Decimal
    from datetime import timedelta
    ensure(st)
    qs = dict(qs or {})
    g1 = lambda k, d="": (qs.get(k) or [d])[0]

    view_id = g1("view")
    if view_id.isdigit():
        row = st.db.execute("SELECT qs FROM board_view WHERE id=? AND game=?", (int(view_id), game)).fetchone()
        if row:
            base = parse_qs(row[0], keep_blank_values=True)
            base.pop("view", None)
            base.update({k: v for k, v in qs.items() if k != "view"})
            qs = base
            with st.db:
                st.db.execute("UPDATE board_view SET uses=uses+1, last_used=? WHERE id=?", (now.isoformat(), int(view_id)))
            g1 = lambda k, d="": (qs.get(k) or [d])[0]

    v = g1("v", "all")
    v = v if v in ("all", "BUY", "CHECK", "WATCH", "PASS", "NEW", "every") else "all"
    t = g1("t", "any")
    t = t if t in ("any", "singles", "lots") else "any"
    site = g1("site", "any")
    site = site if site in ("any", "ebay", "yahoo", "mercari", "other") else "any"
    q = g1("q").strip()[:80]
    fresh = g1("fresh")
    fresh = fresh if fresh in dict((a, c) for a, b, c in FRESH) else ""
    mk = g1("mk")
    mk = mk if mk in MARKS else ""
    lab = g1("lab")[:40]
    try:
        pmin = float(g1("pmin")) if g1("pmin") else None
    except ValueError:
        pmin = None
    try:
        pmax = float(g1("pmax")) if g1("pmax") else None
    except ValueError:
        pmax = None
    cond = g1("cond")
    cond = cond if cond in COND_LABEL else ""
    pcflag = g1("pc") == "1"
    print_id = g1("print")[:160]
    grp = g1("grp", "card")
    grp = grp if grp in ("card", "list") else "card"
    if print_id:
        grp = "list"
    stack = parse_sort(g1("sort"))
    try:
        page = max(1, int(g1("page", "1")))
    except ValueError:
        page = 1

    state = {"v": v, "t": t, "site": site, "q": q, "fresh": fresh, "mk": mk, "lab": lab,
             "pmin": g1("pmin") if pmin is not None else "", "pmax": g1("pmax") if pmax is not None else "",
             "sort": sort_str(stack), "page": str(page) if page > 1 else "",
             "cond": cond, "pc": "1" if pcflag else "", "print": print_id, "grp": grp if grp != "card" else ""}

    def href(**kw):
        merged = {**state, **kw}
        if any(a in kw for a in ("v", "t", "site", "q", "fresh", "mk", "lab", "pmin", "pmax", "cond", "pc", "print", "grp")):
            merged["page"] = ""
        clean = {a: b for a, b in merged.items()
                 if b not in ("", None) and not (a == "v" and b == "all") and not (a in ("t", "site") and b == "any")
                 and not (a == "sort" and b == "lane:a,profit:d") and not (a == "grp" and b == "card")}
        return back + ("?" + urlencode(clean) if clean else "")

    items = open_listings(st, M, game)
    seen = first_seen(st, [k for k, *_ in items])
    marks = marks_of(st)
    labels = labels_of(st)
    piled = frozenset(pile_keys(st))
    analyzed = {k for k, in st.db.execute("SELECT key FROM opportunity WHERE payload_json IS NOT NULL")}
    cond_of: dict[str, tuple[str, str]] = {}
    for k, raw, title in st.db.execute("SELECT key, json_extract(data_json,'$.condition'), title FROM listing WHERE ended_at IS NULL"):
        code = conditions.normalize(raw)
        src = "seller" if code else ""
        if not code:
            code = conditions.title_condition(title)
            src = "title" if code else ""
        if code:
            cond_of[k] = (code, src)
    pcm: dict[str, dict] = {}
    if "pc_card" in {r[0] for r in st.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}:
        for pid, grades in st.db.execute("SELECT printing_id, grades FROM pc_card WHERE status='ok' AND grades IS NOT NULL"):
            try:
                row = {g: float(u["p"]) for g, u in (json.loads(grades) or {}).items() if isinstance(u, dict) and u.get("p")}
                if row:
                    pcm[pid] = row
            except Exception:
                pass
    pc_pct: dict[str, float] = {}
    try:
        fx = FxTable(st.fx_rates(), max_age=timedelta(days=7))
        rate_cache: dict[tuple, float] = {}
        for key, status, d, vd in items:
            pid = d.get("printing_id")
            ask = d.get("all_in_home")
            if not pid or not ask or pid not in pcm:
                continue
            gk = _pc_grade(d.get("grade"))
            p = pcm[pid].get(gk)
            if not p:
                continue
            ck = (pid, gk)
            if ck not in rate_cache:
                try:
                    rate_cache[ck] = float(fx.convert(Decimal(str(p)), "USD", M.home, now))
                except Exception:
                    rate_cache[ck] = 0.0
            rate = rate_cache[ck]
            if rate > 0:
                pct = (float(ask) - rate) / rate * 100
                if pct <= -3:
                    pc_pct[key] = pct
    except Exception:
        pc_pct = {}
    is_lot = lambda d: "LOT" in (d.get("kinds") or [])
    now_ts = now.timestamp()
    win = dict((a, c) for a, b, c in FRESH).get(fresh)
    ql = q.lower()

    def base_ok(i):
        key, status, d, vd = i
        if t == "singles" and is_lot(d):
            return False
        if t == "lots" and not is_lot(d):
            return False
        if site != "any" and site_of(key) != site:
            return False
        if ql and ql not in (d.get("title") or "").lower() and ql not in key.lower():
            return False
        if win:
            ft = _ep(seen.get(key) or "")
            if ft is None or now_ts - ft > win:
                return False
        ask = d.get("all_in_home")
        if pmin is not None and (ask is None or ask < pmin):
            return False
        if pmax is not None and (ask is None or ask > pmax):
            return False
        if cond and cond_of.get(key, ("", ""))[0] != cond:
            return False
        if pcflag and key not in pc_pct:
            return False
        if print_id and d.get("printing_id") != print_id:
            return False
        return True

    pool = [i for i in items if base_ok(i)]
    ecode = lambda i: i[3].code if i[0] in analyzed else NEW
    board_order = {**ORDER, NEW: 4}
    counts = {c: sum(1 for i in pool if ecode(i) == c) for c in list(ORDER) + [NEW]}
    mk_counts = {m: sum(1 for i in pool if marks.get(i[0]) == m) for m in MARKS}
    lab_counts: dict[str, list] = {}
    for i in pool:
        for lname, c in labels.get(i[0], []):
            ent = lab_counts.setdefault(lname, [0, c])
            ent[0] += 1

    shown = [i for i in pool if v in ("all", "every") or ecode(i) == v]
    if mk:
        shown = [i for i in shown if marks.get(i[0]) == mk]
    else:
        shown = [i for i in shown if marks.get(i[0]) != "hidden"]
    if lab:
        shown = [i for i in shown if any(lname == lab for lname, _ in labels.get(i[0], []))]

    def sval(i, k):
        key, status, d, vd = i
        if k == "lane":
            return board_order[ecode(i)]
        if k == "profit":
            return vd.margin if vd.margin is not None else -1e18
        if k == "ask":
            a = d.get("all_in_home")
            return a if a is not None else 1e18
        if k == "value":
            top = vd.top or {}
            r = top.get("resale_mid")
            return float(r) if r else -1e18
        if k == "fresh":
            return _ep(seen.get(key) or "") or 0.0
        if k == "ends":
            return _ep(d.get("ends_at") or "") or 1e18
        if k == "pc":
            return pc_pct.get(key, 1e18)
        return 0

    def skey(i):
        vals = tuple(sval(i, k) if dr == "a" else -sval(i, k) for k, dr in stack)
        return (0 if marks.get(i[0]) == "pinned" else 1,) + vals + (i[0],)

    shown.sort(key=skey)
    group_info: dict[str, tuple] = {}
    n_listings = len(shown)
    n_groups = len(shown)
    if grp == "card":
        seen_g: dict[str, list] = {}
        order_g: list[str] = []
        for i in shown:
            gk = i[2].get("printing_id") or ("solo:" + i[0])
            a = i[2].get("all_in_home")
            if gk in seen_g:
                ent = seen_g[gk]
                ent[1] += 1
                if a is not None:
                    ent[2] = min(ent[2], a)
                    ent[3] = max(ent[3], a)
            else:
                seen_g[gk] = [i[0], 1, a if a is not None else 1e18, a if a is not None else -1e18, gk]
                order_g.append(gk)
        n_groups = len(order_g)
        for gk in order_g:
            rep_key, cnt, lo, hi, _ = seen_g[gk]
            if cnt > 1:
                group_info[rep_key] = (cnt, lo, hi, gk)
        reps = {seen_g[gk][0] for gk in order_g}
        shown = [i for i in shown if i[0] in reps]
    total = n_groups
    pages = max(1, (total + PER_PAGE - 1) // PER_PAGE)
    page = min(page, pages)
    page_items = shown[(page - 1) * PER_PAGE:page * PER_PAGE]

    sp = _sparks(st, now, home_cur)
    ph = photo_counts(st, [item_id_of(key, d) for key, _s, d, _v in page_items])

    # -- sort studio
    pills = []
    for n, (k, dr) in enumerate(stack, 1):
        flip = "a" if dr == "d" else "d"
        toggled = sort_str([(k2, flip if k2 == k else d2) for k2, d2 in stack])
        removed = sort_str([(k2, d2) for k2, d2 in stack if k2 != k]) or "lane:a"
        arrow = "↓" if dr == "d" else "↑"
        how = "highest first" if dr == "d" else "lowest first"
        pills.append(f'<li class="bs-pill" draggable="true" data-k="{k}" data-d="{dr}">'
                     f'<b class="bs-n">{n}</b><span class="bs-w">{e(SORT_LABEL[k])}</span>'
                     f'<a class="bs-dir" href="{e(href(sort=toggled))}" data-tip="{e(SORT_LABEL[k] + ": " + how + ". Click to flip.")}" '
                     f'aria-label="{e(SORT_LABEL[k] + " " + how + ", flip")}">{arrow}</a>'
                     f'<a class="bs-x" href="{e(href(sort=removed))}" aria-label="{e("Stop sorting by " + SORT_LABEL[k])}" '
                     f'data-tip="{e("Stop sorting by " + SORT_LABEL[k])}">×</a></li>')
    remaining = [(k, lab) for k, lab, _ in SORTS if k not in [a for a, _ in stack]]
    addopts = "".join(f'<option value="{k}">{e(lab)}</option>' for k, lab in remaining)
    studio = ('<div class="bsort" role="group" aria-label="Sort order"><span class="bs-l">Sort</span>'
              f'<ol class="bs-list" id="bs-list">{"".join(pills)}</ol>'
              + (f'<select class="bs-add" id="bs-add" aria-label="Add a way to sort"><option value="">＋ add</option>{addopts}</select>'
                 if remaining else "")
              + '</div>')

    # -- facet rail
    def chip(param, val, text, on, n=None, dot=""):
        current = ' aria-current="true"' if on else ""
        count = f'<b class="fc-n">{n:,}</b>' if n is not None else ""
        d = f'<i class="fc-dot c-{dot}" aria-hidden="true"></i>' if dot else ""
        return f'<a class="fchip{" on" if on else ""}" href="{e(href(**{param: val}))}"{current}>{d}{e(text)}{count}</a>'

    rail_html = rail(counts, v if v in ORDER else None, lambda code, on: href(v="all" if on else code))
    new_on = v == NEW
    new_pill = (f'<a class="lane l-new{" on" if new_on else ""}" href="{e(href(v="all" if new_on else NEW))}"'
                f'{" aria-current=" + chr(34) + "true" + chr(34) if new_on else ""} '
                f'data-tip="{e("Unchecked: the engine has not priced this listing yet")}">'
                f'<span class="lane-i">{NEW_ICON}</span><span class="lane-w">Unchecked</span>'
                f'<b class="lane-n" data-n="{counts[NEW]}">{counts[NEW]:,}</b></a>')
    rail_html = rail_html.replace("</nav>", new_pill + "</nav>")
    type_sec = ('<div class="fsec" role="group" aria-label="Card kind"><h3>Card kind</h3>'
                + chip("t", "any", "All cards", t == "any") + chip("t", "singles", "Single cards", t == "singles")
                + chip("t", "lots", "Lots", t == "lots") + '</div>')
    site_sec = ('<div class="fsec" role="group" aria-label="Sites"><h3>Sites</h3>'
                + "".join(chip("site", sv, sl, site == sv) for sv, sl in SITES) + '</div>')
    fresh_sec = ('<div class="fsec" role="group" aria-label="First seen"><h3>First seen</h3>'
                 + chip("fresh", "", "Any time", not fresh)
                 + "".join(chip("fresh", fk, fl, fresh == fk) for fk, fl, _ in FRESH) + '</div>')
    price_sec = ('<div class="fsec" role="group" aria-label="Total to you"><h3>Total to you</h3>'
                 f'<form class="bprice" method="get" action="{e(back)}">'
                 + "".join(f'<input type="hidden" name="{k}" value="{e(val)}">' for k, val in state.items()
                           if val and k not in ("pmin", "pmax", "page"))
                 + f'<input type="number" name="pmin" min="0" step="any" inputmode="decimal" placeholder="from" '
                   f'aria-label="Lowest total to you" value="{e(state["pmin"])}">'
                 + f'<input type="number" name="pmax" min="0" step="any" inputmode="decimal" placeholder="to" '
                   f'aria-label="Highest total to you" value="{e(state["pmax"])}">'
                 + f'<button type="submit">{e(home_cur)}</button></form></div>')
    mark_sec = ('<div class="fsec" role="group" aria-label="Your marks"><h3>Your marks</h3>'
                + chip("mk", "", "Everything up", not mk, sum(1 for i in pool if marks.get(i[0]) != "hidden"))
                + chip("mk", "pinned", "Pinned to top", mk == "pinned", mk_counts["pinned"])
                + chip("mk", "reviewed", "Reviewed", mk == "reviewed", mk_counts["reviewed"])
                + chip("mk", "hidden", "Hidden", mk == "hidden", mk_counts["hidden"]) + '</div>')
    lab_sec = ""
    if lab_counts:
        chips = "".join(chip("lab", lname if lab != lname else "", lname, lab == lname, n, c)
                        for lname, (n, c) in sorted(lab_counts.items(), key=lambda kv: -kv[1][0])[:14])
        lab_sec = ('<div class="fsec" role="group" aria-label="Labels"><h3>Labels</h3>' + chips + '</div>')
    cond_counts = {c: 0 for c in COND_LABEL}
    for i in pool:
        c = cond_of.get(i[0], ("", ""))[0]
        if c in cond_counts:
            cond_counts[c] += 1
    cond_chips = "".join(
        f'<a class="fchip{" on" if cond == c else ""}" href="{e(href(cond="" if cond == c else c))}"'
        f'{" aria-current=" + chr(34) + "true" + chr(34) if cond == c else ""}>'
        f'<span class="fc-cnd c-{col}">{COND_ICON[c]}</span>{e(lab)}<b class="fc-n">{cond_counts[c]:,}</b></a>'
        for c, lab, col in COND if cond_counts[c])
    cond_sec = ('<div class="fsec" role="group" aria-label="Condition"><h3>Condition</h3>' + cond_chips + '</div>') if cond_chips else ""
    pc_sec = ""
    n_pc = sum(1 for i in pool if i[0] in pc_pct)
    if n_pc:
        pc_sec = ('<div class="fsec" role="group" aria-label="PriceCharting"><h3>PriceCharting</h3>'
                  + chip("pc", "" if pcflag else "1", "Under the PC market", pcflag, n_pc) + '</div>')
    facets = (f'<aside class="brail" aria-label="Filters">{rail_html}{type_sec}{site_sec}{fresh_sec}{price_sec}{cond_sec}{pc_sec}{mark_sec}{lab_sec}</aside>')

    # -- saved views
    views = views_of(st, game)
    vpills = "".join(
        f'<span class="bview{" on" if view_id == str(vid) else ""}"><a href="{e(back + "?view=" + str(vid))}">{e(name)}</a>'
        f'<form method="post" action="/board/viewdel" class="bvx" data-bviewdel>'
        f'<input type="hidden" name="t" value="{e(token)}"><input type="hidden" name="id" value="{vid}">'
        f'<input type="hidden" name="back" value="{e(href())}">'
        f'<button type="submit" aria-label="{e("Forget the view " + name)}" data-tip="{e("Forget this view")}">×</button></form></span>'
        for vid, name, vqs, uses in views)
    cur_qs = href().partition("?")[2]
    saveform = (f'<form class="bsave" method="post" action="/board/view" data-bviewsave>'
                f'<input type="hidden" name="t" value="{e(token)}"><input type="hidden" name="qs" value="{e(cur_qs)}">'
                f'<input type="hidden" name="game" value="{e(game)}"><input type="hidden" name="back" value="{e(href())}">'
                f'<input name="name" maxlength="40" placeholder="Name this view…" aria-label="Name this view">'
                f'<button type="submit">Save view</button></form>')
    viewsrow = (f'<div class="bviews" aria-label="Saved views"><span class="bv-l">Views</span>{vpills}{saveform}</div>')

    # -- toolbar
    searchf = (f'<form class="bfind" method="get" action="{e(back)}" role="search">'
               + "".join(f'<input type="hidden" name="{k}" value="{e(val)}">' for k, val in state.items()
                         if val and k not in ("q", "page") and not (k == "v" and val == "all"))
               + f'<input type="search" name="q" id="b-q" value="{e(q)}" placeholder="Find a card or listing…" '
                 f'aria-label="Find a card or listing" autocomplete="off">'
               + f'<button type="submit" aria-label="Search">→</button></form>')
    keys_hint = ('<span class="bkeys" aria-hidden="true"><kbd>j</kbd><kbd>k</kbd> move <kbd>x</kbd> pick '
                 '<kbd>p</kbd> pin <kbd>r</kbd> reviewed <kbd>/</kbd> find</span>')
    grptoggle = ('<span class="bgrp-toggle" role="group" aria-label="Grouping">'
                 + f'<a class="fchip{" on" if grp == "card" else ""}" href="{e(href(grp="card", print=""))}">By card</a>'
                 + f'<a class="fchip{" on" if grp == "list" else ""}" href="{e(href(grp="list"))}">One by one</a></span>')
    toolbar = (f'<div class="bbar"><div class="bbar1">{searchf}{studio}{grptoggle}'
               f'<button type="button" class="bdense" id="b-dense" aria-pressed="false" '
               f'data-tip="Tight rows fit more on screen" aria-label="Toggle tight rows">Tight</button>'
               f'<label class="ball"><input type="checkbox" id="b-all"> pick page</label>{keys_hint}</div>{viewsrow}</div>')

    # -- cards
    cells = []
    for key, status, d, vd in page_items:
        m = marks.get(key, "")
        labs = labels.get(key, [])
        qk = quote(key, safe="")
        back_href = href()
        pin_on = m == "pinned"
        rev_on = m == "reviewed"
        ctl = (f'<div class="bctl">'
               f'<input type="checkbox" class="bsel" name="keys" value="{e(key)}" form="bulkform" '
               f'aria-label="{e("Pick " + (d.get("title") or key))}">'
               f'<form method="post" action="/board/mark" class="bmk" data-bmark>'
               f'<input type="hidden" name="t" value="{e(token)}"><input type="hidden" name="keys" value="{e(key)}">'
               f'<input type="hidden" name="mark" value="{"none" if pin_on else "pinned"}">'
               f'<input type="hidden" name="back" value="{e(back_href)}">'
               f'<button type="submit" class="bpin{" on" if pin_on else ""}" '
               f'aria-label="{e("Unpin" if pin_on else "Pin to top")}" data-tip="{e("Unpin" if pin_on else "Pin to top")}">'
               f'{"Pinned" if pin_on else "Pin"}</button></form>'
               f'<form method="post" action="/board/mark" class="bmk" data-bmark>'
               f'<input type="hidden" name="t" value="{e(token)}"><input type="hidden" name="keys" value="{e(key)}">'
               f'<input type="hidden" name="mark" value="{"none" if rev_on else "reviewed"}">'
               f'<input type="hidden" name="back" value="{e(back_href)}">'
               f'<button type="submit" class=" brev{" on" if rev_on else ""}" '
               f'aria-label="{e("Unmark reviewed" if rev_on else "Mark reviewed")}" '
               f'data-tip="{e("Unmark reviewed" if rev_on else "Mark reviewed")}">'
               f'{"Reviewed ✓" if rev_on else "Reviewed"}</button></form></div>')
        labrow = ""
        if labs:
            chips = []
            for lname, c in labs:
                chips.append(f'<span class="blab c-{e(c)}">{e(lname)}'
                             f'<form method="post" action="/board/unlabel" class="blx" data-bunlabel>'
                             f'<input type="hidden" name="t" value="{e(token)}"><input type="hidden" name="keys" value="{e(key)}">'
                             f'<input type="hidden" name="label" value="{e(lname)}">'
                             f'<input type="hidden" name="back" value="{e(back_href)}">'
                             f'<button type="submit" aria-label="{e("Remove label " + lname)}" '
                             f'data-tip="{e("Remove this label")}">×</button></form></span>')
            labrow = f'<div class="blabs">{"".join(chips)}</div>'
        card = deal_card(key, d, vd, cards, M, now, ph, seen.get(key),
                         (sp or {}).get(d.get("printing_id") or "", "") if not is_lot(d) else "",
                         token=token, piled=piled, back=back)
        meta = []
        if key not in analyzed:
            meta.append(f'<span class="dpill p-new" data-tip="{e("Unchecked: the engine has not priced this listing yet")}">{NEW_ICON} Unchecked</span>')
        if d.get("grade"):
            gtxt = ((d.get("grade_company") or "") + " " + str(d.get("grade"))).strip()
            meta.append(f'<span class="cnd c-grade" data-tip="{e("Graded and slabbed: " + gtxt)}" title="{e("Graded: " + gtxt)}">{GRADE_ICON}<b>{e(gtxt)}</b></span>')
        elif key in cond_of:
            ccode, csrc = cond_of[key]
            where = "named in the listing title" if csrc == "title" else "from the seller's condition field"
            tip = COND_LABEL[ccode] + " - " + where
            meta.append(f'<span class="cnd c-{dict((c, col) for c, _, col in COND)[ccode]}" data-tip="{e(tip)}" title="{e(tip)}">'
                        f'{COND_ICON[ccode]}<b>{ccode}</b> {e(COND_LABEL[ccode])}</span>')
        else:
            meta.append(f'<span class="cnd c-none" data-tip="{e("Neither the title nor the seller names a condition")}" '
                        f'title="{e("Condition unstated")}">Condition unstated</span>')
        if key in pc_pct:
            meta.append(f'<span class="bpc" data-tip="{e("Asking price is " + str(round(-pc_pct[key])) + "% under the PriceCharting sold-market comp for this grade")}" '
                        f'title="{e("Under PriceCharting")}">Under PC −{round(-pc_pct[key])}%</span>')
        gi = group_info.get(key)
        if gi:
            cnt, lo, hi, gk = gi
            span = f"{M(lo)}–{M(hi)}" if hi > lo else M(lo) if hi >= lo else "prices vary"
            meta.append(f'<a class="bgrp" href="{e(href(print=gk, grp="list", page=""))}" '
                        f'data-tip="{e("See every ask of this card")}" title="{e("See every ask")}">{cnt} asks · {e(span)}</a>')
        metarow = f'<div class="bmeta">{"".join(meta)}</div>'
        iid = item_id_of(key, d)
        has_shot = bool(iid and ph.get(iid))
        art_tag = ("Lot photo" if is_lot(d) else "Official art" if _listing_art(d)
                   else "Seller photo" if has_shot else "No photo yet")
        arttag = f'<span class="bart" data-tip="{e("The picture shown: " + art_tag.lower())}">{e(art_tag)}</span>'
        cells.append(f'<div class="bcell{" mk-pinned" if pin_on else ""}{" mk-reviewed" if rev_on else ""}{" b-new" if key not in analyzed else ""}" '
                     f'data-key="{e(key)}" tabindex="0">{ctl}{metarow}{arttag}{card}{labrow}</div>')

    # -- pager
    pager = ""
    if pages > 1:
        lo, hi = max(1, page - 2), min(pages, page + 2)
        nums = []
        if lo > 1:
            nums.append(f'<a href="{e(href(page=""))}">1</a>' + ("<span>…</span>" if lo > 2 else ""))
        nums += [f'<b aria-current="page">{n}</b>' if n == page else f'<a href="{e(href(page=str(n)))}">{n}</a>'
                 for n in range(lo, hi + 1)]
        if hi < pages:
            nums.append(("<span>…</span>" if hi < pages - 1 else "") + f'<a href="{e(href(page=str(pages)))}">{pages}</a>')
        prev = f'<a class="bp-prev" href="{e(href(page=str(page - 1) if page > 2 else ""))}">←</a>' if page > 1 else ""
        nxt = f'<a class="bp-next" href="{e(href(page=str(page + 1)))}">→</a>' if page < pages else ""
        pager = f'<nav class="bpager" aria-label="Pages">{prev}{"".join(nums)}{nxt}</nav>'

    # -- bulk bar
    swatches = "".join(
        f'<label class="bsw c-{c}" data-tip="{e(c.capitalize() + " label")}"><input type="radio" name="color" value="{c}"'
        f'{" checked" if c == "red" else ""} aria-label="{e(c.capitalize() + " label")}"><i></i></label>'
        for c, _hex in COLORS)
    bulk = (f'<form id="bulkform" method="post" action="/board/bulk" class="bbulk" aria-label="Handle picked listings">'
            f'<input type="hidden" name="t" value="{e(token)}"><input type="hidden" name="back" value="{e(href())}">'
            f'<input type="hidden" name="keys" id="b-keys" value="">'
            f'<b class="bn"><span id="b-n">0</span> picked</b>'
            f'<input class="bl-in" name="label" maxlength="40" placeholder="Label these…" aria-label="Label for the picked listings">'
            f'<span class="bsws" role="radiogroup" aria-label="Label color">{swatches}</span>'
            f'<button type="submit" name="act" value="label">Label</button>'
            f'<button type="submit" name="act" value="pinned">Pin to top</button>'
            f'<button type="submit" name="act" value="reviewed">Mark reviewed</button>'
            f'<button type="submit" name="act" value="hidden" class="bhide">Hide</button>'
            f'<button type="submit" name="act" value="none" class="bghost">Clear marks</button>'
            f'<button type="button" id="b-clear" class="bghost">Let go</button></form>')

    tick = (f'<form method="post" action="/engine/tick" class="inline btick"><input type="hidden" name="t" value="{e(token)}">'
            f'<button>Check for new listings now</button> <a class="small" href="{e(back)}/engine">engine details</a></form>')

    other_note = ""
    if not pool:
        other = "ygo" if game == "pkm" else "pkm"
        n_other = len(open_listings(st, M, other))
        if n_other:
            other_note = (f'<p class="empty">{n_other:,} live eBay listings are under the other game. '
                          f'Switch the game in the top bar.</p>')

    if grp == "card":
        sub = f'{n_groups:,} cards from {n_listings:,} live listings. One tile per card, its best ask leads.'
    else:
        sub = f'{total:,} of {len(items):,} public listings match. A Skip is still shown. Nothing here was deleted.'
    printnote = ""
    if print_id:
        printnote = (f'<p class="bprintnote">Every ask of one card. '
                     f'<a href="{e(href(print="", grp="card", page=""))}">Back to all cards</a></p>')
    head = (f'<div class="bhead"><h1>Listings</h1>'
            f'<p class="sub">{sub}</p>{tick}</div>')
    empty = ('<p class="empty">Nothing matches these filters. Widen a lane, or clear the search.</p>'
             if not cells else "")
    grid = (printnote + f'<div class="deals bgrid" id="b-grid">{"".join(cells)}</div>') if cells else printnote
    drawer = ('<div class="bshade" id="b-shade" hidden></div>'
              '<aside class="bdrawer" id="b-drawer" aria-label="Listing peek" aria-hidden="true">'
              '<div class="bd-head"><b id="bd-title">Listing</b>'
              '<a id="bd-full" href="#" class="small">Full page ↗</a>'
              '<button type="button" id="bd-close" aria-label="Close the peek" data-tip="Close">×</button></div>'
              '<div class="bd-body" id="bd-body"></div></aside>')
    toast = '<div class="btoast" id="b-toast" role="status" hidden><span id="bt-msg"></span><button type="button" id="bt-undo" hidden>Undo</button></div>'
    return (head + toolbar
            + f'<div class="blayout">{facets}<section class="bmain" aria-label="Listings">{other_note}{empty}{grid}{pager}</section></div>'
            + bulk + drawer + toast)


# -- styles -----------------------------------------------------------------------------------------------------------

BOARD_CSS = """
.bhead{display:flex;flex-wrap:wrap;align-items:baseline;gap:8px 18px;margin-bottom:10px}
.bhead h1{margin:0}.bhead .sub{margin:0}.btick{margin-left:auto}
.bbar{position:sticky;top:58px;z-index:40;background:var(--bg);border:1px solid var(--fg);
 box-shadow:3px 3px 0 color-mix(in srgb,var(--fg) 22%,transparent);padding:10px 12px;margin:0 0 14px}
.bbar1{display:flex;flex-wrap:wrap;align-items:center;gap:8px 14px}
.bfind{display:flex;align-items:center;border:1px solid var(--fg);background:var(--surface)}
.bfind input{border:0;background:none;padding:7px 10px;min-width:210px;font:inherit;color:var(--fg)}
.bfind input:focus{outline:2px solid #2f6ce0;outline-offset:-2px}
.bfind button{border:0;border-left:1px solid var(--fg);background:var(--fg);color:var(--bg);padding:7px 11px;cursor:pointer;font:inherit}
.bsort{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.bs-l,.bv-l{font-size:11px;text-transform:uppercase;letter-spacing:.09em;opacity:.65}
.bs-list{display:flex;gap:6px;list-style:none;margin:0;padding:0;flex-wrap:wrap}
.bs-pill{display:inline-flex;align-items:center;gap:5px;border:1px solid var(--fg);background:var(--surface);
 padding:3px 6px;font-size:12.5px;cursor:grab;user-select:none}
.bs-pill.drag{opacity:.45;border-style:dashed}
.bs-pill.over{outline:2px solid #2f6ce0;outline-offset:1px}
.bs-n{display:inline-grid;place-items:center;width:16px;height:16px;background:var(--fg);color:var(--bg);font-size:10.5px}
.bs-dir,.bs-x{text-decoration:none;color:var(--fg);padding:0 3px;font-weight:700;line-height:1}
.bs-dir:hover{color:#2f6ce0}.bs-x:hover{color:#e05252}
.bs-add{font:inherit;font-size:12.5px;border:1px dashed var(--fg);background:var(--surface);color:var(--fg);padding:3px 5px}
.bdense{font:inherit;font-size:12px;border:1px solid var(--fg);background:var(--surface);color:var(--fg);padding:5px 9px;cursor:pointer}
.bdense[aria-pressed="true"]{background:var(--fg);color:var(--bg)}
.ball{display:inline-flex;align-items:center;gap:6px;font-size:12.5px;cursor:pointer}
.bkeys{font-size:11px;opacity:.6;margin-left:auto;white-space:nowrap}
.bkeys kbd{border:1px solid var(--line);border-bottom-width:2px;padding:0 4px;font-size:10.5px;background:var(--surface)}
.bviews{display:flex;align-items:center;gap:6px;flex-wrap:wrap;margin-top:8px;padding-top:8px;border-top:1px dashed var(--line)}
.bview{display:inline-flex;align-items:center;border:1px solid var(--fg);background:var(--surface);font-size:12.5px}
.bview.on{background:var(--fg)}
.bview.on>a{color:var(--bg)}
.bview>a{color:var(--fg);text-decoration:none;padding:3px 8px}
.bview>a:hover{text-decoration:underline}
.bvx{display:inline-flex;margin:0}
.bvx button{border:0;border-left:1px solid var(--line);background:none;color:inherit;padding:3px 6px;cursor:pointer;font:inherit;line-height:1}
.bvx button:hover{color:#e05252}
.bsave{display:inline-flex;gap:0;margin-left:auto}
.bsave input{font:inherit;font-size:12.5px;border:1px solid var(--fg);border-right:0;background:var(--surface);color:var(--fg);padding:3px 8px;width:150px}
.bsave button{font:inherit;font-size:12.5px;border:1px solid var(--fg);background:var(--fg);color:var(--bg);padding:3px 9px;cursor:pointer}
.blayout{display:grid;grid-template-columns:236px minmax(0,1fr);gap:18px;align-items:start}
.brail{position:sticky;top:132px;max-height:calc(100vh - 150px);overflow-y:auto;padding-right:6px;
 display:flex;flex-direction:column;gap:12px;scrollbar-width:thin;scrollbar-color:var(--fg) transparent}
.brail::-webkit-scrollbar{width:9px}
.brail::-webkit-scrollbar-track{background:transparent}
.brail::-webkit-scrollbar-thumb{background:var(--fg);border:2px solid var(--bg)}
.brail::-webkit-scrollbar-thumb:hover{background:#2f6ce0}
.brail .rail{display:flex;flex-direction:column;gap:6px}
.fsec{border:1px solid var(--line);background:var(--surface);padding:9px 10px}
.fsec h3{margin:0 0 7px;font-size:11px;text-transform:uppercase;letter-spacing:.09em;opacity:.65}
.fsec .fchip{display:flex;align-items:center;gap:7px;padding:4px 7px;margin:2px 0;font-size:13px;
 color:var(--fg);text-decoration:none;border:1px solid transparent}
.fsec .fchip:hover{border-color:var(--fg)}
.fsec .fchip.on{background:var(--fg);color:var(--bg);border-color:var(--fg)}
.fc-n{margin-left:auto;font-size:11.5px;opacity:.75;font-variant-numeric:tabular-nums}
.fc-dot{width:9px;height:9px;border-radius:50%;background:var(--lc,var(--fg));flex:none}
.c-red{--lc:#e05252}.c-blue{--lc:#2f6ce0}.c-gold{--lc:#d4af6a}.c-green{--lc:#2fbf71}
.c-amber{--lc:#e6a23c}.c-ink{--lc:var(--fg)}.c-violet{--lc:#b05fd6}.c-teal{--lc:#3fb8c9}
.bprice{display:flex;gap:5px}
.bprice input{width:100%;min-width:0;font:inherit;font-size:12.5px;border:1px solid var(--line);background:var(--bg);color:var(--fg);padding:4px 6px}
.bprice input:focus{outline:2px solid #2f6ce0;outline-offset:-1px}
.bprice button{font:inherit;font-size:11.5px;border:1px solid var(--fg);background:var(--fg);color:var(--bg);padding:4px 7px;cursor:pointer}
.bgrid{position:relative}
.bcell{position:relative;outline:none}
.bcell:focus-visible{outline:2px solid #2f6ce0;outline-offset:3px}
.bcell .deal{height:100%}
.bctl{position:absolute;top:-9px;right:8px;z-index:5;display:flex;align-items:center;gap:5px}
.bsel{width:17px;height:17px;accent-color:#2f6ce0;cursor:pointer;margin:0}
.bmk{display:inline-flex;margin:0}
.bpin,.brev{font:inherit;font-size:10.5px;text-transform:uppercase;letter-spacing:.06em;border:1px solid var(--fg);
 background:var(--surface);color:var(--fg);padding:2px 7px;cursor:pointer;line-height:1.5}
.bpin:hover,.brev:hover{transform:translateY(-1px)}
.bpin.on{background:#d4af6a;border-color:var(--fg);color:#1c1710}
.brev.on{background:#2fbf71;border-color:var(--fg);color:#0e1a12}
.bcell.mk-pinned .deal{border-color:var(--fg);box-shadow:3px 3px 0 #d4af6a}
.bcell.mk-reviewed .deal{opacity:.72}
.bcell.mk-reviewed .deal:hover{opacity:1}
.bcell.picked .deal{outline:2px solid #2f6ce0;outline-offset:2px}
.blabs{display:flex;flex-wrap:wrap;gap:5px;margin-top:6px}
.blab{display:inline-flex;align-items:center;gap:4px;font-size:11.5px;border:1px solid var(--fg);
 background:color-mix(in srgb,var(--lc,var(--fg)) 16%,var(--surface));color:var(--fg);padding:1px 4px 1px 8px}
.blab::before{content:"";width:7px;height:7px;border-radius:50%;background:var(--lc,var(--fg))}
.blx{display:inline-flex;margin:0}
.blx button{border:0;background:none;color:inherit;font:inherit;line-height:1;padding:1px 4px;cursor:pointer}
.blx button:hover{color:#e05252}
body.b-tight .bgrid{grid-template-columns:repeat(auto-fill,minmax(min(360px,100%),1fr));gap:10px}
body.b-tight .deal{padding:10px;grid-template-columns:84px minmax(0,1fr);gap:10px}
body.b-tight .dwhy,body.b-tight .dmore,body.b-tight .dsub{display:none}
body.b-tight .dtitle{font-size:14px}
.bpager{display:flex;align-items:center;gap:6px;justify-content:center;margin:20px 0 8px;font-size:13.5px}
.bpager a,.bpager b{display:inline-grid;place-items:center;min-width:30px;height:30px;padding:0 7px;
 border:1px solid var(--fg);text-decoration:none;color:var(--fg);background:var(--surface)}
.bpager b{background:var(--fg);color:var(--bg)}
.bpager a:hover{background:#2f6ce0;color:#fff;border-color:var(--fg)}
.bpager span{opacity:.5}
.bbulk{position:fixed;left:50%;bottom:16px;transform:translateX(-50%);z-index:60;display:none;align-items:center;gap:9px;
 flex-wrap:wrap;justify-content:center;max-width:min(96vw,980px);background:var(--fg);color:var(--bg);
 border:1px solid var(--fg);box-shadow:4px 4px 0 color-mix(in srgb,var(--fg) 30%,transparent);padding:10px 14px;margin:0}
body.b-sel .bbulk{display:flex}
.bbulk .bn{font-size:13px;white-space:nowrap}
.bbulk .bn span{font-size:16px}
.bbulk input.bl-in{font:inherit;font-size:13px;border:1px solid var(--bg);background:var(--surface);color:var(--fg);padding:5px 8px;width:150px}
.bbulk button{font:inherit;font-size:12.5px;border:1px solid var(--bg);background:var(--bg);color:var(--fg);padding:5px 10px;cursor:pointer}
.bbulk button:hover{background:#d4af6a;color:#1c1710;border-color:#d4af6a}
.bbulk .bhide:hover{background:#e05252;color:#fff;border-color:#e05252}
.bbulk .bghost{background:none;color:var(--bg);border-style:dashed}
.bsws{display:inline-flex;gap:4px;align-items:center}
.bsw{position:relative;width:18px;height:18px;cursor:pointer;display:inline-block}
.bsw input{position:absolute;opacity:0;inset:0;margin:0;cursor:pointer}
.bsw i{position:absolute;inset:2px;border-radius:50%;background:var(--lc,var(--bg));border:1px solid var(--bg)}
.bsw input:checked+i{outline:2px solid var(--bg);outline-offset:1px}
.bsw input:focus-visible+i{outline:2px solid #2f6ce0;outline-offset:1px}
.bshade{position:fixed;inset:0;background:color-mix(in srgb,var(--fg) 38%,transparent);z-index:70}
.bdrawer{position:fixed;top:0;right:0;bottom:0;width:min(620px,94vw);z-index:80;background:var(--bg);
 border-left:2px solid var(--fg);box-shadow:-6px 0 0 color-mix(in srgb,var(--fg) 18%,transparent);
 transform:translateX(103%);visibility:hidden;transition:transform .22s ease,visibility 0s .22s;display:flex;flex-direction:column}
.bdrawer.open{transform:translateX(0);visibility:visible;transition:transform .22s ease}
.bd-head{display:flex;align-items:center;gap:12px;padding:12px 16px;border-bottom:1px solid var(--fg);background:var(--surface)}
.bd-head b{font-size:15px;flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.bd-head a{color:var(--fg);white-space:nowrap}
.bd-head button{border:1px solid var(--fg);background:none;color:var(--fg);font-size:17px;line-height:1;width:30px;height:30px;cursor:pointer}
.bd-head button:hover{background:#e05252;color:#fff;border-color:#e05252}
.bd-body{overflow-y:auto;padding:16px;flex:1;scrollbar-width:thin;scrollbar-color:var(--fg) transparent}
.bd-body::-webkit-scrollbar{width:10px}
.bd-body::-webkit-scrollbar-track{background:var(--surface)}
.bd-body::-webkit-scrollbar-thumb{background:var(--fg);border:2px solid var(--surface)}
.bd-body::-webkit-scrollbar-thumb:hover{background:#2f6ce0}
.bd-body .crumb-back{display:none}
.btoast{position:fixed;left:50%;bottom:76px;transform:translateX(-50%);z-index:90;display:flex;align-items:center;gap:12px;
 background:var(--surface);color:var(--fg);border:1px solid var(--fg);box-shadow:3px 3px 0 color-mix(in srgb,var(--fg) 25%,transparent);
 padding:9px 14px;font-size:13.5px;max-width:92vw}
.btoast button{font:inherit;font-size:12.5px;border:1px solid var(--fg);background:var(--fg);color:var(--bg);padding:3px 10px;cursor:pointer}
.btoast button:hover{background:#2fbf71;border-color:var(--fg);color:#0e1a12}
.bmeta{display:flex;flex-wrap:wrap;align-items:center;gap:5px;margin:0 0 6px}
body{overflow-x:clip}
html{overflow-x:clip}
.cnd{display:inline-flex;align-items:center;gap:4px;font-size:11px;line-height:1.5;border:1px solid var(--lc,var(--fg));
 color:var(--fg);background:color-mix(in srgb,var(--lc,var(--fg)) 13%,var(--surface));padding:1px 6px;white-space:nowrap}
.cnd svg{color:var(--lc,var(--fg));flex:none}
.cnd b{font-size:10.5px;letter-spacing:.03em}
.cnd.c-grade{--lc:#d4af6a}
.cnd.c-none{border-style:dashed;opacity:.7;background:none}
.bpc{display:inline-flex;align-items:center;font-size:11px;font-weight:650;border:1px solid var(--fg);
 background:#2fbf71;color:#0e1a12;padding:1px 7px;white-space:nowrap}
.bgrp{display:inline-flex;align-items:center;font-size:11px;border:1px solid var(--fg);background:var(--surface);
 color:var(--fg);text-decoration:none;padding:1px 7px;white-space:nowrap}
.bgrp:hover{background:#d4af6a;color:#1c1710}
.bart{position:absolute;left:14px;top:44px;z-index:4;font-size:9px;text-transform:uppercase;letter-spacing:.07em;
 background:var(--fg);color:var(--bg);padding:1px 5px;pointer-events:none;opacity:.92}
.bcell.b-new .deal{border-style:dashed}
.bcell.b-new .dpill{display:none}
.dpill.p-new{display:inline-flex;align-items:center;gap:5px;border:1px dashed var(--fg);background:var(--surface);
 color:var(--fg);font-size:12px;padding:2px 8px}
.dpill.p-new svg{width:13px;height:13px}
.rail .lane.l-new{--lane:var(--fg2)}
.fc-cnd{display:inline-flex;align-items:center;color:var(--lc,var(--fg))}
.fc-cnd svg{width:12px;height:12px}
.fchip.on .fc-cnd{color:var(--bg)}
.bgrp-toggle{display:inline-flex;border:1px solid var(--fg)}
.bgrp-toggle .fchip{border:0;margin:0;padding:5px 9px;font-size:12.5px;color:var(--fg);text-decoration:none}
.bgrp-toggle .fchip.on{background:var(--fg);color:var(--bg)}
.bprintnote{margin:0 0 10px;font-size:13px}
.bprintnote a{color:var(--fg)}
body.b-tight .bmeta{margin-bottom:4px}
body.b-tight .bart{display:none}
@media(max-width:900px){
 .blayout{grid-template-columns:1fr}
 .brail{position:static;max-height:none;overflow:visible;flex-direction:row;flex-wrap:wrap}
 .brail .rail{flex-direction:row;flex-wrap:wrap;width:100%}
 .fsec{flex:1 1 160px}
 .bbar{top:52px}
 .bkeys{display:none}
 .bsave{margin-left:0}
}
"""

# -- script -----------------------------------------------------------------------------------------------------------

BOARD_JS = """
(function(){
  var grid=document.getElementById('b-grid');
  if(!grid)return;
  var path=location.pathname, KEY='cm.board.'+path;
  var TOKEN=(document.querySelector('#bulkform [name=t]')||{}).value||'';
  var $=function(id){return document.getElementById(id)};
  function post(url,data){
    data.append('ajax','1');
    return fetch(url,{method:'POST',body:data,headers:{'Accept':'application/json'}}).then(function(r){return r.json()});
  }
  function fd(form){var d=new FormData(form);return d}
  var toastT=null,undoFn=null;
  function toast(msg,undo){
    var t=$('b-toast');if(!t)return;
    $('bt-msg').textContent=msg;
    var u=$('bt-undo');undoFn=undo||null;u.hidden=!undo;
    t.hidden=false;
    if(toastT)clearTimeout(toastT);
    toastT=setTimeout(function(){t.hidden=true;undoFn=null},6000);
  }
  $('bt-undo').addEventListener('click',function(){
    if(!undoFn)return;var f=undoFn;undoFn=null;$('b-toast').hidden=true;f();
  });
  function reload(){location.reload()}

  /* memory: last filters per board, and row density */
  try{
    if(!location.search){var last=localStorage.getItem(KEY);if(last){location.replace(path+'?'+last);return}}
    else{var qs=location.search.slice(1);if(qs)localStorage.setItem(KEY,qs)}
    if(localStorage.getItem('cm.board.tight')==='1'){document.body.classList.add('b-tight');$('b-dense').setAttribute('aria-pressed','true')}
  }catch(err){}
  $('b-dense').addEventListener('click',function(){
    var on=document.body.classList.toggle('b-tight');
    this.setAttribute('aria-pressed',on?'true':'false');
    try{localStorage.setItem('cm.board.tight',on?'1':'0')}catch(err){}
  });

  /* selection + bulk bar */
  function picked(){return Array.prototype.slice.call(grid.querySelectorAll('.bsel:checked'))}
  function paintSel(){
    var n=picked().length;
    document.body.classList.toggle('b-sel',n>0);
    $('b-n').textContent=String(n);
    grid.querySelectorAll('.bcell').forEach(function(c){
      c.classList.toggle('picked',c.querySelector('.bsel').checked);
    });
  }
  grid.addEventListener('change',function(ev){if(ev.target.classList.contains('bsel'))paintSel()});
  $('b-all').addEventListener('change',function(){
    var on=this.checked;
    grid.querySelectorAll('.bsel').forEach(function(b){b.checked=on});
    paintSel();
  });
  $('b-clear').addEventListener('click',function(){
    grid.querySelectorAll('.bsel').forEach(function(b){b.checked=false});
    $('b-all').checked=false;paintSel();
  });
  $('bulkform').addEventListener('submit',function(ev){
    ev.preventDefault();
    var act=(ev.submitter&&ev.submitter.value)||'pinned';
    var keys=picked().map(function(b){return b.value});
    if(!keys.length)return;
    var d=fd(this);d.set('keys',keys.join('\\n'));d.set('act',act);
    var n=keys.length;
    post('/board/bulk',d).then(function(j){
      if(!j.ok){toast('That did not take. Reload and try again.');return}
      if(act==='label'){
        var lab=d.get('label'),col=d.get('color')||'ink';
        keys.forEach(function(k){addChip(k,lab,col)});
        toast('Labeled '+n+' listing'+(n>1?'s':'')+'.',function(){
          var u=new FormData();u.append('t',d.get('t'));u.append('keys',keys.join('\\n'));u.append('label',lab);
          post('/board/unlabel',u).then(reload);
        });
      }else{
        applyMarkDom(keys,act);
        var word={pinned:'Pinned',reviewed:'Marked reviewed',hidden:'Hid',none:'Cleared marks on'}[act]||'Handled';
        toast(word+' '+n+' listing'+(n>1?'s':'')+'.',function(){
          var u=new FormData();u.append('t',d.get('t'));u.append('restore',JSON.stringify(j.prev||{}));
          post('/board/mark',u).then(reload);
        });
      }
      grid.querySelectorAll('.bsel').forEach(function(b){b.checked=false});
      $('b-all').checked=false;paintSel();
    }).catch(function(){toast('That did not take. Reload and try again.')});
  });
  function cellOf(k){return grid.querySelector('.bcell[data-key="'+CSS.escape(k)+'"]')}
  function applyMarkDom(keys,mark){
    keys.forEach(function(k){
      var c=cellOf(k);if(!c)return;
      c.classList.remove('mk-pinned','mk-reviewed');
      var pin=c.querySelector('.bpin'),rev=c.querySelector('.brev');
      if(mark==='pinned'){c.classList.add('mk-pinned');grid.prepend(c);if(pin){pin.classList.add('on');pin.textContent='Pinned';pin.parentNode.querySelector('[name=mark]').value='none'}}
      if(mark==='reviewed'){c.classList.add('mk-reviewed');if(rev){rev.classList.add('on');rev.textContent='Reviewed ✓';rev.parentNode.querySelector('[name=mark]').value='none'}}
      if(mark==='none'){
        if(pin){pin.classList.remove('on');pin.textContent='Pin';pin.parentNode.querySelector('[name=mark]').value='pinned'}
        if(rev){rev.classList.remove('on');rev.textContent='Reviewed';rev.parentNode.querySelector('[name=mark]').value='reviewed'}
      }
      if(mark==='hidden')c.remove();
    });
    if(mark==='hidden'){var s=document.querySelector('.bhead .sub');if(s)s.textContent='Hidden listings stay under Your marks → Hidden.'}
  }
  function addChip(k,lab,col){
    var c=cellOf(k);if(!c)return;
    var row=c.querySelector('.blabs');
    if(!row){row=document.createElement('div');row.className='blabs';c.appendChild(row)}
    if(Array.prototype.some.call(row.querySelectorAll('.blab'),function(x){return x.firstChild&&x.textContent.indexOf(lab)===0}))return;
    var span=document.createElement('span');span.className='blab c-'+col;span.textContent=lab;
    var f=document.createElement('form');f.method='post';f.action='/board/unlabel';f.className='blx';f.setAttribute('data-bunlabel','');
    [['t',TOKEN],['keys',k],['label',lab],['back',location.pathname+location.search]].forEach(function(p){
      var i=document.createElement('input');i.type='hidden';i.name=p[0];i.value=p[1];f.appendChild(i);
    });
    var b=document.createElement('button');b.type='submit';b.textContent='×';b.setAttribute('aria-label','Remove label '+lab);f.appendChild(b);
    span.appendChild(f);row.appendChild(span);
  }

  /* per-card marks and label removal, optimistic */
  document.addEventListener('submit',function(ev){
    var f=ev.target;
    if(f.hasAttribute('data-bmark')){
      ev.preventDefault();
      var d=fd(f),k=d.get('keys'),mark=d.get('mark');
      post('/board/mark',d).then(function(j){
        if(!j.ok)return;
        applyMarkDom([k],mark);
        toast(mark==='none'?'Mark cleared.':'Marked.',function(){
          var u=new FormData();u.append('t',d.get('t'));u.append('restore',JSON.stringify(j.prev||{}));
          post('/board/mark',u).then(reload);
        });
      });
    }else if(f.hasAttribute('data-bunlabel')){
      ev.preventDefault();
      var d2=fd(f),lab=d2.get('label'),k2=d2.get('keys');
      post('/board/unlabel',d2).then(function(j){
        if(!j.ok)return;
        var c=cellOf(k2);
        if(c)c.querySelectorAll('.blab').forEach(function(x){if(x.textContent.indexOf(lab)===0)x.remove()});
        toast('Label removed.',function(){
          var u=new FormData();u.append('t',d2.get('t'));u.append('keys',k2);u.append('label',lab);u.append('color','ink');
          post('/board/label',u).then(reload);
        });
      });
    }else if(f.hasAttribute('data-bviewsave')){
      ev.preventDefault();
      var d3=fd(f);
      if(!d3.get('name').trim()){toast('Give the view a name first.');return}
      post('/board/view',d3).then(function(j){if(j.ok)reload()});
    }else if(f.hasAttribute('data-bviewdel')){
      ev.preventDefault();
      var d4=fd(f);
      post('/board/viewdel',d4).then(function(j){
        if(!j.ok)return;
        f.parentNode.remove();
        toast('View forgotten.',function(){
          var u=new FormData();u.append('t',d4.get('t'));u.append('id',j.view.id);u.append('name',j.view.name);
          u.append('qs',j.view.qs);u.append('game',j.view.game);
          post('/board/view',u).then(reload);
        });
      });
    }
  });

  /* sort studio: flip and remove are links; drag reorders; select adds */
  var list=$('bs-list');
  function navSort(order){
    var u=new URL(location.href);
    if(order&&order!=='lane:a,profit:d')u.searchParams.set('sort',order);else u.searchParams.delete('sort');
    u.searchParams.delete('page');
    location.href=u.pathname+u.search;
  }
  if(list){
    var dragEl=null;
    list.addEventListener('dragstart',function(ev){
      dragEl=ev.target.closest('.bs-pill');if(dragEl)dragEl.classList.add('drag');
    });
    list.addEventListener('dragend',function(){
      if(dragEl)dragEl.classList.remove('drag');
      list.querySelectorAll('.over').forEach(function(x){x.classList.remove('over')});
      dragEl=null;
    });
    list.addEventListener('dragover',function(ev){
      ev.preventDefault();
      var t=ev.target.closest('.bs-pill');
      list.querySelectorAll('.over').forEach(function(x){x.classList.remove('over')});
      if(t&&t!==dragEl)t.classList.add('over');
    });
    list.addEventListener('drop',function(ev){
      ev.preventDefault();
      var t=ev.target.closest('.bs-pill');
      if(!dragEl||!t||t===dragEl)return;
      var kids=Array.prototype.slice.call(list.children);
      if(kids.indexOf(dragEl)<kids.indexOf(t))t.after(dragEl);else t.before(dragEl);
      var order=Array.prototype.map.call(list.children,function(li){return li.getAttribute('data-k')+':'+li.getAttribute('data-d')}).join(',');
      navSort(order);
    });
  }
  var add=$('bs-add');
  if(add)add.addEventListener('change',function(){
    if(!this.value)return;
    var cur=Array.prototype.map.call(list.children,function(li){return li.getAttribute('data-k')+':'+li.getAttribute('data-d')});
    var def={lane:'a',profit:'d',ask:'a',value:'d',fresh:'d',ends:'a',pc:'a'}[this.value]||'d';
    cur.push(this.value+':'+def);
    navSort(cur.join(','));
  });

  /* find box: enter or a short pause */
  var qin=$('b-q'),qT=null;
  if(qin){
    qin.addEventListener('input',function(){
      if(qT)clearTimeout(qT);
      qT=setTimeout(function(){qin.form.requestSubmit()},650);
    });
  }

  /* peek drawer */
  var drawer=$('b-drawer'),shade=$('b-shade'),lastFocus=null;
  function closeDrawer(){
    drawer.classList.remove('open');drawer.setAttribute('aria-hidden','true');shade.hidden=true;
    if(lastFocus)lastFocus.focus();
  }
  function openDrawer(href,title,from){
    lastFocus=from||null;
    $('bd-title').textContent=title||'Listing';
    $('bd-full').setAttribute('href',href);
    var body=$('bd-body');
    while(body.firstChild)body.removeChild(body.firstChild);
    var loading=document.createElement('p');loading.className='muted';loading.textContent='Opening…';body.appendChild(loading);
    drawer.classList.add('open');drawer.setAttribute('aria-hidden','false');shade.hidden=false;
    $('bd-close').focus();
    fetch(href,{headers:{'Accept':'text/html'}}).then(function(r){return r.text()}).then(function(html){
      var doc=new DOMParser().parseFromString(html,'text/html');
      var main=doc.querySelector('main.page');
      while(body.firstChild)body.removeChild(body.firstChild);
      if(main){
        Array.prototype.slice.call(main.childNodes).forEach(function(n){body.appendChild(document.importNode(n,true))});
        var h=main.querySelector('h1');if(h)$('bd-title').textContent=h.textContent;
      }else{body.textContent='Could not open the peek. Use Full page.'}
    }).catch(function(){
      while(body.firstChild)body.removeChild(body.firstChild);
      body.textContent='Could not open the peek. Use Full page.';
    });
  }
  grid.addEventListener('click',function(ev){
    var a=ev.target.closest('a.dtitle, .dm-line a[href^="/opp/"]');
    if(!a)return;
    ev.preventDefault();
    openDrawer(a.getAttribute('href'),a.textContent,a);
  });
  $('bd-close').addEventListener('click',closeDrawer);
  shade.addEventListener('click',closeDrawer);

  /* keys: j k move, x pick, p pin, r reviewed, / find, esc close */
  document.addEventListener('keydown',function(ev){
    var tag=(ev.target.tagName||'').toLowerCase();
    if(tag==='input'||tag==='select'||tag==='textarea'){
      if(ev.key==='Escape')ev.target.blur();
      return;
    }
    if(ev.key==='Escape'){closeDrawer();return}
    if(ev.key==='/'){ev.preventDefault();if(qin)qin.focus();return}
    var cells=Array.prototype.slice.call(grid.querySelectorAll('.bcell'));
    if(!cells.length)return;
    var cur=document.activeElement&&document.activeElement.classList&&document.activeElement.classList.contains('bcell')?document.activeElement:null;
    var i=cur?cells.indexOf(cur):-1;
    if(ev.key==='j'||ev.key==='ArrowDown'){ev.preventDefault();cells[Math.min(cells.length-1,i+1)].focus()}
    else if(ev.key==='k'||ev.key==='ArrowUp'){ev.preventDefault();cells[Math.max(0,i<0?0:i-1)].focus()}
    else if(cur&&(ev.key==='x'||ev.key===' ')){ev.preventDefault();var b=cur.querySelector('.bsel');b.checked=!b.checked;paintSel()}
    else if(cur&&ev.key==='p'){var pf=cur.querySelector('.bpin');if(pf)pf.form.requestSubmit()}
    else if(cur&&ev.key==='r'){var rf=cur.querySelector('.brev');if(rf)rf.form.requestSubmit()}
    else if(cur&&ev.key==='Enter'){var t=cur.querySelector('a.dtitle');if(t)openDrawer(t.getAttribute('href'),t.textContent,cur)}
  });
})();
"""