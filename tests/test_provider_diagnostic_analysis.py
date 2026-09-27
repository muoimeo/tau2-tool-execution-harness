"""Offline tests for safe provider-diagnostic aggregation."""

import json
import sys

from scripts import analyze_provider_retry_diagnostics as analyzer


def test_diagnostic_summary_compares_context_tools_ids_and_finish_reasons(tmp_path, monkeypatch):
    trace_root = tmp_path / "traces"
    trace_root.mkdir()
    rows = [
        {
            "sequence": 1, "generation_id": "task-23__trial-000__seed-1__attempt-001-g0001",
            "provider_attempt": 1, "requested_model": "openai/model", "resolved_model": "model",
            "completion_id": "c-1", "transport_request_id": "t-1", "id_source": "transport_request_id",
            "finish_reason": "stop", "tool_schema_count": 0, "input_token_bucket": "0-4k",
            "request_size_bucket": "0-4k", "failure_code": None, "outcome": "success",
        },
        {
            "sequence": 2, "generation_id": "task-23__trial-000__seed-1__attempt-001-g0002",
            "provider_attempt": 1, "requested_model": "openai/model", "resolved_model": "model",
            "completion_id": "c-1", "transport_request_id": "t-2", "id_source": "transport_request_id",
            "finish_reason": "stop", "tool_schema_count": 15, "input_token_bucket": "4k-16k",
            "request_size_bucket": "16k-64k", "failure_code": "PROVIDER_EMPTY_ASSISTANT_MESSAGE", "outcome": "budget_exhausted",
        },
    ]
    (trace_root / "episode.provider-retry.jsonl").write_text(
        "\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8"
    )
    output = tmp_path / "analysis"
    monkeypatch.setattr(sys, "argv", ["analyzer", "--trace-root", str(trace_root), "--output-dir", str(output)])
    analyzer.main()
    summary = json.loads((output / "provider_diagnostic_summary.json").read_text(encoding="utf-8"))
    assert summary["schema_version"] == "provider-diagnostic-summary/v2"
    assert summary["empty_rate_by_tools_enabled"]["tools_on"]["empty_rate"] == 1.0
    assert summary["empty_rate_by_tools_enabled"]["tools_off"]["empty_rate"] == 0.0
    assert summary["completion_id_uniqueness"] == {"non_null_count": 2, "unique_count": 1, "duplicate_count": 1}
    assert summary["finish_reason_distribution"] == {"stop": 2}
    assert summary["fingerprint_completion_id_correlation"]["repeated_fingerprint_groups"] == 0
