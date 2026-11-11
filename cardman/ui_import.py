"""Import page: drop saved sold pages, then confirm which exact printing each sale was."""
from __future__ import annotations

import html
from urllib.parse import quote

from . import charts
from .sold_import import batch_rows, batches


def e(v) -> str:
    return html.escape("" if v is None else str(v), quote=True)


GUIDE = (
    ("eBay", "Search the card on ebay.com, tick <b>Sold items</b> in the left filters, then press <kbd>⌘S</kbd> and save "
             "as <b>Webpage, Complete</b>."),
    ("130point", "Search the card on 130point.com (it shows the real price of accepted Best Offers), then <kbd>⌘S</kbd>."),
    ("Yahoo! Auctions", "Search on auctions.yahoo.co.jp, open <b>落札相場</b> (closed auctions), then <kbd>⌘S</kbd>."),
    ("Mercari", "Search on jp.mercari.com, filter <b>売り切れ</b> (sold), scroll so the results load, then <kbd>⌘S</kbd> as "
                "<b>Webpage, Complete</b>. Mercari shows no sale date, so the day you save is used."),
)


def _label(p) -> str:
    return " · ".join(x for x in (p.set_code or "no code", p.rarity, p.edition, p.product_name) if x)


def _options(title: str, catalog, limit: int = 15) -> list[tuple[str, str, float]]:
    from .identity import edition_variants
    from .intel import analyze
    an = analyze(title, catalog)
    out = []
    for c in an.identity.candidates:
        vs = edition_variants(c, catalog)
        for v in vs:
            out.append((v.id, _label(v), an.weights.get(c.id, 0) / len(vs)))
    out.sort(key=lambda x: -x[2])
    return out[:limit]


def _status(o, confirmed, n_cands: int) -> tuple[str, str]:
    t = o.evidence_type.value
    if o.is_lot or o.quantity > 1:
        return "muted", "Lot: kept as evidence, never a single-card price"
    if t == "SELLER_CLAIM":
        return "warnt", "Best Offer: real price hidden; kept only as an upper bound"
    if confirmed:
        return "good", "Confirmed by you"
    if n_cands == 1:
        return "good", "Identified from the evidence"
    if n_cands == 0:
        return "muted", "No card matched the title"
    return "warnt", f"Could be {n_cands} printings: pick the right one to use this sale"


