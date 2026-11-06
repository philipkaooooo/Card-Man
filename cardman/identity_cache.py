"""Local identity cache with revision-aware updates (CLAUDE.md section 10, step 2).

Backed by SQLite (stdlib). The cache records which source revision every printing was first
and last seen in. A printing that disappears from the source is RETIRED, never deleted, so a
decision snapshot taken earlier can still be replayed against the identity it used.

Sync from a YAML Yugi git checkout is incremental: `git ls-tree` gives a blob hash per card
file, and only files whose blob changed since the stored revision are re-read (via
`git cat-file --batch`, i.e. exactly the committed bytes of that revision).

usage:
  python -m cardman.identity_cache sync [--db data/cache/identity.sqlite] [--repo data/cache/yaml-yugi]
  python -m cardman.identity_cache stats [--db ...]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import subprocess
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from .identity import Catalog
from .identity_sources import YAML_YUGI_NORMALIZER_VERSION, normalize_yaml_yugi
from .models import CardFamily, Printing, Region

CACHE_SCHEMA_VERSION = 1
YAML_YUGI_URL = "https://github.com/DawnbrandBots/yaml-yugi"
SOURCE = "yaml_yugi"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS source_file (
    source TEXT NOT NULL, path TEXT NOT NULL, blob TEXT NOT NULL, passcode TEXT, family_id TEXT,
    family_json TEXT, revision TEXT NOT NULL, retired_rev TEXT,
    PRIMARY KEY (source, path));
CREATE TABLE IF NOT EXISTS printing (
    id TEXT PRIMARY KEY, source TEXT NOT NULL, path TEXT NOT NULL, family_id TEXT NOT NULL,
    region TEXT NOT NULL, set_code TEXT, data_json TEXT NOT NULL, content_hash TEXT NOT NULL,
    first_seen_rev TEXT NOT NULL, last_seen_rev TEXT NOT NULL, retired_rev TEXT);
CREATE INDEX IF NOT EXISTS printing_code ON printing(set_code);
CREATE INDEX IF NOT EXISTS printing_family ON printing(family_id);
CREATE INDEX IF NOT EXISTS printing_path ON printing(source, path);
CREATE TABLE IF NOT EXISTS printing_history (
    id TEXT NOT NULL, revision TEXT NOT NULL, change TEXT NOT NULL, data_json TEXT,
    recorded_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sync_log (
    source TEXT NOT NULL, from_rev TEXT, to_rev TEXT NOT NULL, started_at TEXT NOT NULL,
    files_changed INTEGER, files_removed INTEGER, added INTEGER, changed INTEGER, retired INTEGER,
    restored INTEGER, status TEXT NOT NULL, error TEXT);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _printing_json(p: Printing) -> str:
    d = asdict(p)
    d["region"] = p.region.value
    d["game_format"] = p.game_format.value
    return json.dumps(d, sort_keys=True, ensure_ascii=False)


def _printing_from_json(s: str) -> Printing:
    d = json.loads(s)
    d["region"] = Region(d["region"])
    from .models import GameFormat
    d["game_format"] = GameFormat(d["game_format"])
    for k in ("variant_evidence", "source_refs"):
        d[k] = tuple(d[k])
    return Printing(**d)


def _git(repo: Path, *args: str, input_bytes: bytes | None = None) -> bytes:
    return subprocess.run(["git", "-C", str(repo), *args], input=input_bytes, capture_output=True,
                          check=True).stdout


def read_blobs(repo: Path, blobs: list[str]) -> dict[str, bytes]:
    """Read committed blob contents in one `git cat-file --batch` call."""
    if not blobs:
        return {}
    out = _git(repo, "cat-file", "--batch", input_bytes=("\n".join(blobs) + "\n").encode())
    res, i = {}, 0
    while i < len(out):
        nl = out.index(b"\n", i)
        sha, _typ, size = out[i:nl].split(b" ")
        n = int(size)
        res[sha.decode()] = out[nl + 1:nl + 1 + n]
        i = nl + 1 + n + 1
    return res


class IdentityCache:
    def __init__(self, db_path: Path | str):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.db_path)
        self.db.executescript(_SCHEMA)
        self._meta_set("cache_schema_version", str(CACHE_SCHEMA_VERSION), only_if_missing=True)

    # -- meta ----------------------------------------------------------------------------------
    def _meta_get(self, key: str) -> str | None:
        row = self.db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row[0] if row else None

    def _meta_set(self, key: str, value: str, only_if_missing: bool = False) -> None:
        verb = "INSERT OR IGNORE" if only_if_missing else "INSERT OR REPLACE"
        self.db.execute(f"{verb} INTO meta(key, value) VALUES (?, ?)", (key, value))

    def revision(self, source: str = SOURCE) -> str | None:
        return self._meta_get(f"{source}:revision")

    # -- sync ----------------------------------------------------------------------------------
    def sync_yaml_yugi(self, repo: Path, *, rev: str = "HEAD", force_full: bool = False) -> dict:
        """Bring the cache to `rev` of the checkout (reads committed blobs, not the work tree)."""
        repo = Path(repo)
        to_rev = _git(repo, "rev-parse", "--verify", f"{rev}^{{commit}}").decode().strip()
        from_rev = self.revision()
        started = _now()
        norm_changed = self._meta_get(f"{SOURCE}:normalizer") != YAML_YUGI_NORMALIZER_VERSION
        stats = dict(source=SOURCE, from_rev=from_rev, to_rev=to_rev, files_changed=0, files_removed=0,
                     added=0, changed=0, retired=0, restored=0, status="ok", error=None)
        if from_rev == to_rev and not (force_full or norm_changed):
            stats["status"] = "up_to_date"
            self._log(stats, started)
            return stats
        try:
            tree = {}
            for line in _git(repo, "ls-tree", "-r", to_rev, "data/cards").decode().splitlines():
                meta, path = line.split("\t", 1)
                if path.endswith(".json"):
                    tree[path] = meta.split()[2]
            stored = {p: b for p, b in self.db.execute(
                "SELECT path, blob FROM source_file WHERE source=? AND retired_rev IS NULL", (SOURCE,))}
            changed = [p for p, b in tree.items() if force_full or norm_changed or stored.get(p) != b]
            removed = [p for p in stored if p not in tree]
            contents = read_blobs(repo, [tree[p] for p in changed])
            with self.db:
                for path in changed:
                    rec = json.loads(contents[tree[path]])
                    self._apply_file(path, tree[path], rec, to_rev, stats)
                for path in removed:
                    self._retire_file(path, to_rev, stats)
                stats["files_changed"], stats["files_removed"] = len(changed), len(removed)
                self._meta_set(f"{SOURCE}:revision", to_rev)
                self._meta_set(f"{SOURCE}:normalizer", YAML_YUGI_NORMALIZER_VERSION)
                self._meta_set(f"{SOURCE}:synced_at", _now())
        except Exception as e:  # keep the previous revision intact; record the failure
            # the transaction rolled back: nothing was applied
            stats.update(status="failed", error=f"{type(e).__name__}: {e}"[:500], files_changed=0,
                         files_removed=0, added=0, changed=0, retired=0, restored=0)
        self._log(stats, started)
        return stats

    def _apply_file(self, path: str, blob: str, rec: dict, rev: str, stats: dict) -> None:
        ref = f"{SOURCE}@{rev[:12]}:{path}"
        norm = normalize_yaml_yugi(rec, ref, file_key=Path(path).stem)
        fam_json = json.dumps(asdict(norm.family), ensure_ascii=False) if norm else None
        self.db.execute(
            "INSERT OR REPLACE INTO source_file(source, path, blob, passcode, family_id, family_json, revision,"
            " retired_rev) VALUES (?,?,?,?,?,?,?,NULL)",
            (SOURCE, path, blob, norm.passcode if norm else None, norm.family.id if norm else None, fam_json, rev))
        new = {p.id: p for p in (norm.printings if norm else ())}
        old = {pid: (h, r) for pid, h, r in self.db.execute(
            "SELECT id, content_hash, retired_rev FROM printing WHERE source=? AND path=?", (SOURCE, path))}
        for pid, p in new.items():
            # source_refs carry the revision; hash identity content only so a no-op bump is not a change
            body = _printing_json(p)
            h = hashlib.sha256(json.dumps({**json.loads(body), "source_refs": []}, sort_keys=True).encode()).hexdigest()
            if pid not in old:
                self.db.execute(
                    "INSERT INTO printing(id, source, path, family_id, region, set_code, data_json, content_hash,"
                    " first_seen_rev, last_seen_rev) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (pid, SOURCE, path, p.family_id, p.region.value, p.set_code, body, h, rev, rev))
                self._hist(pid, rev, "added", body)
                stats["added"] += 1
                continue
            old_h, retired = old[pid]
            change = "restored" if retired else ("changed" if old_h != h else None)
            self.db.execute("UPDATE printing SET data_json=?, content_hash=?, last_seen_rev=?, retired_rev=NULL"
                            " WHERE id=?", (body, h, rev, pid))
            if change:
                self._hist(pid, rev, change, body)
                stats[change] += 1
        for pid, (_h, retired) in old.items():
            if pid not in new and not retired:
                self.db.execute("UPDATE printing SET retired_rev=? WHERE id=?", (rev, pid))
                self._hist(pid, rev, "retired", None)
                stats["retired"] += 1

    def _retire_file(self, path: str, rev: str, stats: dict) -> None:
        self.db.execute("UPDATE source_file SET retired_rev=? WHERE source=? AND path=?", (rev, SOURCE, path))
        for (pid,) in self.db.execute("SELECT id FROM printing WHERE source=? AND path=? AND retired_rev IS NULL",
                                      (SOURCE, path)).fetchall():
            self.db.execute("UPDATE printing SET retired_rev=? WHERE id=?", (rev, pid))
            self._hist(pid, rev, "retired", None)
            stats["retired"] += 1

    def _hist(self, pid: str, rev: str, change: str, body: str | None) -> None:
        self.db.execute("INSERT INTO printing_history(id, revision, change, data_json, recorded_at) VALUES (?,?,?,?,?)",
                        (pid, rev, change, body, _now()))

    def _log(self, s: dict, started: str) -> None:
        with self.db:
            self.db.execute(
                "INSERT INTO sync_log(source, from_rev, to_rev, started_at, files_changed, files_removed, added,"
                " changed, retired, restored, status, error) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (s["source"], s["from_rev"], s["to_rev"], started, s["files_changed"], s["files_removed"],
                 s["added"], s["changed"], s["retired"], s["restored"], s["status"], s["error"]))

    # -- read ----------------------------------------------------------------------------------
    def printing(self, pid: str, include_retired: bool = True) -> Printing | None:
        q = "SELECT data_json, retired_rev FROM printing WHERE id=?"
        row = self.db.execute(q, (pid,)).fetchone()
        if not row or (row[1] and not include_retired):
            return None
        return _printing_from_json(row[0])

    def is_retired(self, pid: str) -> bool:
        row = self.db.execute("SELECT retired_rev FROM printing WHERE id=?", (pid,)).fetchone()
        return bool(row and row[0])

    def catalog(self) -> Catalog:
        fams: dict[str, CardFamily] = {}
        for (fj,) in self.db.execute("SELECT family_json FROM source_file WHERE retired_rev IS NULL AND"
                                     " family_json IS NOT NULL ORDER BY path"):
            d = json.loads(fj)
            d["aliases"] = tuple(d["aliases"])
            f = CardFamily(**d)
            if f.id in fams:  # alternate-artwork passcodes share a Konami ID: merge, keep all passcodes
                prev = fams[f.id]
                f = CardFamily(prev.id, {**f.names, **prev.names}, prev.konami_id,
                               ",".join(sorted({*(prev.passcode or "").split(","), f.passcode or ""} - {""})),
                               tuple(dict.fromkeys(prev.aliases + f.aliases)))
            fams[f.id] = f
        prints = [_printing_from_json(r[0]) for r in
                  self.db.execute("SELECT data_json FROM printing WHERE retired_rev IS NULL ORDER BY id")]
        return Catalog(list(fams.values()), prints, edition_rules=load_edition_rules())

    def stats(self) -> dict:
        one = lambda q: self.db.execute(q).fetchone()[0]  # noqa: E731
        return {
            "revision": self.revision(), "synced_at": self._meta_get(f"{SOURCE}:synced_at"),
            "files": one("SELECT count(*) FROM source_file WHERE retired_rev IS NULL"),
            "printings_active": one("SELECT count(*) FROM printing WHERE retired_rev IS NULL"),
            "printings_retired": one("SELECT count(*) FROM printing WHERE retired_rev IS NOT NULL"),
            "by_region": dict(self.db.execute("SELECT region, count(*) FROM printing WHERE retired_rev IS NULL"
                                              " GROUP BY region").fetchall()),
            "last_sync": self.db.execute("SELECT * FROM sync_log ORDER BY rowid DESC LIMIT 1").fetchone(),
        }


def load_edition_rules(path: Path | None = None) -> dict[str, list[str]]:
    path = path or Path(__file__).resolve().parent.parent / "data" / "edition_rules.json"
    if not Path(path).exists():
        return {}
    return {k: v for k, v in json.loads(Path(path).read_text(encoding="utf-8")).items() if not k.startswith("_")}


def update_checkout(repo: Path) -> None:
    """Clone or fast-forward a shallow YAML Yugi checkout that this tool owns."""
    repo = Path(repo)
    if not (repo / ".git").exists():
        repo.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "clone", "--depth", "1", "--single-branch", "--branch", "master", YAML_YUGI_URL,
                        str(repo)], check=True, env={"GIT_LFS_SKIP_SMUDGE": "1", **__import__("os").environ})
        return
    _git(repo, "fetch", "--depth", "1", "origin", "master")
    _git(repo, "reset", "--hard", "FETCH_HEAD")


def main():
    root = Path(__file__).resolve().parent.parent
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["sync", "stats"])
    ap.add_argument("--db", default=str(root / "data" / "cache" / "identity.sqlite"))
    ap.add_argument("--repo", default=str(root / "data" / "cache" / "yaml-yugi"))
    ap.add_argument("--no-fetch", action="store_true", help="use the checkout as is")
    ap.add_argument("--rev", default="HEAD", help="source revision to sync to")
    a = ap.parse_args()
    cache = IdentityCache(a.db)
    if a.cmd == "sync":
        if not a.no_fetch:
            update_checkout(Path(a.repo))
        print(json.dumps(cache.sync_yaml_yugi(Path(a.repo), rev=a.rev), indent=1))
    print(json.dumps(cache.stats(), indent=1, default=str))


if __name__ == "__main__":
    main()
