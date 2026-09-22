// Usage: node shape-compare.cjs CONFIG.json submit|complete
const fs = require("node:fs");
const assert = require("node:assert/strict");
const { chromium, expect } = require("@playwright/test");
(async () => {
  const config = JSON.parse(fs.readFileSync(process.argv[2]));
  const mode = process.argv[3];
  assert.ok(["submit", "complete"].includes(mode));
  const evidence = `${config.root}/browser-submitted.json`;
  if (mode === "submit")
    assert.ok(!fs.existsSync(evidence), "Do not resubmit an existing audit");
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage({
      viewport: { width: 1700, height: 1100 },
    });
    const errors = [];
    page.on("pageerror", (e) => errors.push(String(e)));
    page.on("dialog", (d) => d.accept());
    await page.goto(config.url);
    const settled = async (id) => {
      let value;
      await expect
        .poll(
          async () => {
            const response = await page.request.get(
              `${config.url}/api/runs/${id}`,
            );
            assert.equal(response.status(), 200);
            value = await response.json();
            assert.ok(!value.error, value.error);
            return value.busy;
          },
          { timeout: 120000 },
        )
        .toBe(false);
      return value;
    };
    if (mode === "submit") {
      assert.notEqual(config.first, config.second);
      await page
        .getByRole("button", { name: "remote-shape-compare", exact: false })
        .click();
      for (const [node, backend] of [
        ["first_shape", config.first],
        ["second_shape", config.second],
      ]) {
        await page.locator(`.react-flow__node[data-id="${node}"]`).click();
        await page
          .getByLabel("节点 Backend", { exact: true })
          .selectOption(backend);
      }
      const compiled = page.waitForResponse(
        (r) =>
          r.url().endsWith("/api/compile") && r.request().method() === "POST",
      );
      await page.getByRole("button", { name: "编译校验", exact: true }).click();
      assert.equal((await (await compiled).json()).execution_ready, true);
      await page.getByRole("button", { name: "运行", exact: true }).click();
      await page.getByLabel("图片来源").selectOption("upload");
      const upload = page.waitForResponse((r) =>
        r.url().endsWith("/api/inputs/rgba"),
      );
      await page.getByLabel("上传运行图片").setInputFiles(config.image);
      const uploaded = await upload;
      assert.ok(uploaded.ok());
      const identity = page.locator(".execution-panel .run-identity");
      await expect(identity).toContainText(/sha256:[a-f0-9]{64}/);
      const artifactId = (await identity.innerText()).match(
        /sha256:[a-f0-9]{64}/,
      )[0];
      const input = { image_ref: { artifact_id: artifactId } };
      const started = page.waitForResponse(
        (r) => r.url().endsWith("/api/runs") && r.request().method() === "POST",
      );
      await page
        .getByRole("button", { name: "启动新运行 · 检查执行范围", exact: true })
        .click();
      await page
        .getByRole("button", { name: "确认执行上述范围", exact: true })
        .click();
      const response = await started;
      assert.equal(response.status(), 202);
      const created = await response.json();
      fs.writeFileSync(evidence, JSON.stringify({ created, input }, null, 2), {
        flag: "wx",
      });
      const submitted = await settled(created.run.run_id);
      assert.equal(submitted.run.status, "running");
      assert.deepEqual(
        submitted.run.dag.named_actual_inputs.image,
        input.image_ref,
      );
      const bindings = [];
      for (const node of ["first_shape", "second_shape"]) {
        const state = submitted.run.dag.node_states[node];
        assert.equal(state.attempts.length, 1);
        assert.deepEqual(
          state.attempts[0].resolved_inputs.image,
          input.image_ref,
        );
        bindings.push(state.attempts[0].remote_binding);
      }
      assert.notEqual(bindings[0].service_id, bindings[1].service_id);
      assert.notEqual(bindings[0].submission_key, bindings[1].submission_key);
      assert.deepEqual(errors, []);
      fs.writeFileSync(
        evidence,
        JSON.stringify({ created, input, submitted, errors }, null, 2),
      );
      console.log(
        submitted.run.run_id,
        "submitted two independently bound jobs",
      );
    } else {
      const submitted = JSON.parse(fs.readFileSync(evidence));
      const id = submitted.created.run.run_id;
      await page.getByRole("button", { name: "运行", exact: true }).click();
      await page.getByLabel("选择运行", { exact: true }).selectOption(id);
      const resume = async () => {
        const response = page.waitForResponse(
          (r) =>
            r.url().endsWith(`/api/runs/${id}/resume`) &&
            r.request().method() === "POST",
        );
        await page
          .getByRole("button", { name: "恢复 / 继续此运行", exact: true })
          .click();
        assert.equal((await response).status(), 202);
        return settled(id);
      };
      const completed = await resume();
      assert.equal(completed.run.status, "succeeded");
      for (const state of Object.values(completed.run.dag.node_states)) {
        assert.equal(state.status, "succeeded");
        assert.equal(state.attempts.length, 1);
      }
      for (const node of ["first_shape", "second_shape"]) {
        assert.deepEqual(
          completed.run.dag.node_states[node].attempts[0].remote_binding,
          submitted.submitted.run.dag.node_states[node].attempts[0]
            .remote_binding,
        );
      }
      for (const branch of ["first", "second"]) {
        for (const port of ["glb", "release"])
          assert.ok(
            completed.outputs.some(
              (o) => o.node_id === `${branch}_publish` && o.port === port,
            ),
          );
      }
      for (const output of completed.outputs) {
        const response = await page.request.get(config.url + output.url);
        assert.ok(response.ok());
        const bytes = await response.body();
        assert.ok(bytes.length);
        if (output.port === "glb") {
          assert.equal(bytes.toString("ascii", 0, 4), "glTF");
          assert.equal(bytes.readUInt32LE(8), bytes.length);
        }
      }
      const restored = await resume();
      assert.equal(restored.run.status, "succeeded");
      assert.deepEqual(
        restored.run.dag.node_states,
        completed.run.dag.node_states,
      );
      assert.deepEqual(restored.outputs, completed.outputs);
      assert.deepEqual(errors, []);
      fs.writeFileSync(
        `${config.root}/browser-completed.json`,
        JSON.stringify({ completed, restored, errors }, null, 2),
        { flag: "wx" },
      );
      await page.screenshot({ path: `${config.root}/browser-completed.png` });
      console.log(id, "two releases downloaded; recovery preserved attempts");
    }
  } finally {
    await browser.close();
  }
})().catch((e) => {
  console.error(e);
  process.exitCode = 1;
});
