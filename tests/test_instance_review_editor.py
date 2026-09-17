"""Exercise the shipped instance review ordering logic with Node.js."""

import shutil
import subprocess
from pathlib import Path

import pytest


def test_selected_proposal_order_can_be_changed_explicitly():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required for the instance review frontend regression")
    editor = Path("src/assets_generator/resources/instance-review.html").read_text()
    move = editor.split("function move(id,delta){", 1)[1].split("function renderList(){", 1)[0]
    script = r"""
const assert=require('node:assert/strict');
let selected=['p0','p1','p2'],draws=0;
function draw(){draws++}
function move(id,delta){
"""
    script += move
    script += r"""
move('p2',-1);
assert.deepEqual(selected,['p0','p2','p1']);
move('p0',-1);
assert.deepEqual(selected,['p0','p2','p1']);
move('missing',1);
assert.deepEqual(selected,['p0','p2','p1']);
assert.equal(draws,1);
"""
    subprocess.run([node, "-e", script], check=True, capture_output=True, text=True, timeout=20)
    assert "globalCompositeOperation='source-in'" in editor
    assert "proposal_ids:selected" in editor
