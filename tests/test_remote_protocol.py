import copy
import json

import pytest

from assets_generator.remote_protocol import RemoteIdentity, RemoteJob, RemoteRequest


def request():
    return RemoteRequest.create(
        RemoteIdentity("service", "sha256:" + "a" * 64), "submission", {"seed": 42}
    )


def envelope(state="running"):
    raw = request().to_dict()
    raw.pop("payload")
    return {
        **raw,
        "protocol_version": "1",
        "job_id": "job-one",
        "state": state,
        "result": None,
        "error": None,
    }


def test_request_is_immutable_and_key_is_not_in_content_digest():
    payload = {"nested": [1, 2]}
    first = RemoteRequest.create(request().identity, "first", payload)
    second = RemoteRequest.create(request().identity, "second", payload)
    payload["nested"].append(3)
    assert first.request_digest == second.request_digest
    assert first.to_dict()["payload"] == {"nested": [1, 2]}
    exposed = first.to_dict()
    exposed["payload"]["nested"].append(4)
    assert first.to_dict()["payload"] == {"nested": [1, 2]}
    assert RemoteRequest(first.identity, first.submission_key, first.payload_json) == first


@pytest.mark.parametrize(
    "field,value",
    [
        ("service_id", "different"),
        ("backend_digest", "sha256:" + "b" * 64),
        ("submission_key", "other"),
        ("request_digest", "sha256:" + "c" * 64),
        ("job_id", "../job"),
        ("protocol_version", 1),
        ("state", "cancelled"),
        ("state", []),
        ("result", {}),
        ("error", {"code": "failure", "detail": "bad"}),
    ],
)
def test_reject_inconsistent_identity_and_state(field, value):
    raw = envelope()
    raw[field] = value
    with pytest.raises(ValueError):
        RemoteJob.parse(raw, request())


def test_terminal_results_are_copied_and_not_accepted_as_artifacts():
    raw = envelope("succeeded")
    raw["result"] = {"outputs": ["remote-description"]}
    job = RemoteJob.parse(raw, request(), expected_job_id="job-one")
    raw["result"]["outputs"].clear()
    assert json.loads(job.result_json) == {"outputs": ["remote-description"]}
    with pytest.raises(ValueError, match="ID changed"):
        RemoteJob.parse(envelope(), request(), expected_job_id="another")
    raw = envelope("failed")
    raw["error"] = {"code": "BACKEND_TIMEOUT", "detail": "server confirmed failure"}
    job = RemoteJob.parse(raw, request())
    assert json.loads(job.error_json)["code"] == "BACKEND_TIMEOUT"
    for changes in (
        {"result": {}},
        {"error": None},
        {"error": {"code": "", "detail": "bad"}},
        {"unexpected": True},
    ):
        broken = copy.deepcopy(raw)
        broken.update(changes)
        with pytest.raises(ValueError):
            RemoteJob.parse(broken, request())


@pytest.mark.parametrize("payload", [b"[]", b'{"a": 1}', b'{"x":NaN}'])
def test_reject_noncanonical_payload(payload):
    with pytest.raises(ValueError):
        RemoteRequest(request().identity, "key", payload)


@pytest.mark.parametrize(
    "raw",
    [
        b'{"a":1,"a":2}',
        b'{"a":NaN}',
        b'{"a":Infinity}',
        b'{"a":1e999}',
        b'{"nested":{"a":0,"a":1}}',
    ],
)
def test_strict_wire_json_rejects_ambiguity(raw):
    from assets_generator.remote_protocol import decode_remote_json

    with pytest.raises(ValueError):
        decode_remote_json(raw)


def test_strict_wire_json_preserves_valid_nested_values():
    from assets_generator.remote_protocol import decode_remote_json

    assert decode_remote_json(b'{"nested":[true,null,42,1.5]}') == {"nested": [True, None, 42, 1.5]}
