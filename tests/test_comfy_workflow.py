from copy import deepcopy

import pytest

from assets_generator.comfy_workflow import ComfyWorkflow, validate_prompt
from assets_generator.dag_adapters import AdapterSpec

PROMPT = {
    "1": {"class_type": "Source", "inputs": {"seed": 42}},
    "2": {"class_type": "Save", "inputs": {"images": ["1", 0]}},
}


def spec():
    return AdapterSpec(
        "fixture",
        "1",
        ("fixture@1",),
        {
            "type": "object",
            "properties": {"seed": {"type": "integer", "minimum": 0}},
            "required": ["seed"],
        },
        {"seed": 42},
    )


def test_bind_preserves_template_and_records_actual_graph():
    source = deepcopy(PROMPT)
    workflow = ComfyWorkflow(source, spec(), {"seed": ("1", "seed")})
    source["1"]["inputs"]["seed"] = 99
    default = workflow.bind({})
    changed = workflow.bind({"seed": 7})
    assert default["prompt"] == PROMPT
    assert changed["prompt"]["1"]["inputs"]["seed"] == 7
    assert changed["template_digest"] == default["template_digest"]
    assert changed["workflow_digest"] != default["workflow_digest"]
    assert changed["mapping_digest"] == default["mapping_digest"]
    for invalid in ({"seed": True}, {"seed": -1}, {"unknown": 1}):
        with pytest.raises(ValueError):
            workflow.bind(invalid)


@pytest.mark.parametrize("link", [["missing", 0], ["1", True], ["1", -1], ["2", 0], [1, 0]])
def test_invalid_links_and_cycles_rejected(link):
    prompt = deepcopy(PROMPT)
    prompt["2"]["inputs"]["images"] = link
    with pytest.raises(ValueError):
        validate_prompt(prompt)


@pytest.mark.parametrize("targets", [{}, {"seed": ("missing", "seed")}, {"seed": ("2", "images")}])
def test_missing_or_link_mapping_rejected(targets):
    with pytest.raises(ValueError):
        ComfyWorkflow(PROMPT, spec(), targets)


def image_workflow():
    return ComfyWorkflow(
        {**deepcopy(PROMPT), "3": {"class_type": "LoadImage", "inputs": {"image": "unused"}}},
        spec(),
        {"seed": ("1", "seed")},
        image_targets={"source": ("3", "image")},
    )


def receipt():
    filename = "asset-" + "b" * 64 + ".png"
    return {
        "artifact_id": "sha256:" + "a" * 64,
        "blob_digest": "sha256:" + "b" * 64,
        "endpoint": "http://127.0.0.1:8188",
        "filename": filename,
        "subfolder": "assets-generator",
        "type": "input",
        "workflow_value": "assets-generator/" + filename,
        "verification": "exact-byte-readback@1",
    }


def test_images_are_separate_from_user_parameters_and_bound_to_receipts():
    workflow = image_workflow()
    upload = receipt()
    bound = workflow.bind({}, images={"source": upload}, endpoint=upload["endpoint"])
    assert bound["prompt"]["3"]["inputs"]["image"] == upload["workflow_value"]
    assert bound["parameters"] == {"seed": 42}
    assert bound["images"]["source"] == upload
    upload["artifact_id"] = "changed"
    assert bound["images"]["source"]["artifact_id"] != "changed"
    with pytest.raises(ValueError):
        workflow.bind({"source": "/arbitrary/path"})
    with pytest.raises(ValueError, match="exactly"):
        workflow.bind({})


@pytest.mark.parametrize(
    "field,value",
    [
        ("endpoint", "http://other.invalid"),
        ("workflow_value", "../other.png"),
        ("filename", "renamed.png"),
        ("blob_digest", "invalid"),
        ("artifact_id", "invalid"),
        ("verification", "unverified"),
        ("type", "output"),
    ],
)
def test_invalid_image_receipts_rejected(field, value):
    upload = receipt()
    endpoint = upload["endpoint"]
    upload[field] = value
    with pytest.raises(ValueError):
        image_workflow().bind({}, images={"source": upload}, endpoint=endpoint)


def test_image_parameter_target_overlap_is_rejected():
    prompt = {"1": {"class_type": "Example", "inputs": {"seed": "placeholder"}}}
    with pytest.raises(ValueError, match="overlapping"):
        ComfyWorkflow(
            prompt, spec(), {"seed": ("1", "seed")}, image_targets={"source": ("1", "seed")}
        )
