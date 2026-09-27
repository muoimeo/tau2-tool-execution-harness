import pytest

from tau2.data_model.message import ToolCall
from tau2.fault_injection.observer import NativeExecutionObserver, summarize_harness_trace
from tau2.registry import registry


@pytest.mark.offline_deterministic
def test_native_observer_is_read_only_and_records_retail_precondition():
    environment = registry.get_env_constructor("retail")()
    observer = NativeExecutionObserver()
    observer.bind(environment)
    before = environment.get_db_hash()
    response = environment.get_response(ToolCall(
        id="fi-read", name="find_user_id_by_name_zip",
        arguments={"first_name": "Nobody", "last_name": "Missing", "zip": "00000"},
        requestor="assistant",
    ))
    assert response.error is True
    assert observer.dispatch_count == 1
    assert observer.write_dispatch_count == 0
    assert observer.calls[0].state_hash_before == before == observer.calls[0].state_hash_after


@pytest.mark.offline_deterministic
def test_harness_trace_summary_exports_native_tool_calls(tmp_path):
    (tmp_path / "episode.jsonl").write_text(
        '{"logical_call_id":"a","tool_name":"lookup","native_tool_type":"read","requestor":"assistant","phase":"dispatch_completed","dispatch_status":"success","state_hash_before":"h1","state_hash_after":"h1"}\n'
        '{"logical_call_id":"b","tool_name":"write","native_tool_type":"write","requestor":"assistant","phase":"dispatch_completed","dispatch_status":"native_error","state_hash_before":"h1","state_hash_after":"h1"}\n',
        encoding="utf-8",
    )
    summary = summarize_harness_trace(tmp_path)
    assert summary["native_tool_calls"] == [
        {"logical_call_id": "a", "tool_name": "lookup", "tool_type": "read", "requestor": "assistant", "error": False, "state_hash_before": "h1", "state_hash_after": "h1"},
        {"logical_call_id": "b", "tool_name": "write", "tool_type": "write", "requestor": "assistant", "error": True, "state_hash_before": "h1", "state_hash_after": "h1"},
    ]
