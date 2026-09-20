import json
from copy import deepcopy

import pytest

from assets_generator.comfy_profile import ComfyImageProfile


def profile():
    return {
        "schema": "comfy-image-profile@1",
        "service_id": "image-service",
        "endpoint": "http://127.0.0.1:8188",
        "prompt": {
            "1": {"class_type": "LoadImage", "inputs": {"image": "placeholder"}},
            "2": {"class_type": "Fixture", "inputs": {"image": ["1", 0], "seed": 1}},
            "3": {"class_type": "SaveImage", "inputs": {"images": ["2", 0]}},
        },
        "parameter_schema": {"type": "object", "properties": {"seed": {"type": "integer"}}},
        "defaults": {"seed": 1},
        "parameter_targets": {"seed": ["2", "seed"]},
        "image_targets": {"image": ["1", "image"]},
        "output": {"node": "3", "index": 0, "mode": "RGB"},
        "deployment_claims": {"models": "unverified"},
    }


def test_profile_roundtrip_canonical_identity_and_detached_workflow(tmp_path):
    raw = profile()
    path = tmp_path / "profile.json"
    path.write_text(json.dumps(raw, indent=2))
    loaded = ComfyImageProfile.load(path)
    compact = ComfyImageProfile(json.dumps(raw).encode())
    assert compact.identity == loaded.identity
    assert loaded.to_dict() == raw
    assert loaded.workflow().spec.normalize_parameters({})["seed"] == 1
    detached = loaded.to_dict()
    detached["defaults"]["seed"] = 99
    assert loaded.to_dict()["defaults"]["seed"] == 1
    for field, value in [
        ("defaults", {"seed": 2}),
        ("output", {"node": "3", "index": 1, "mode": "RGBA"}),
        ("deployment_claims", {"revision": "changed"}),
    ]:
        changed = deepcopy(raw)
        changed[field] = value
        assert ComfyImageProfile(json.dumps(changed).encode()).identity != loaded.identity


@pytest.mark.parametrize(
    "field,value",
    [
        ("image_targets", {"image": ["2", "image"]}),
        ("parameter_targets", {"seed": ["missing", "seed"]}),
        ("output", {"node": "3", "index": True, "mode": "RGB"}),
        ("output", {"node": "missing", "index": 0, "mode": "RGB"}),
        ("output", {"node": "3", "index": 0, "mode": "L"}),
        ("endpoint", "http://user:password@localhost:8188"),
        ("deployment_claims", []),
    ],
)
def test_profile_rejects_invalid_bindings(field, value):
    raw = profile()
    raw[field] = value
    with pytest.raises(ValueError):
        ComfyImageProfile(json.dumps(raw).encode())


def test_profile_rejects_duplicate_json_keys():
    with pytest.raises(ValueError, match="duplicate"):
        ComfyImageProfile(b'{"schema":"one","schema":"two"}')


def test_profile_start_binds_request_and_finish_mapping(tmp_path, monkeypatch):
    import assets_generator.comfy_result as result
    import assets_generator.comfy_service as service
    from assets_generator.models import ArtifactRef
    from assets_generator.remote_protocol import RemoteRequest

    loaded = ComfyImageProfile(json.dumps(profile()).encode())
    source = ArtifactRef("sha256:" + "a" * 64)
    req = loaded.request("one", images={"image": source}, parameters={})
    assert json.loads(req.payload_json)["parameters"] == {"seed": 1}
    calls = []

    def start(*args, **kwargs):
        calls.append(kwargs)
        return {"phase": "acknowledged"}

    monkeypatch.setattr(service, "start_owned_image", start)
    assert loaded.start(None, req, tmp_path / "journal", None)["phase"] == "acknowledged"
    assert calls[0]["images"] == {"image": source}
    assert calls[0]["deployment_claims"]["profile_digest"] == loaded.identity.backend_digest
    altered = profile()
    altered["defaults"]["seed"] = 2
    other = ComfyImageProfile(json.dumps(altered).encode())
    with pytest.raises(ValueError, match="another profile"):
        other.start(None, req, tmp_path / "journal", None)
    raw = json.loads(req.payload_json)
    raw["parameters"] = {}
    with pytest.raises(ValueError, match="normalized"):
        loaded.start(
            None, RemoteRequest.create(loaded.identity, "two", raw), tmp_path / "journal", None
        )
    from unittest.mock import Mock

    import assets_generator.comfy_failure as failure

    monkeypatch.setattr(failure, "finish_failure", lambda *a: None)
    owner = Mock()
    owner.comfy_result.return_value = None
    monkeypatch.setattr(service, "finish_owned_image", lambda *a, **kw: calls.append(kw))
    monkeypatch.setattr(result, "publish_image_result", lambda *a: "published")
    assert loaded.finish(owner, req, tmp_path / "journal", None) == "published"
    assert calls[-1] == profile()["output"]
