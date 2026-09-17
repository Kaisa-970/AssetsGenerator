"""Exercise scene layout pose state using the shipped browser implementation."""

import shutil
import subprocess
from pathlib import Path

import pytest


def test_pose_edits_are_kept_per_instance():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required for the scene layout frontend regression")
    editor = Path("src/assets_generator/resources/scene-layout-editor.html").read_text()
    default_pose = editor.split("function defaultPose(){", 1)[1].split("function updatePose", 1)[0]
    update_pose = editor.split("function updatePose(id,field,value){", 1)[1].split(
        "function showPose", 1
    )[0]
    script = """
const assert=require('node:assert/strict');
let poses={chair:defaultPose(),table:defaultPose()},applied=[];
function applyPose(id){applied.push(id)}
function defaultPose(){
"""
    script += default_pose
    script += "\nfunction updatePose(id,field,value){" + update_pose
    script += """
updatePose('chair','x',2.5);
updatePose('chair','yaw',45);
updatePose('table','scale',0.75);
assert.equal(poses.chair.translation.x,2.5);
assert.equal(poses.chair.rotation_degrees.yaw,45);
assert.equal(poses.chair.scale,1);
assert.equal(poses.table.translation.x,0);
assert.equal(poses.table.scale,0.75);
assert.deepEqual(applied,['chair','chair','table']);
"""
    subprocess.run([node, "-e", script], check=True, capture_output=True, text=True, timeout=20)
    assert "GLTFLoader" in editor
    assert "OrbitControls" in editor
    assert "s*(cz*sy*sx-sz*cx)" in editor
    assert "post({reviewer,poses})" in editor
