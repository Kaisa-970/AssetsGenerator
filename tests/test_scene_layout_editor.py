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


def test_selection_highlight_and_isolation_do_not_change_layout():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required for the scene layout frontend regression")
    editor = Path("src/assets_generator/resources/scene-layout-editor.html").read_text()
    body = editor.split("function updateSelection(){", 1)[1].split("function frameAll", 1)[0]
    script = (
        """
const assert=require('node:assert/strict');
const elements={};
const $=id=>elements[id]??=( {setAttribute(k,v){this[k]=v}} );
const objects={a:{visible:true},b:{visible:true}};
const poses={a:{x:0},b:{x:2}}, before=JSON.stringify(poses);
let selected='a',isolated=false,selectionBox,removed=[],disposed=0;
const scene={add(){},remove(box){removed.push(box)}};
const THREE={BoxHelper:class {
  constructor(object){this.object=object;this.material={}}
  dispose(){disposed++}
}};
function updateSelection(){
"""
        + body
    )
    script += """
updateSelection();
assert.equal(selectionBox.object,objects.a);
assert.equal(selectionBox.material.depthTest,false);
isolated=true; updateSelection();
assert.equal(objects.b.visible,false);
selected='b'; updateSelection();
assert.equal(objects.a.visible,false);
assert.equal(objects.b.visible,true);
assert.equal(selectionBox.object,objects.b);
assert.ok($('selection').textContent.includes('b'));
isolated=false; updateSelection();
assert.ok(objects.a.visible && objects.b.visible);
assert.equal(disposed,3);
assert.equal(removed.length,3);
assert.equal(JSON.stringify(poses),before);
assert.equal($('isolate')['aria-pressed'],'false');
"""
    subprocess.run([node, "-e", script], check=True, capture_output=True, text=True, timeout=20)
