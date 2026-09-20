// Run against examples/cpu_image_editor.py. Usage: node cpu-image-run.cjs URL IMAGE [manual|fanout]
const assert = require("node:assert/strict");
const { chromium, expect } = require("@playwright/test");
(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage({
      viewport: { width: 1600, height: 1000 },
    });
    const errors = [];
    page.on("dialog", (dialog) => dialog.accept());
    page.on("pageerror", (error) => errors.push(String(error)));
    const url = process.argv[2];
    await page.goto(url);
    const fanout = process.argv[4] === "fanout";
    const manual = fanout || process.argv[4] === "manual";
    const nodeIds = fanout
      ? ["encode_png", "encode_png_2"]
      : [manual ? "encode_png" : "encode"];
    if (manual) {
      await page.locator('.react-flow__node[data-id="input:image"]').click();
      await page.getByLabel("输入契约 · JSON").fill(
        JSON.stringify({
          kind: "rgb_image",
          carriers: ["artifact_ref"],
          schema_name: "raster_image",
          schema_version: "1.0",
        }),
      );
      await page
        .getByRole("button", { name: "应用输入契约", exact: true })
        .click();
      for (const [index, nodeId] of nodeIds.entries()) {
        await page
          .locator(".catalog-item")
          .filter({ has: page.getByText("encode_png", { exact: true }) })
          .click();
        const source = page.locator(
          '.react-flow__node[data-id="input:image"] .react-flow__handle.source',
        );
        const target = page.locator(
          `.react-flow__node[data-id="${nodeId}"] .react-flow__handle.target`,
        );
        await page.waitForTimeout(250);
        await source.dragTo(target);
        await expect(page.locator(".react-flow__edge")).toHaveCount(index + 1);
      }
      await page.getByRole("button", { name: "编译校验", exact: true }).click();
      await expect(page.getByRole("status")).toContainText(
        "编译通过；可在运行页创建新运行",
      );
    } else {
      await page
        .getByRole("button", { name: "cpu-image-editor", exact: true })
        .click();
    }
    await page.getByRole("button", { name: "运行", exact: true }).click();
    await page.getByLabel("图片来源").selectOption("upload");
    await page.getByLabel("上传运行图片").setInputFiles(process.argv[3]);
    const start = page.getByRole("button", { name: "启动新运行", exact: true });
    await expect(start).toBeEnabled();
    const response = page.waitForResponse(
      (r) => r.url().endsWith("/api/runs") && r.request().method() === "POST",
    );
    await start.click();
    const result = await response;
    assert.equal(result.status(), 202, await result.text());
    const id = (await result.json()).run.run_id;
    const snapshot = async () =>
      (await page.request.get(`${url}/api/runs/${id}`)).json();
    await expect
      .poll(async () => (await snapshot()).run.status)
      .toBe("succeeded");
    for (const nodeId of nodeIds) {
      await page
        .getByRole("button", {
          name: `预览图片 · ${nodeId} · image`,
          exact: true,
        })
        .click();
      const img = page.getByRole("img", {
        name: `节点 ${nodeId} 的 image 输出`,
        exact: true,
      });
      await expect(img).toBeVisible();
      await expect
        .poll(() => img.evaluate((el) => el.naturalWidth))
        .toBeGreaterThan(0);
    }
    const before = await snapshot();
    await page.reload();
    await page.getByRole("button", { name: "运行", exact: true }).click();
    await page.getByLabel("选择运行", { exact: true }).selectOption(id);
    await page
      .getByRole("button", { name: "恢复 / 继续此运行", exact: true })
      .click();
    await expect.poll(async () => (await snapshot()).busy).toBe(false);
    const after = await snapshot();
    assert.equal(after.run.status, "succeeded");
    assert.deepEqual(
      Object.keys(after.run.dag.node_states).sort(),
      [...nodeIds].sort(),
    );
    for (const nodeId of nodeIds) {
      assert.equal(after.run.dag.node_states[nodeId].attempts.length, 1);
      assert.deepEqual(
        after.run.dag.node_states[nodeId].attempts,
        before.run.dag.node_states[nodeId].attempts,
      );
    }
    if (fanout) {
      const states = after.run.dag.node_states;
      assert.deepEqual(
        states[nodeIds[0]].attempts[0].resolved_inputs.image,
        after.run.dag.named_actual_inputs.image,
      );
      assert.ok(
        states[nodeIds[0]].attempts[0].resolved_inputs.image.artifact_id,
      );
      assert.deepEqual(
        states[nodeIds[0]].attempts[0].resolved_inputs,
        states[nodeIds[1]].attempts[0].resolved_inputs,
      );
    }
    let inspectionMutations = 0;
    const countMutation = (request) => {
      if (request.method() !== "GET") inspectionMutations++;
    };
    page.on("request", countMutation);
    await page
      .getByText("执行记录 · 1 次", { exact: true })
      .evaluateAll((items) => items.forEach((item) => item.click()));
    for (const nodeId of nodeIds) {
      const summary = page.getByText(`输入输出证据 · ${nodeId} · #1`, {
        exact: true,
      });
      await summary.click();
      const evidence = summary.locator("..").locator("pre");
      const displayed = JSON.parse(await evidence.innerText());
      const attempt = after.run.dag.node_states[nodeId].attempts[0];
      assert.deepEqual(displayed.resolved_inputs, attempt.resolved_inputs);
      assert.deepEqual(displayed.outputs, attempt.outputs);
      assert.deepEqual(displayed.provenance, attempt.provenance);
      assert.equal(displayed.binding_digest, attempt.binding_digest);
    }
    const planResponse = await page.request.get(`${url}/api/runs/${id}/plan`);
    assert.equal(planResponse.status(), 200);
    const plan = await planResponse.json();
    assert.equal(plan.plan_id, after.run.dag.plan_id);
    await page
      .getByRole("button", { name: "查看固定运行图", exact: true })
      .click();
    const dialog = page.getByRole("dialog", {
      name: "固定运行图",
      exact: true,
    });
    for (const nodeId of nodeIds) {
      await dialog.locator(`.react-flow__node[data-id="${nodeId}"]`).click();
      const details = dialog.locator(".run-binding-details pre");
      await expect
        .poll(async () => JSON.parse(await details.innerText()))
        .toEqual({
          operator: "encode_png@1",
          ...plan.bindings[nodeId],
        });
      assert.match(
        plan.bindings[nodeId].implementation_digest,
        /^sha256:[0-9a-f]{64}$/,
      );
    }
    await page.getByRole("button", { name: "关闭运行图", exact: true }).click();
    assert.deepEqual((await snapshot()).run, after.run);
    assert.equal(inspectionMutations, 0);
    page.off("request", countMutation);
    assert.deepEqual(errors, []);
    console.log(
      JSON.stringify({
        run_id: id,
        status: after.run.status,
        nodes: nodeIds,
        attempts_per_node: 1,
      }),
    );
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
