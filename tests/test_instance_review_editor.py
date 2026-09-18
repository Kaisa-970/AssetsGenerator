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
    assert "tintMask(overlay.data,colors[order%colors.length]," in editor
    assert "proposal_ids:selected" in editor


def test_opaque_binary_mask_background_stays_transparent():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required for the instance review frontend regression")
    editor = Path("src/assets_generator/resources/instance-review.html").read_text()
    tint = editor.split("function tintMask(pixels,color,invert=false){", 1)[1].split(
        "function draw(){", 1
    )[0]
    script = (
        "const assert=require('node:assert/strict');\nfunction tintMask(pixels,color,invert=false){"
        + tint
    )
    script += """
// Grayscale PNG becomes fully opaque RGBA, including its black background.
const pixels=new Uint8ClampedArray([0,0,0,255,255,255,255,255,0,0,0,255]);
tintMask(pixels,'#e63946');
assert.deepEqual([...pixels],[230,57,70,0,230,57,70,153,230,57,70,0]);
const empty=new Uint8ClampedArray([0,0,0,255]);
tintMask(empty,'#2a9d8f');
assert.equal(empty[3],0);
const full=new Uint8ClampedArray([255,255,255,255]);
tintMask(full,'#2a9d8f');
assert.deepEqual([...full],[42,157,143,153]);
const inverse=new Uint8ClampedArray([0,0,0,255,255,255,255,255]);
tintMask(inverse,'#e63946',true);
assert.deepEqual([...inverse],[230,57,70,153,230,57,70,0]);
"""
    subprocess.run([node, "-e", script], check=True, capture_output=True, text=True, timeout=20)


def test_largest_component_preview_matches_server_policy():
    import json

    import numpy as np
    from scipy import ndimage

    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required")
    editor = Path("src/assets_generator/resources/instance-review.html").read_text()
    function = (
        "function keepLargest("
        + editor.split("function keepLargest(", 1)[1].split("function draw(){", 1)[0]
    )
    cases = [
        np.zeros((4, 5), dtype=np.uint8),
        np.eye(5, dtype=np.uint8),
        np.array([[1, 0, 1], [1, 0, 1]], dtype=np.uint8),
    ]
    cases += [np.random.default_rng(i).integers(0, 2, (8, 9), dtype=np.uint8) for i in range(15)]
    script = "const assert=require('node:assert/strict');\n" + function
    for pixels in cases:
        labels, count = ndimage.label(pixels, structure=np.ones((3, 3)))
        sizes = np.bincount(labels.ravel())
        sizes[0] = 0
        winner = int(np.argmax(sizes)) if count else 0
        expected = ((labels == winner) & (labels != 0)).astype(int).ravel().tolist()
        script += "{const mask=" + json.dumps(pixels.ravel().tolist()) + ";"
        script += "const rgba=new Uint8ClampedArray(mask.flatMap(x=>[200,30,40,x?153:0]));"
        script += f"keepLargest(rgba,{pixels.shape[1]},{pixels.shape[0]});"
        script += (
            "assert.deepEqual(mask.map((_,i)=>rgba[i*4+3]?1:0)," + json.dumps(expected) + ");}\n"
        )
    subprocess.run([node, "-e", script], check=True, capture_output=True, text=True, timeout=20)
