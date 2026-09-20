from copy import deepcopy

import pytest

from assets_generator.comfy_history import validate_history
from assets_generator.comfy_submission import ComfySubmissionUnknown


def fixture():
    record = {
        "phase": "sending",
        "prompt_id": "fixed",
        "prompt": {"1": {"class_type": "Fixture", "inputs": {"seed": 42}}},
    }
    entry = {
        "prompt": [0, "fixed", deepcopy(record["prompt"]), {}, ["1"]],
        "outputs": {"1": {"images": [{"filename": "output.png"}]}},
        "status": {"status_str": "success", "completed": True, "messages": []},
    }
    return record, {"fixed": entry}


def test_lost_ack_history_correlates_and_detaches_without_authorizing_repost():
    record, history = fixture()
    result = validate_history(record, history)
    assert result["state"] == "succeeded"
    assert result["provenance_scope"] == "composite_boundary_only"
    history["fixed"]["outputs"].clear()
    assert result["outputs"]["1"]["images"]
    assert record["phase"] == "sending"


@pytest.mark.parametrize(
    "change",
    ["missing", "outer_id", "inner_id", "graph", "prepared", "status", "incomplete", "outputs"],
)
def test_unrelated_or_malformed_history_is_uncertain(change):
    record, history = fixture()
    entry = history["fixed"]
    if change == "missing":
        history = {}
    elif change == "outer_id":
        history = {"other": entry}
    elif change == "inner_id":
        entry["prompt"][1] = "other"
    elif change == "graph":
        entry["prompt"][2]["1"]["inputs"]["seed"] = 43
    elif change == "prepared":
        record["phase"] = "prepared"
    elif change == "status":
        entry["status"]["completed"] = 1
    elif change == "incomplete":
        entry["status"]["completed"] = False
    else:
        entry["outputs"] = {"unknown": {}}
    with pytest.raises(ComfySubmissionUnknown):
        validate_history(record, history)


def test_error_history_preserves_diagnostics_and_is_not_success():
    record, history = fixture()
    history["fixed"]["status"] = {
        "status_str": "error",
        "completed": False,
        "messages": [["execution_error", {"exception_message": "fixture failed"}]],
    }
    result = validate_history(record, history)
    assert result["state"] == "failed"
    assert result["status"]["messages"] == history["fixed"]["status"]["messages"]
