import copy
import json
import threading
from http.client import HTTPConnection

import pytest
from test_dag_image_adapters import image_plan
from test_workbench_engine import fixture_engine

from assets_generator.contracts import ContractError
from assets_generator.dag_engine import DagEngine
from assets_generator.dag_persistence import DagRepository
from assets_generator.dag_review import DagMaskReviewService, create_dag_review_server


def setup_review(tmp_path):
    store, image, profile = fixture_engine(tmp_path)
    registry, plan = image_plan(profile)
    repo = DagRepository(store, tmp_path / "dag")
    repo.__enter__()
    engine = DagEngine(repo, registry)
    run = engine.drain(engine.create(plan, {"image": image}).run_id)
    return repo, engine, run


def preview(service):
    run = service.get_run()
    return service.preview(
        {
            "expected_revision": run["revision"],
            "proposal_id": "p0",
            "invert": False,
            "keep_largest": True,
        }
    )


def command(view):
    return {
        "expected_revision": view["revision"],
        "idempotency_key": "confirm",
        "reviewer": "Explicit test reviewer",
        "request": view["review"]["request"],
        "preview_signature": view["review"]["draft"]["preview_signature"],
        "final_mask": view["review"]["draft"]["final_mask"],
    }


def test_preview_restart_confirm_and_old_receipt_does_not_redispatch(tmp_path, monkeypatch):
    repo, engine, run = setup_review(tmp_path)
    try:
        service = DagMaskReviewService(engine, run.run_id, "choose_object")
        view = preview(service)
        assert service.output("preview").data.startswith(b"\x89PNG")
        restarted = DagMaskReviewService(engine, run.run_id, "choose_object")
        assert restarted.get_run()["review"]["draft"] == view["review"]["draft"]
        body = command(restarted.get_run())
        altered = copy.deepcopy(body)
        altered["final_mask"]["artifact_id"] = "sha256:" + "0" * 64
        with pytest.raises(ContractError, match="saved preview"):
            restarted.decision(altered)
        restarted.decision(body)
        restarted.close()
        completed = repo.load(run.run_id)
        assert completed.status == "succeeded"
        outputs = restarted.get_run()["outputs"]
        assert any(item["key"].endswith("--glb") for item in outputs)
        glb = next(item["key"] for item in outputs if item["key"].endswith("--glb"))
        assert restarted.output(glb).data[:4] == b"glTF"
        decision = engine.store.read_structured(
            completed.dag.node_states["choose_object"].current().decision
        )
        assert decision["reviewer"] == body["reviewer"]
        assert decision["request"] == body["request"]
        monkeypatch.setattr(engine, "decide", lambda *a, **kw: pytest.fail("must not redispatch"))
        last = DagMaskReviewService(engine, run.run_id, "choose_object")
        assert last.decision(body)["status"] == "succeeded"
        with pytest.raises(ContractError):
            last.decision({**body, "reviewer": "other"})
    finally:
        repo.__exit__(None, None, None)


def test_confirmation_is_nonblocking_and_http_is_scoped(tmp_path, monkeypatch):
    repo, engine, run = setup_review(tmp_path)
    service = DagMaskReviewService(engine, run.run_id, "choose_object")
    view = preview(service)
    entered, release = threading.Event(), threading.Event()

    def slow(*args, **kwargs):
        entered.set()
        assert release.wait(10)

    monkeypatch.setattr(engine, "decide", slow)
    server = create_dag_review_server(service, 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    connection = HTTPConnection("127.0.0.1", server.server_port, timeout=3)

    def request(method, path, body=None, headers=None):
        connection.request(method, path, json.dumps(body) if body else None, headers or {})
        response = connection.getresponse()
        return response.status, response.read()

    try:
        status, page = request("GET", "/")
        assert status == 200 and b"review_only" in page
        _, raw = request("GET", "/session")
        session = json.loads(raw)
        assert session["catalog"]["review_only"]
        assert request("GET", "/runs/run_other")[0] == 404
        assert request("GET", "/session", headers={"Host": "evil.example"})[0] == 403
        url = f"/runs/{run.run_id}/decision"
        assert request("POST", url, command(view))[0] == 403
        headers = {
            "Origin": f"http://127.0.0.1:{server.server_port}",
            "X-Workbench-Token": session["token"],
            "Content-Type": "application/json",
        }
        assert request("POST", url, command(view), headers)[0] == 202
        assert entered.wait(2)
        assert request("GET", f"/runs/{run.run_id}")[0] == 200
        assert request("POST", url, command(view), headers)[0] == 202
    finally:
        release.set()
        service.close()
        connection.close()
        server.shutdown()
        server.server_close()
        thread.join()
        repo.__exit__(None, None, None)


def test_review_frontend_keeps_unacknowledged_key_and_shows_qualified_glb():
    from test_workbench_frontend import _run_js

    _run_js(r"""
const b=browser();b.run('reviewOnly=true');
const waiting={...runState('dag_a'),submission_pending:true,review:{confirmed:false,items:[]}};
b.fetch(()=>Promise.resolve(response(waiting)));
await b.run("durableCommand('/runs/dag_a/decision',{expected_revision:3,reviewer:'alice'})");
assert.ok(storage.get('workbench.pending'));
const done={...runState('dag_a',4),status:'succeeded',
 review:{confirmed:true,items:[],decision_key:'other'},
 outputs:[{key:'generate--glb',url:'/model'}]};
b.run(`render(${JSON.stringify(done)})`);
assert.ok(storage.get('workbench.pending'));
done.review.decision_key=b.run('pendingCommand.body.idempotency_key');
b.run(`render(${JSON.stringify(done)})`);
assert.equal(storage.get('workbench.pending'),undefined);
assert.equal(b.e('outputs').children.at(-1).textContent,'查看模型');
""")
