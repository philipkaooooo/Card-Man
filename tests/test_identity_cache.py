"""Revision-aware cache updates, run against a throwaway git repo seeded with real YAML Yugi records."""
import json
import shutil
import subprocess
from pathlib import Path

from cardman.identity import ListingSignals, resolve
from cardman.identity_cache import IdentityCache
from cardman.models import IdentityStatus

DATA = Path(__file__).parent / "data" / "yaml_yugi"


def git(repo, *a):
    return subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True, text=True).stdout.strip()


def make_repo(tmp_path):
    repo = tmp_path / "yy"
    (repo / "data" / "cards").mkdir(parents=True)
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "t@example.invalid")
    git(repo, "config", "user.name", "t")
    for pw in ("89631139", "77336644"):
        shutil.copy(DATA / f"{pw}.json", repo / "data" / "cards" / f"{pw}.json")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "r1")
    return repo


def test_incremental_sync_retires_instead_of_deleting(tmp_path):
    repo = make_repo(tmp_path)
    cache = IdentityCache(tmp_path / "c.sqlite")
    s1 = cache.sync_yaml_yugi(repo)
    assert s1["status"] == "ok" and s1["files_changed"] == 2
    r1 = git(repo, "rev-parse", "HEAD")
    gone = "p:EN_TCG:CRMS-EN004:crimson-crisis:ghost-rare@77336644"
    assert cache.printing(gone) and not cache.is_retired(gone)

    # r2: drop one rarity from one file; leave the other file untouched.
    p = repo / "data" / "cards" / "77336644.json"
    rec = json.loads(p.read_text(encoding="utf-8"))
    for s in rec["sets"]["en"]:
        if s["set_number"] == "CRMS-EN004":
            s["rarities"] = ["Ultra Rare", "Ultimate Rare"]
    p.write_text(json.dumps(rec, ensure_ascii=False), encoding="utf-8")
    git(repo, "commit", "-qam", "r2")

    s2 = cache.sync_yaml_yugi(repo)
    assert s2["from_rev"] == r1 and s2["files_changed"] == 1   # BEWD file not re-read
    assert s2["retired"] == 1 and s2["added"] == 0
    assert cache.is_retired(gone)
    assert cache.printing(gone) is not None                    # still replayable
    assert gone not in {x.id for x in cache.catalog().printings}

    assert cache.sync_yaml_yugi(repo)["status"] == "up_to_date"

    # Going back to r1 restores the printing with its history intact.
    s3 = cache.sync_yaml_yugi(repo, rev=r1)
    assert s3["restored"] == 1 and not cache.is_retired(gone)
    hist = [c for (c,) in cache.db.execute("SELECT change FROM printing_history WHERE id=? ORDER BY rowid", (gone,))]
    assert hist == ["added", "retired", "restored"]


def test_removed_file_retires_its_printings(tmp_path):
    repo = make_repo(tmp_path)
    cache = IdentityCache(tmp_path / "c.sqlite")
    cache.sync_yaml_yugi(repo)
    git(repo, "rm", "-q", "data/cards/77336644.json")
    git(repo, "commit", "-qm", "rm")
    s = cache.sync_yaml_yugi(repo)
    assert s["files_removed"] == 1 and s["retired"] == 9   # 4 EN + 5 JP printings
    assert all(not p.id.endswith("@77336644") for p in cache.catalog().printings)


def test_failed_sync_keeps_previous_revision(tmp_path):
    repo = make_repo(tmp_path)
    cache = IdentityCache(tmp_path / "c.sqlite")
    cache.sync_yaml_yugi(repo)
    r1 = cache.revision()
    (repo / "data" / "cards" / "00000001.json").write_text("{not json", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "broken")
    s = cache.sync_yaml_yugi(repo)
    assert s["status"] == "failed" and s["added"] == 0
    assert cache.revision() == r1


def test_catalog_from_cache_resolves_like_source(tmp_path):
    repo = make_repo(tmp_path)
    cache = IdentityCache(tmp_path / "c.sqlite")
    cache.sync_yaml_yugi(repo)
    res = resolve(ListingSignals(title="青眼の白龍", printed_set_code="LB-01", card_language="ja"), cache.catalog())
    assert res.status == IdentityStatus.RESOLVED
    assert res.printing.source_refs[0].startswith("yaml_yugi@")


def test_opening_the_cache_leaves_no_write_lock_behind(tmp_path):
    import sqlite3
    from cardman.identity_cache import IdentityCache
    p = tmp_path / "c.sqlite"
    first = IdentityCache(p)                 # stays open, as a long-running UI keeps it
    other = sqlite3.connect(p, timeout=0.2)
    with other:
        other.execute("CREATE TABLE IF NOT EXISTS probe (x)")   # would raise "database is locked" before the fix
    assert first.revision() is None
