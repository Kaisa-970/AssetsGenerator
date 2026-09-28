// Prepare-only acceptance: real input binding and preflight; never dispatches a run.
// Usage: node node-model-iteration.cjs URL FRESH_EVIDENCE_DIRECTORY IMAGE_FILE SOURCE_RUN_ID NEXT_SEED
const fs = require("node:fs");
const assert = require("node:assert/strict");
const { chromium, expect } = require("@playwright/test");
(async () => {
  const [url, directory, image, id, nextSeed] = process.argv.slice(2);
  assert.ok(url && directory && image && id && nextSeed);
  const seed = Number(nextSeed);
  assert.ok(Number.isInteger(seed) && seed >= 0 && seed <= 4294967295);
  fs.mkdirSync(directory, { recursive: false });
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({
    viewport: { width: 1920, height: 1080 },
  });
  const blocked = [],
    errors = [];
  const save = (name, value) =>
    fs.writeFileSync(
      `${directory}/${name}.json`,
      JSON.stringify(value, null, 2),
    );
  page.on("pageerror", (e) => errors.push(String(e)));
  await page.route("**/api/runs**", (route) => {
    if (route.request().method() !== "GET") {
      blocked.push({
        url: route.request().url(),
        method: route.request().method(),
      });
      return route.abort("blockedbyclient");
    }
    return route.continue();
  });
  const get = async (path) => {
    const r = await page.request.get(`${url}${path}`);
    assert.equal(r.status(), 200);
    return r.json();
  };
  try {
    const before = await get(`/api/runs/${id}`);
    save("before", before);
    const plan = await get(`/api/runs/${id}/plan`);
    const sourceParameters = plan.bindings.shape.parameters;
    assert.equal(sourceParameters.pipeline_type, "512");
    assert.notEqual(sourceParameters.seed, seed);
    save("source-plan", plan);
    const text = await get(
      `/api/inputs/text?artifact_id=${encodeURIComponent(before.run.dag.named_actual_inputs.text.artifact_id)}`,
    );
    await page.goto(url);
    await page
      .getByRole("button", { name: "运行记录与诊断", exact: true })
      .click();
    await page.getByLabel("选择运行", { exact: true }).selectOption(id);
    await page
      .getByRole("button", { name: "将配置载入画布", exact: true })
      .click();
    await page.getByRole("button", { name: "继续替换", exact: true }).click();
    const dock = page.getByRole("button", {
      name: "运行记录与诊断",
      exact: true,
    });
    if ((await dock.getAttribute("aria-expanded")) === "true")
      await dock.click();
    await page
      .getByLabel("定位画布节点", { exact: true })
      .selectOption("input:image");
    await page
      .getByLabel("上传输入 image", { exact: true })
      .setInputFiles(image);
    await expect(
      page.getByLabel("输入 image Artifact ID", { exact: true }),
    ).toHaveValue(before.run.dag.named_actual_inputs.image.artifact_id);
    await page
      .getByLabel("定位画布节点", { exact: true })
      .selectOption("input:text");
    await page.getByLabel("文本输入 text", { exact: true }).fill(text.text);
    await page.getByRole("button", { name: "应用文本", exact: true }).click();
    await expect(
      page.getByLabel("输入 text Artifact ID", { exact: true }),
    ).toHaveValue(before.run.dag.named_actual_inputs.text.artifact_id);
    await page
      .getByLabel("定位画布节点", { exact: true })
      .selectOption("shape");
    await expect(page.getByLabel("节点参数 seed", { exact: true })).toHaveValue(
      String(sourceParameters.seed),
    );
    await page.getByLabel("节点参数 seed", { exact: true }).fill(String(seed));
    await page.getByLabel("节点参数 seed", { exact: true }).press("Enter");
    await expect(
      page.getByLabel("预览配置状态", { exact: true }),
    ).toContainText("结果过期");
    await page.screenshot({ path: `${directory}/changed-seed.png` });
    await page
      .getByRole("checkbox", {
        name: "复用所选运行的有效节点结果（后端核对身份与证据）",
        exact: true,
      })
      .check();
    const response = page.waitForResponse(
      (r) =>
        r.url().endsWith("/api/preflight") && r.request().method() === "POST",
    );
    await page
      .getByRole("button", {
        name: "重新执行受影响节点 · 检查执行范围",
        exact: true,
      })
      .click();
    const r = await response;
    const intent = r.request().postDataJSON(),
      report = await r.json();
    save("preflight", { status: r.status(), intent, report });
    assert.equal(r.status(), 200);
    assert.equal(intent.pipeline.nodes.shape.parameters.seed, seed);
    assert.deepEqual(intent.input_refs, before.run.dag.named_actual_inputs);
    assert.equal(report.execution_ready, true);
    assert.equal(report.nodes.segment.status, "reuse");
    assert.equal(report.nodes.extract.status, "reuse");
    assert.equal(report.nodes.shape.status, "execute");
    await expect(
      page.getByRole("button", { name: "确认执行上述范围", exact: true }),
    ).toBeEnabled();
    await page.screenshot({ path: `${directory}/preflight.png` });
    const after = await get(`/api/runs/${id}`);
    assert.deepEqual(after.run, before.run);
    assert.deepEqual(after.snapshot_ref, before.snapshot_ref);
    assert.deepEqual(blocked, []);
    assert.deepEqual(errors, []);
    save("acceptance", {
      mode: "prepare-only",
      source_run: id,
      seed,
      text: text.text,
      blocked,
      errors,
      report,
    });
    console.log("PASS prepare-only; no run submitted", directory);
  } catch (e) {
    save("failure", { error: String(e), blocked, errors });
    await page.screenshot({ path: `${directory}/failure.png` });
    fs.writeFileSync(
      `${directory}/failure.txt`,
      await page.locator("body").innerText(),
    );
    throw e;
  } finally {
    await browser.close();
  }
})().catch((e) => {
  console.error(e);
  process.exitCode = 1;
});
