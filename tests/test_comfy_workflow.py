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
