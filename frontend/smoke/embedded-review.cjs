const fs = require("fs");
const assert = require("node:assert/strict");
const { chromium, expect } = require("@playwright/test");
(async () => {
  const config = JSON.parse(fs.readFileSync(process.argv[2]));
  require("./claim-run.cjs").claimRun(config.root);
  const b = await chromium.launch({ headless: true });
  const p = await b.newPage({ viewport: { width: 1600, height: 1000 } });
  p.setDefaultTimeout(120000);
  const errors = [];
  p.on("pageerror", (e) => errors.push(String(e)));
  p.on("dialog", (d) => d.accept());
  await p.goto(config.url);
  const remoteSubmit = process.argv.includes("--remote-submit");
  await p
    .getByRole("button", {
      name: config.template || "dag-image-asset",
      exact: false,
    })
    .click();
  await p.getByRole("button", { name: "运行", exact: true }).click();
  let created;
  let uploaded;
  let originalRequest;
  const uploadRetry = process.argv.includes("--upload-retry");
  if (uploadRetry) {
    await p.getByLabel("图片来源", { exact: true }).selectOption("upload");
    await p.route(config.url + "/api/inputs/image", async (route) => {
      const response = await route.fetch();
      assert.equal(response.status(), 201);
      uploaded = await response.json();
      await route.fulfill({ response });
    });
    await p.getByLabel("上传运行图片").setInputFiles(config.image);
    await expect(
      p.getByText("图片已上传；点击启动新运行才会执行模型。", { exact: true }),
    ).toBeVisible();
    assert.ok(uploaded.image_ref.artifact_id);
    assert.deepEqual(
      await (await p.request.get(config.url + "/api/runs")).json(),
      { runs: [] },
    );
    // Let the real server commit and dispatch, then lose only the response.
    await p.route(config.url + "/api/runs", async (route) => {
      if (route.request().method() !== "POST") return route.continue();
      originalRequest = route.request().postDataJSON();
      const response = await route.fetch();
      assert.equal(response.status(), 202);
      created = await response.json();
      await route.abort("failed");
    });
    await p
      .getByRole("button", { name: "启动新运行 · 检查执行范围", exact: true })
      .click();
    await p
      .getByRole("button", { name: "重试原创建请求", exact: true })
      .waitFor();
    await expect(
      p.getByRole("button", { name: "重试原创建请求", exact: true }),
    ).toBeEnabled();
    await p.unroute(config.url + "/api/runs");
    await p.reload();
    await p.getByRole("button", { name: "运行", exact: true }).click();
    const replayResponse = p.waitForResponse(
      (r) =>
        r.url() === config.url + "/api/runs" && r.request().method() === "POST",
    );
    await p
      .getByRole("button", { name: "重试原创建请求", exact: true })
      .click();
    const replay = await replayResponse;
    assert.deepEqual(replay.request().postDataJSON(), originalRequest);
    assert.deepEqual(originalRequest.input_refs.image, uploaded.image_ref);
    assert.ok(originalRequest.preflight_digest);
    assert.equal(originalRequest.image_ref, undefined);
    assert.equal((await replay.json()).run.run_id, created.run.run_id);
    const runs = await (await p.request.get(config.url + "/api/runs")).json();
    assert.equal(runs.runs.length, 1);
  } else {
    await p.getByLabel("图片来源", { exact: true }).selectOption("path");
    await p.getByLabel("运行图片路径").fill(config.image);
    const creation = p.waitForResponse(
      (response) =>
        response.url() === config.url + "/api/runs" &&
        response.request().method() === "POST",
    );
    await p
      .getByRole("button", { name: "启动新运行 · 检查执行范围", exact: true })
      .click();
    created = await (await creation).json();
  }
  const runId = created.run.run_id;

  await p
    .getByRole("button", { name: "准备人工审查 · choose_object", exact: true })
    .click();
  await p
    .getByRole("button", { name: "在工作台审查 mask", exact: true })
    .click();
  const f = p.frameLocator('iframe[title="mask 审查 choose_object"]');
  await f.locator('input[name="proposal"]').first().check();
  await f.locator("#confirm:not([disabled])").waitFor();
  await f
    .locator("#reviewer")
    .fill("Codex automated browser smoke (not user approval)");
  await f.locator("#confirm").click();
  await p.getByRole("button", { name: "收起审查", exact: true }).click();
  if (remoteSubmit) {
    const job = p.getByLabel("远程作业标识 · shape", { exact: true });
    await expect(job).toHaveValue(/.+/);
    const response = await p.request.get(config.url + "/api/runs/" + runId);
    assert.equal(response.status(), 200);
    const result = await response.json();
    const nodes = result.run.dag.node_states;
    for (const name of ["candidates", "choose_object", "prepare"]) {
      assert.equal(nodes[name].status, "succeeded");
      assert.equal(nodes[name].attempts.length, 1);
    }
    assert.equal(nodes.shape.status, "running");
    assert.equal(nodes.shape.attempts.length, 1);
    assert.equal(Object.keys(result.run.dag.receipts).length, 1);
    assert.deepEqual(errors, []);
    assert.equal(
      nodes.shape.attempts[0].remote_binding.submission_key,
      await job.inputValue(),
    );
    // Save the exact parent and job before any separate service execution command.
    fs.writeFileSync(
      config.root + "/browser-submitted.json",
      JSON.stringify(
        {
          result,
          errors,
          uploaded,
          originalRequest,
          submission_key: await job.inputValue(),
          reviewer: "Codex automated browser smoke (not user approval)",
          acceptance:
            "submitted only; remote inference and release not verified",
        },
        null,
        2,
      ),
      { flag: "wx" },
    );
    await p.screenshot({ path: config.root + "/browser-submitted.png" });
    console.log(runId, "submitted", await job.inputValue());
    await b.close();
    return;
  }
  await p
    .getByRole("link", { name: "generate_asset · glb ↗", exact: true })
    .waitFor();
  await p.getByRole("button", { name: "查看固定运行图", exact: true }).click();
  await p
    .getByRole("dialog", { name: "固定运行图" })
    .locator('.react-flow__node[data-id="generate_asset"]')
    .filter({ hasText: "succeeded" })
    .waitFor();
  await p.screenshot({ path: config.root + "/embedded-completed.png" });
  const result = await (
    await p.request.get(config.url + "/api/runs/" + runId)
  ).json();
  if (
    result.run.run_id !== runId ||
    result.run.status !== "succeeded" ||
    errors.length ||
    Object.values(result.run.dag.node_states).some(
      (state) => state.status !== "succeeded" || state.attempts.length !== 1,
    ) ||
    Object.keys(result.run.dag.receipts).length !== 1
  )
    throw Error(JSON.stringify({ result, errors }));
  if (uploadRetry)
    assert.deepEqual(
      result.run.dag.named_actual_inputs.image,
      uploaded.image_ref,
    );
  for (const output of result.outputs) {
    const response = await p.request.get(config.url + output.url);
    assert.equal(response.status(), 200);
    assert.ok((await response.body()).length > 0);
  }
  fs.writeFileSync(
    config.root + "/embedded-result.json",
    JSON.stringify({ result, errors, uploaded, originalRequest }, null, 2),
  );
  console.log(
    result.run.run_id,
    result.run.status,
    "browser errors",
    errors.length,
  );
  await b.close();
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
