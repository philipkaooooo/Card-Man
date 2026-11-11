"""Comparables drawn from the store across runs, and re-importing the same facts later."""
import json
from datetime import timedelta
from pathlib import Path

import pytest

from cardman.slice import run
from cardman.store import ImmutableViolation, Store, main, replay
from tests.test_store import NOW, ROOT

DEMO = ROOT / "examples" / "demo_observations_SYNTHETIC.csv"
LATER = NOW + timedelta(hours=1)          # the demo FX table is only fresh until 2026-11-05


def listing():
    return json.loads((ROOT / "examples" / "demo_listing_SYNTHETIC.json").read_text())


def split_csv(tmp_path: Path) -> tuple[Path, Path]:
    """Demo rows d1, d4, d5 in one file and d2, d3 in another: two separate imports."""
    head, *rows = DEMO.read_text().splitlines()
    a = tmp_path / "a.csv"; b = tmp_path / "b.csv"
    a.write_text("\n".join([head] + [r for r in rows if r.split(",")[0] in ("d1", "d4", "d5")]) + "\n")
    b.write_text("\n".join([head] + [r for r in rows if r.split(",")[0] in ("d2", "d3")]) + "\n")
    return a, b


def test_same_csv_in_a_later_run_is_not_a_rewrite(tmp_path):
    db = str(tmp_path / "s.sqlite")
    run(listing(), DEMO, NOW, allow_synthetic=True, store_path=db)
    rep = run(listing(), DEMO, LATER, allow_synthetic=True, store_path=db)   # used to raise ImmutableViolation
    assert len(rep["comps"]) == 3
    st = Store(db)
    assert st.original("d1").capture_time == NOW                              # first capture is kept


def test_changed_fact_on_reimport_is_still_refused(tmp_path):
    db = str(tmp_path / "s.sqlite")
    run(listing(), DEMO, NOW, allow_synthetic=True, store_path=db)
    edited = tmp_path / "edited.csv"
    edited.write_text(DEMO.read_text().replace("d1,MARKETPLACE_MARKED_SOLD,demo,900", "d1,MARKETPLACE_MARKED_SOLD,demo,990"))
    with pytest.raises(ImmutableViolation):
        run(listing(), edited, LATER, allow_synthetic=True, store_path=db)


def test_comps_accumulate_across_runs(tmp_path):
    a, b = split_csv(tmp_path)
    db = str(tmp_path / "s.sqlite")
    first = run(listing(), a, NOW, allow_synthetic=True, store_path=db)
    assert [c["id"] for c in first["comps"]] == ["d1"]
    csv_only = run(listing(), b, LATER, allow_synthetic=True)
    assert csv_only["comp_pool"]["source"] == "csv" and len(csv_only["comps"]) == 2
    second = run(listing(), b, LATER, allow_synthetic=True, store_path=db)
    assert second["comp_pool"] == {"source": "store", "observations": 5}
    assert sorted(c["id"] for c in second["comps"]) == ["d1", "d2", "d3"]
    assert second["buy_ceiling"]["ceiling"] == "17125"                       # same as the one-file demo


def test_store_only_run_needs_no_csv(tmp_path):
    db = str(tmp_path / "s.sqlite")
    run(listing(), DEMO, NOW, allow_synthetic=True, store_path=db)
    rep = run(listing(), None, LATER, allow_synthetic=True, store_path=db)
    assert len(rep["comps"]) == 3
    with pytest.raises(ValueError):
        run(listing(), None, LATER)


def test_cancellation_between_runs_drops_the_comp_but_old_snapshot_still_replays(tmp_path):
    db = str(tmp_path / "s.sqlite")
    before = run(listing(), DEMO, NOW, allow_synthetic=True, store_path=db)
    st = Store(db)
    st.set_status("d3", "CANCELLED", "buyer did not pay", actor="manual", now=NOW + timedelta(minutes=30))
    after = run(listing(), None, LATER, allow_synthetic=True, store_path=db)
    assert [c["id"] for c in after["comps"]] == ["d2", "d1"]
    assert any(i == "d3" and "CANCELLED" in why for i, why in after["rejected_evidence"])
    assert after["buy_ceiling"]["ceiling"] != before["buy_ceiling"]["ceiling"]
    assert replay(Store(db), before["status"]["stored_snapshot_id"])["matches"]


def test_observations_from_later_runs_do_not_leak_into_an_earlier_decision_time(tmp_path):
    a, b = split_csv(tmp_path)
    db = str(tmp_path / "s.sqlite")
    run(listing(), b, LATER, allow_synthetic=True, store_path=db)     # d2, d3 captured at LATER
    rep = run(listing(), a, NOW, allow_synthetic=True, store_path=db)  # decision at NOW cannot see them
    assert [c["id"] for c in rep["comps"]] == ["d1"]


def test_cli_import_status_comps_decide(tmp_path, capsys):
    db = str(tmp_path / "s.sqlite")
    real = tmp_path / "real.csv"
    real.write_text(DEMO.read_text().replace("demo://", "https://example.invalid/"))
    main(["--store", db, "--now", NOW.isoformat(), "import", str(real)])
    assert json.loads(capsys.readouterr().out) == {"rows": 5, "added": 5, "already_stored": 0, "synthetic": 0}
    main(["--store", db, "--now", LATER.isoformat(), "import", str(real)])
    assert json.loads(capsys.readouterr().out)["already_stored"] == 5
    main(["--store", db, "--now", LATER.isoformat(), "status", "d1", "refunded", "item not as described"])
    assert json.loads(capsys.readouterr().out)["history"][0][0] == "REFUNDED"
    main(["--store", db, "--now", LATER.isoformat(), "comps", "p:LOB-EN001:UR:1st"])
    out = json.loads(capsys.readouterr().out)
    assert [c["id"] for c in out["comps"]] == ["d3", "d2"] and out["pool"] == 5
    main(["--store", db, "comps", "p:LOB-EN001:UR:1st", "--as-of", (NOW + timedelta(minutes=1)).isoformat()])
    assert [c["id"] for c in json.loads(capsys.readouterr().out)["comps"]] == ["d3", "d2", "d1"]

    rep = run(listing(), None, LATER, store_path=db)
    sid = rep["status"]["stored_snapshot_id"]
    capsys.readouterr()
    main(["--store", db, "--now", LATER.isoformat(), "decide", sid, "WAIT", "authenticity not reviewed",
          "--wait-trigger", "seller sends back-of-card photo"])
    assert json.loads(capsys.readouterr().out)["decisions"][0][0] == "WAIT"
    main(["--store", db, "replay", sid])
    assert json.loads(capsys.readouterr().out)["matches"] is True
