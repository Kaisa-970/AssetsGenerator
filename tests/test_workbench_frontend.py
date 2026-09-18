"""Run shipped workbench JavaScript in Node VM with a minimal browser boundary."""

# Embedded browser fixture code is intentionally kept as JavaScript.
# ruff: noqa: E501

import json
import shutil
import subprocess
from pathlib import Path

import pytest

_HARNESS = r"""
const vm=require('node:vm'),assert=require('node:assert/strict');
const htmlScript=SOURCE;
const storage=new Map();
function deferred(){let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b});return {promise,resolve,reject}}
function browser(){
 const elements={},controls=[],models=[];
 function element(tag='div'){
  const e={tag,value:'',checked:false,disabled:false,hidden:false,files:[],dataset:{},children:[],textContent:'',
   append(...items){this.children.push(...items)},replaceChildren(...items){this.children=items},
   querySelector(){return this.span??=(element('span'))},setAttribute(k,v){this[k]=v},
   addEventListener(){},remove(){const i=models.indexOf(this);if(i>=0)models.splice(i,1)},after(item){models.push(item)},
   getContext(){return {clearRect(){},drawImage(){},getImageData(){return {data:[]}},putImageData(){}}}};
  if(['button','input','select'].includes(tag))controls.push(e);return e;
 }
 const inputIds=['photo','invert','largest','reviewer'];const buttons=['create','preview','confirm','retry'];
 const document={getElementById(id){return elements[id]??=(element(inputIds.includes(id)?'input':buttons.includes(id)?'button':['runs','backend'].includes(id)?'select':'div'))},
  createElement:element,createTextNode(text){return {textContent:text}},
  querySelector(s){return s==='model-viewer'?models[0]:controls.find(e=>e.name==='proposal'&&e.checked)},
  querySelectorAll(s){return controls.filter(e=>s.split(',').includes(e.tag))}};
 for(const id of [...inputIds,...buttons,'runs','backend'])document.getElementById(id);
 let nextFetch=()=>Promise.reject(Error('unexpected fetch'));
 const ctx={document,console,JSON,Error,Map,crypto:{randomUUID:()=>Math.random().toString(36)},
  sessionStorage:{getItem:k=>storage.get(k)??null,setItem:(k,v)=>storage.set(k,v),removeItem:k=>storage.delete(k)},
  fetch:(...args)=>nextFetch(...args),setInterval(){},Option:function(text,value){return {text,value}},
  customElements:{get:()=>undefined},Image:class {},loadModelModule:()=>Promise.resolve(),
 };
 vm.createContext(ctx);
 // Replace only browser startup/network module loader, retaining shipped handlers/state.
 const script=htmlScript.slice(0,htmlScript.indexOf('\n(async()=>{'))
  .replace(/import\('https:\/\/ajax.googleapis.com\/[^']+'\)/g,'loadModelModule()');
 vm.runInContext(script,ctx);
 return {ctx,document,controls,models,e:id=>document.getElementById(id),
  run:code=>vm.runInContext(code,ctx),fetch:fn=>{nextFetch=fn}};
}
function runState(id,revision=1){return {run_id:id,revision,status:'waiting_for_input',stages:[],outputs:[]}}
const response=value=>({ok:true,json:async()=>value});
(async()=>{TEST})().catch(error=>{console.error(error);process.exitCode=1});
"""


