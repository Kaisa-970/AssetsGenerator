"""Execute the shipped editor handlers with a small DOM/scene harness in Node."""

import shutil
import subprocess
from pathlib import Path

import pytest


def test_restore_clears_preview_before_review():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required for the editor handler regression")
    editor = Path("src/assets_generator/resources/alignment-editor.html").read_text()
    restore = editor.split("$('restore').onclick=", 1)[1].split("$('select-alignment').onclick", 1)[
        0
    ]
    clear = editor.split("function clearPreview(){", 1)[1].split("$('return-layers')", 1)[0]
    review = editor.split("for(const [id,decision] of", 1)[1].split("let historyData=", 1)[0]
    script = r"""
const assert=require('node:assert/strict');
const elements=new Map();
const $=id=>{if(!elements.has(id))elements.set(id,{value:'',checked:true});return elements.get(id)};
const status={textContent:''};
const previewA={traverse(fn){fn({isMesh:true,
 geometry:{dispose(){disposed++}},material:{dispose(){disposed++}}})}};
let disposed=0,removed=null,previewGroup=previewA;
let saved={alignment:{artifact_id:'A'}},selection={artifact_id:'selection-A'},busy=false;
const scene={remove(group){removed=group}};
const sourceGroup={visible:false,matrix:{
 set(...args){this.elements=args},
 decompose(p,q,s){Object.assign(p,{x:7,y:0,z:0});Object.assign(s,{x:1})}
 },updateMatrixWorld(){}};
const targetGroup={visible:false};
const THREE={Vector3:class {},Quaternion:class {},
 Euler:class {setFromQuaternion(){return {x:0,y:0,z:0}}}};
const matrixB=[[1,0,0,7],[0,1,0,0],[0,0,1,0],[0,0,0,1]];
const requests=[];
let failRestore=false;
async function post(path,body){
 requests.push({path,body});
 if(path==='/restore'){
   if(failRestore)throw Error('restore failed');
   return {alignment:{artifact_id:'B'},matrix:matrixB};
 }
 assert.equal(body.alignment_id,'B');
 assert.equal(previewGroup,null);
 assert.equal(sourceGroup.visible,true);
 assert.equal(targetGroup.visible,true);
 return {review:{artifact_id:'review-B'}};
}
async function action(fn){await fn()}
function lock(on){busy=on}
function fit(){}
$('alignments').value='B';$('reviewer').value='tester';
"""
    script += "\nfunction clearPreview(){" + clear
    script += "\n$('restore').onclick=" + restore
    script += "\nfor(const [id,decision] of" + review
    script += r"""
(async()=>{
 // Failed restore keeps the old displayed result and association intact.
 failRestore=true;
 await assert.rejects($('restore').onclick(),/restore failed/);
 assert.equal(previewGroup,previewA);assert.equal(saved.alignment.artifact_id,'A');
 failRestore=false;
 await $('restore').onclick();
 assert.equal(removed,previewA);assert.equal(disposed,2);
 assert.equal(previewGroup,null);assert.equal(selection,null);
 assert.equal(saved.alignment.artifact_id,'B');
 assert.deepEqual(sourceGroup.matrix.elements,matrixB.flat());
 assert.equal(sourceGroup.visible,true);assert.equal(targetGroup.visible,true);
 await $('accept').onclick();
 assert.equal(requests.at(-1).path,'/review');
 assert.equal(requests.at(-1).body.alignment_id,'B');
 assert.equal(requests.at(-1).body.decision,'accepted');
 // Restoring respects intentional layer visibility preferences.
 $('show-target').checked=false;
 await $('restore').onclick();
 assert.equal(targetGroup.visible,false);assert.equal(sourceGroup.visible,true);
})().catch(error=>{console.error(error);process.exitCode=1});
"""
    subprocess.run([node, "-e", script], check=True, capture_output=True, text=True, timeout=20)
