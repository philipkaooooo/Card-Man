"""Extract identity-only fields for a few real cards from a pinned YAML Yugi revision.

The output is the regression-test catalog in tests/data/yaml_yugi/. Card text is dropped
(it is Konami's copyright and not needed for identity). Re-run to refresh:

  python scripts/extract_identity_subset.py <yaml-yugi checkout> <rev> <passcode> ...
"""
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

KEEP = ("konami_id", "password", "card_type", "limit_regulation", "yugipedia_page_id")


def main():
    repo, rev, *pws = sys.argv[1:]
    full = subprocess.run(["git", "-C", repo, "rev-parse", "--verify", f"{rev}^{{commit}}"], capture_output=True,
                          check=True, text=True).stdout.strip()
    out = Path(__file__).resolve().parent.parent / "tests" / "data" / "yaml_yugi"
    out.mkdir(parents=True, exist_ok=True)
    files = {}
    for pw in pws:
        path = f"data/cards/{pw}.json"
        raw = subprocess.run(["git", "-C", repo, "show", f"{full}:{path}"], capture_output=True, check=True).stdout
        rec = json.loads(raw)
        slim = {k: rec[k] for k in KEEP if k in rec}
        slim["name"] = {k: v for k, v in rec["name"].items() if k in ("en", "ja", "ja_romaji")}
        slim["sets"] = {k: v for k, v in rec.get("sets", {}).items() if k in ("en", "ja")}
        slim["images"] = rec.get("images", [])
        (out / f"{pw}.json").write_text(json.dumps(slim, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        files[f"{pw}.json"] = {"source_path": path, "name_en": rec["name"].get("en")}
    (out / "PROVENANCE.json").write_text(json.dumps({
        "source": "YAML Yugi (compiled from Yugipedia)", "repository": "https://github.com/DawnbrandBots/yaml-yugi",
        "revision": full, "extracted_at": datetime.now(timezone.utc).isoformat(),
        "fields": "identity only; card text removed", "files": files,
        "limitations": ["no edition (1st/Unlimited/Limited) data", "no release dates", "no Asian-English prints",
                        "secondary source: not cross-checked against Konami DB or YGOResources"],
    }, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
