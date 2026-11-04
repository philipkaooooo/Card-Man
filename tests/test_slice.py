import json
from datetime import datetime, timezone
from pathlib import Path

from cardman.slice import run

EX = Path(__file__).parent.parent / "examples"
NOW = datetime(2026, 11, 4, 12, tzinfo=timezone.utc)


def load():
    return json.loads((EX / "demo_listing_SYNTHETIC.json").read_text())


def test_synthetic_rows_refused_by_default():
    rep = run(load(), EX / "demo_observations_SYNTHETIC.csv", NOW)
    assert rep["comps"] == [] and rep["buy_ceiling"]["ceiling"] is None


def test_end_to_end_demo_produces_all_five_outputs():
    rep = run(load(), EX / "demo_observations_SYNTHETIC.csv", NOW, allow_synthetic=True)
    assert rep["identity"]["status"] == "RESOLVED"
    assert len(rep["comps"]) == 3 and all(c["synthetic"] for c in rep["comps"])
    assert rep["exit_scenarios"]["method"] == "sparse:min/median"
    assert rep["buy_ceiling"]["ceiling"] == "17125"
    assert rep["status"]["stage"] == "OBSERVE"          # authenticity not reviewed blocks QUALIFIED


def test_ambiguous_identity_yields_no_ceiling():
    l = load(); l["signals"].pop("verified_edition")
    rep = run(l, EX / "demo_observations_SYNTHETIC.csv", NOW, allow_synthetic=True)
    assert rep["buy_ceiling"]["ceiling"] is None and rep["status"]["stage"] == "SPECTATE"