def _run_js(test):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required for browser state regressions")
    html = Path("src/assets_generator/resources/node-workbench.html").read_text()
    source = html.split("<script>", 1)[1].split("</script>", 1)[0]
    script = _HARNESS.replace("SOURCE", json.dumps(source)).replace("TEST", test)
    result = subprocess.run([node, "-e", script], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr


def test_late_poll_cannot_replace_new_run_or_revision():
    _run_js(r"""
const b=browser();b.run(`render(${JSON.stringify(runState('run_a',2))})`);
const old=deferred();b.fetch(()=>old.promise);const refresh=b.run('refresh()');
b.run(`render(${JSON.stringify(runState('run_b',1))})`);
old.resolve(response(runState('run_a',3)));await refresh;
assert.equal(b.run('current.run_id'),'run_b');
const stale=deferred();b.fetch(()=>stale.promise);const again=b.run('refresh()');
b.run(`render(${JSON.stringify(runState('run_b',5))})`);
stale.resolve(response(runState('run_b',2)));await again;
assert.equal(b.run('current.revision'),5);
""")


def test_preview_disables_inputs_until_response():
    _run_js(r"""
const b=browser();b.run(`render(${JSON.stringify(runState('run_a'))})`);
const radio=b.document.createElement('input');radio.name='proposal';radio.value='p1';radio.checked=true;
const pending=deferred();b.fetch(path=>path.endsWith('mask-preview')?pending.promise:Promise.resolve(response(runState('run_a',2))));
const request=b.e('preview').onclick();
for(const id of ['invert','largest','runs','photo','backend'])assert.equal(b.e(id).disabled,true,id);
assert.equal(radio.disabled,true);
pending.resolve(response({}));await request;
assert.equal(b.e('invert').disabled,false);
""")


def test_late_model_loader_cannot_attach_previous_run():
    _run_js(r"""
const b=browser();b.run(`render(${JSON.stringify(runState('run_a'))})`);
const pending=deferred();b.ctx.loadModelModule=()=>pending.promise;
const showing=b.run("showModel('/runs/run_a/outputs/glb')");
b.run(`render(${JSON.stringify(runState('run_b'))})`);
pending.resolve();await showing;
assert.equal(b.models.length,0);
""")


def test_pending_command_body_and_key_survive_reload_and_revision_change():
    _run_js(r"""
const first=browser();let initial;
first.fetch((path,options)=>{initial={path,body:JSON.parse(options.body)};return Promise.reject(Error('response lost'))});
await assert.rejects(first.run("durableCommand('/runs/run_a/decision',{expected_revision:3,reviewer:'alice'})"));
assert.ok(storage.get('workbench.pending'));
const second=browser();let replay;
second.fetch((path,options)=>{replay={path,body:JSON.parse(options.body)};return Promise.resolve(response(runState('run_a',8)))});
await second.run("durableCommand('/runs/run_a/decision',{expected_revision:8,reviewer:'alice'})");
assert.deepEqual(replay,initial);
assert.equal(storage.has('workbench.pending'),false);
""")


def test_pending_command_rejects_different_business_request():
    _run_js(r"""
const b=browser();let calls=0;
b.fetch(()=>{calls++;return Promise.reject(Error('response lost'))});
await assert.rejects(b.run("durableCommand('/runs/run_a/decision',{expected_revision:3,reviewer:'alice'})"));
await assert.rejects(b.run("durableCommand('/runs/run_a/decision',{expected_revision:4,reviewer:'bob'})"));
assert.equal(calls,1);
""")


def test_failed_preview_image_never_enables_confirmation():
    _run_js(r"""
const b=browser();
b.ctx.Image=class{set src(value){queueMicrotask(()=>this.onerror())}};
b.run(`render(${JSON.stringify({...runState('run_a'),image_url:'/image',review:{items:[{proposal_id:'p1',area:4}],draft:{proposal_id:'p1',invert:false,keep_largest:false,mask_url:'/mask'}}})})`);
await new Promise(resolve=>setImmediate(resolve));
assert.equal(b.e('confirm').disabled,true);
assert.equal(b.run('previewLoaded'),false);
""")


def test_explicit_rejection_clears_pending_command():
    _run_js(r"""
const b=browser();b.fetch(()=>Promise.resolve({ok:false,status:409,json:async()=>({error:'stale revision'})}));
await assert.rejects(b.run("durableCommand('/runs/run_a/retry',{expected_revision:3})"));
assert.equal(storage.get('workbench.pending'),undefined);
assert.equal(b.run('pendingCommand'),null);
""")
