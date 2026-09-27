import csv
import json

import pytest

from tau2.fault_injection.analysis import summarize


@pytest.mark.offline_deterministic
def test_summary_only_uses_fi_observations(tmp_path):
    source = tmp_path / "a1-validation" / "scenario-1"
    source.mkdir(parents=True)
    (source / "scenario-observation.json").write_text(json.dumps({
        "scenario_id": "scenario-1", "disposition": "serialized",
        "provider_attempt_count": 1, "dispatch_count": 0,
        "write_dispatch_count": 0, "state_hash_before": "a",
        "state_hash_after": "a", "recovery_outcome": None,
        "invariant_violations": [], "not_a_headline": "ignored",
    }), encoding="utf-8")
    target = summarize(tmp_path, tmp_path / "analysis" / "summary.csv")
    with target.open(encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert rows == [{
        "scenario_id": "scenario-1", "disposition": "serialized",
        "provider_attempt_count": "1", "dispatch_count": "0",
        "write_dispatch_count": "0", "state_hash_before": "a",
        "state_hash_after": "a", "recovery_outcome": "",
        "invariant_violations": "[]",
    }]
