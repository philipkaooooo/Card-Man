"""One click from a sold-results page into CardMan, and the searches worth running.

The "Send to CardMan" bookmark posts the page you are looking at (what your browser shows, signed in, after it has
loaded) to this machine. Nothing is fetched by CardMan itself. The capture key in the bookmark is the only thing that
lets a page post here, so another website cannot push pages into your store.
"""
from __future__ import annotations

import json
import secrets
from pathlib import Path
from urllib.parse import quote_plus, urlsplit

ROOT = Path(__file__).resolve().parent.parent
KEY_FILE = ROOT / "data" / "capture_key"
EBAY_YGO_SINGLES = "183454"


def capture_key(path: Path | None = None) -> str:
    path = Path(path or KEY_FILE)
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(secrets.token_urlsafe(24))
        path.chmod(0o600)
    return path.read_text().strip()


def bookmarklet(port: int, key: str) -> str:
    js = ("(function(){var f=document.createElement('form');f.method='POST';"
          f"f.action='http://127.0.0.1:{port}/capture';f.target='_blank';f.acceptCharset='utf-8';"
          "function a(n,v){var i=document.createElement('input');i.type='hidden';i.name=n;i.value=v;f.appendChild(i)}"
          f"a('k',{json.dumps(key)});a('url',location.href);a('html',document.documentElement.outerHTML);"
          "document.body.appendChild(f);f.submit();f.remove();})();")
    return "javascript:" + js


def file_name_for(url: str) -> str:
    host = (urlsplit(url).hostname or "page").replace(".", "_")
    return f"{host}.html"


def sold_search_links(name_en: str | None, name_ja: str | None, code: str | None = None) -> list[tuple[str, str]]:
    """Sold-results searches for one card, most precise first. 240 results per eBay page."""
    out = []
    q_en = " ".join(x for x in (name_en, code) if x)
    if q_en:
        out.append(("eBay sold", f"https://www.ebay.com/sch/i.html?_nkw={quote_plus(q_en)}&_sacat={EBAY_YGO_SINGLES}"
                                 "&LH_Sold=1&LH_Complete=1&_ipg=240"))
        out.append(("130point", f"https://130point.com/sales/?search={quote_plus(q_en)}"))
    q_ja = " ".join(x for x in (name_ja, code if code and "-JP" in code else None) if x)
    if q_ja:
        out.append(("Yahoo! 落札相場", f"https://auctions.yahoo.co.jp/closedsearch/closedsearch?p={quote_plus(q_ja)}&n=100"))
        out.append(("Mercari 売り切れ", f"https://jp.mercari.com/search?keyword={quote_plus(q_ja)}&status=sold_out"))
    return out


def worth_pricing(st, idx, limit: int = 12) -> list[dict]:
    """Cards where sold prices would change a decision now: the most likely card behind open opportunities."""
    have = {r[0] for r in st.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "opportunity" not in have:
        return []
    seen, out = set(), []
    for (pj,) in st.db.execute("SELECT payload_json FROM opportunity WHERE status IN ('OPEN','ESCALATED') ORDER BY priority DESC LIMIT 400"):
        p = json.loads(pj)
        sc = sorted(p.get("scenarios") or [], key=lambda s: -(s.get("weight") or 0))
        if not sc:
            continue
        pid = sc[0].get("printing_id") or ""
        pc = pid.rsplit("@", 1)[-1].split("|")[0] if "@" in pid else None
        if not pc or pc in seen:
            continue
        c = idx.card(pc) if idx is not None else None
        if not c:
            continue
        seen.add(pc)
        code = (sc[0].get("label") or "").split(" · ")[0]
        code = code if code and code != "no code" else None
        out.append({"passcode": pc, "name": c.get("name_en") or c.get("name_ja"), "ja": c.get("name_ja"), "code": code,
                    "links": sold_search_links(c.get("name_en"), c.get("name_ja"), code)})
        if len(out) == limit:
            break
    return out
