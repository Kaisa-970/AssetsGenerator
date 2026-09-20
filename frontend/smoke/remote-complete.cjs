// Continue an already submitted run. Never creates a run or submits a decision.
const fs = require("node:fs");
const assert = require("node:assert/strict");
const { chromium, expect } = require("@playwright/test");
(async () => {
  const config = JSON.parse(fs.readFileSync(process.argv[2]));
  const submitted = JSON.parse(fs.readFileSync(config.root + "/browser-submitted.json"));
  const runId = submitted.result.run.run_id;
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });
    const errors = [];
    page.on("pageerror", error => errors.push(String(error)));
    await page.goto(config.url);
    await page.getByRole("button", { name: "运行", exact: true }).click();
    await page.getByLabel("选择运行", { exact: true }).selectOption(runId);
    const resume = page.getByRole("button", { name: "恢复 / 继续此运行", exact: true });
    const read = async () => {
      const response = await page.request.get(config.url + "/api/runs/" + runId);
      assert.equal(response.status(), 200);
      return response.json();
    };
    const before = await read();
    assert.equal(before.run.dag.node_states.shape.attempts[0].remote_binding.submission_key,
      submitted.submission_key);
    await expect(resume).toBeEnabled();
    await resume.click();
    let completed;
    await expect.poll(async () => {
      completed = await read();
      if (["failed", "interrupted", "recovery_blocked"].includes(completed.run.status))
        throw Error(JSON.stringify(completed));
      return !completed.busy && completed.run.status;
    }, { timeout: 120000, intervals: [1000] }).toBe("succeeded");
    for (const state of Object.values(completed.run.dag.node_states)) {
      assert.equal(state.status, "succeeded");
      assert.equal(state.attempts.length, 1);
    }
    assert.deepEqual(completed.run.dag.receipts, submitted.result.run.dag.receipts);
    assert.deepEqual(completed.run.dag.node_states.shape.attempts[0].remote_binding,
      submitted.result.run.dag.node_states.shape.attempts[0].remote_binding);
    assert.ok(completed.outputs.some(o => o.node_id === "publish" && o.port === "glb"));
    assert.ok(completed.outputs.some(o => o.node_id === "publish" && o.port === "release"));
    for (const output of completed.outputs) {
      const response = await page.request.get(config.url + output.url);
      assert.equal(response.status(), 200);
      const bytes = await response.body();
      assert.ok(bytes.length > 0);
      if (output.port === "glb") {
        assert.equal(bytes.toString("ascii", 0, 4), "glTF");
        assert.equal(bytes.readUInt32LE(4), 2);
        assert.equal(bytes.readUInt32LE(8), bytes.length);
      }
    }
    await expect(resume).toBeEnabled();
    const resumed = page.waitForResponse(r => r.url().endsWith(`/api/runs/${runId}/resume`));
    await resume.click();
    assert.equal((await resumed).status(), 202);
    let restored;
    await expect.poll(async () => {
      restored = await read();
      return !restored.busy && restored.run.status;
    }, { timeout: 120000, intervals: [1000] }).toBe("succeeded");
    assert.deepEqual(restored.run.dag.node_states, completed.run.dag.node_states);
    assert.deepEqual(restored.run.dag.receipts, completed.run.dag.receipts);
    assert.deepEqual(restored.outputs, completed.outputs);
    assert.deepEqual(errors, []);
    await page.screenshot({ path: config.root + "/browser-completed.png" });
    fs.writeFileSync(config.root + "/browser-completed.json", JSON.stringify({
      completed, restored, errors,
      reviewer: submitted.reviewer,
      acceptance: "browser continuation, output download and completed recovery; model quality not assessed",
    }, null, 2), { flag: "wx" });
    console.log(runId, "completed and restored; outputs verified");
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