def view_import(st, token: str, batch: str | None, catalog, csv_form: str) -> str:
    drop = (
        '<section class="dropbox"><h2>Add sold prices</h2>'
        '<p class="why">Drop pages you saved from a sold-results search. CardMan reads every sale on them, removes seller names, '
        'and keeps the page as evidence. Nothing is fetched from those sites.</p>'
        f'<form method="post" action="/import/pages" enctype="multipart/form-data" class="drop" data-drop>'
        f'<input type="hidden" name="t" value="{e(token)}">'
        '<label class="dz"><input type="file" name="pages" multiple accept=".html,.htm,.webarchive,text/html" required>'
        '<span class="dz-big">Drop saved pages here</span><span class="dz-small">or click to choose · .html or Safari .webarchive · '
        'several at once is fine</span></label><button>Import pages</button></form>'
        '<p class="small muted">Or put the files in <code>~/card-man/data/inbox</code>; the engine reads them on its next pass.</p>'
        '<details class="howto" open><summary>How to save a sold-results page (3 steps)</summary><ol class="guide">'
        + "".join(f"<li><b>{e(site)}</b>: {text}</li>" for site, text in GUIDE) + '</ol></details></section>')

    bl = batches(st)
    if not bl and not batch:
        return '<h1>Import</h1>' + drop + csv_form
    batch = batch or bl[0][0]
    rows = batch_rows(st, batch)
    strength = {"INDEPENDENT_TRANSACTION": 0, "ACCEPTED_OFFER": 1, "MARKETPLACE_MARKED_SOLD": 2}
    rows.sort(key=lambda r: strength.get(r["obs"].evidence_type.value, 9))      # the copy with the real price is the one shown
    need, ready, other = [], [], []
    shown_lineage: dict[str, list[str]] = {}
    for r in rows:
        lin = r["obs"].upstream_lineage[0] if r["obs"].upstream_lineage else None
        if lin:
            shown_lineage.setdefault(lin, []).append(r["site"])
    seen_lineage: set[str] = set()
    for r in rows:
        o = r["obs"]
        lin = o.upstream_lineage[0] if o.upstream_lineage else None
        # One sale, two sites: show it once (the stronger record first), and say where else it appeared.
        if lin and lin in seen_lineage:
            continue
        if lin:
            seen_lineage.add(lin)
        also = [s for s in shown_lineage.get(lin, []) if s != r["site"]] if lin else []
        n = len(o.printing_candidates)
        cls, msg = _status(o, r["confirmed"], n)
        price = e(charts.fmt(float(o.original_amount), o.currency))
        when = o.event_time.date().isoformat()
        flags = "".join(f'<span class="chip">{e(x)}</span>' for x in
                        ([f"{o.grade_company} {o.grade}"] if o.grade_company else []) + (["lot"] if o.is_lot else []))
        head = (f'<td><a href="{e(o.source_url)}" target="_blank" rel="noreferrer noopener">{e(r["title"])}</a> {flags}'
                f'<br><span class="muted small">{e(r["site"])} · {e(o.listing_id)}{" · also on " + e(", ".join(sorted(set(also)))) if also else ""}</span></td>'
                f'<td class="n">{price}</td><td class="d">{e(when)}</td>')
        if cls == "warnt" and n > 1 and catalog is not None and o.evidence_type.value != "SELLER_CLAIM":
            opts = _options(r["title"], catalog)
            sel = "".join(f'<option value="{e(pid)}">{e(label)}</option>' for pid, label, _w in opts)
            form = (f'<form method="post" action="/obs/{quote(o.id, safe="")}/identify" class="inline pick">'
                    f'<input type="hidden" name="t" value="{e(token)}"><input type="hidden" name="back" value="/import?batch={e(batch)}">'
                    f'<select name="printing" aria-label="Which printing">{sel}</select><button>This one</button></form>'
                    f'<span class="small muted">{e(msg)}</span>')
            need.append(f'<tr>{head}<td>{form}</td></tr>')
        else:
            label = ""
            if len(o.printing_candidates) == 1 and catalog is not None:
                base = o.printing_candidates[0].split("|")[0]
                p = next((x for x in catalog.printings if x.id == base), None)
                label = (_label(p) + (" · " + o.printing_candidates[0].split("|ed:")[1] if "|ed:" in o.printing_candidates[0] else "")) if p else ""
            (ready if cls == "good" else other).append(
                f'<tr>{head}<td><span class="{cls}">{e(msg)}</span>{"<br><span class=small>" + e(label) + "</span>" if label else ""}</td></tr>')

    def table(title, body, why):
        if not body:
            return ""
        return (f'<section><h2>{e(title)} <span class="muted">({len(body)})</span></h2><p class="why">{why}</p><div class="wrap"><table>'
                '<tr><th>Sale</th><th class="n">Price</th><th>Sold</th><th>Identity</th></tr>' + "".join(body) + '</table></div></section>')

    picker = "".join(f'<a class="chip{" good" if b == batch else ""}" href="/import?batch={e(b)}">{e(at[:16].replace("T", " "))} · {n} sales · {e(sites)}</a> '
                     for b, at, n, sites in bl)
    return ('<h1>Import</h1>' + drop
            + (f'<p class="batches">Imports: {picker}</p>' if bl else "")
            + table("Pick the printing", need, "The title alone cannot say which printing sold. Choose it once and the sale counts "
                    "for that printing from now on; the most likely options come first.")
            + table("Ready to use", ready, "These sales now price their printing.")
            + table("Kept as evidence only", other, "Lots and hidden Best Offer amounts never set a price, but stay on record.")
            + csv_form)


IMPORT_CSS = """
.dropbox .why{max-width:80ch}form.drop{display:flex;gap:10px;align-items:stretch;flex-wrap:wrap;margin:10px 0 6px}
.dz{flex:1;min-width:260px;border:2px dashed var(--axis);border-radius:14px;padding:26px 18px;text-align:center;cursor:pointer;
background:var(--soft);display:flex;flex-direction:column;gap:4px;color:var(--fg2)}.dz.over{border-color:var(--accent);background:var(--surface)}
.dz input{position:absolute;opacity:0;width:1px;height:1px}.dz-big{font-size:17px;font-weight:600;color:var(--fg)}.dz-small{font-size:12px}
form.drop button{padding:0 22px;font-weight:600}.guide{margin:6px 0 0;padding-left:20px}.guide li{margin:4px 0}
kbd{font:12px ui-monospace,monospace;border:1px solid var(--axis);border-bottom-width:2px;border-radius:4px;padding:0 4px;background:var(--surface)}
.batches{display:flex;flex-wrap:wrap;gap:6px;align-items:center}.pick select{max-width:420px}
"""

IMPORT_JS = r"""
(function(){document.querySelectorAll('form[data-drop]').forEach(function(f){var dz=f.querySelector('.dz'),inp=f.querySelector('input[type=file]');
if(!dz||!inp)return;['dragenter','dragover'].forEach(function(t){dz.addEventListener(t,function(ev){ev.preventDefault();dz.classList.add('over')})});
['dragleave','drop'].forEach(function(t){dz.addEventListener(t,function(ev){ev.preventDefault();dz.classList.remove('over')})});
dz.addEventListener('drop',function(ev){if(ev.dataTransfer&&ev.dataTransfer.files.length){inp.files=ev.dataTransfer.files;f.submit()}});
inp.addEventListener('change',function(){var s=dz.querySelector('.dz-small');if(s&&inp.files.length)s.textContent=inp.files.length+' file(s) ready'})})})();
"""
