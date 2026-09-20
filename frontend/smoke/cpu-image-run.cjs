// Run against examples/cpu_image_editor.py. Usage: node cpu-image-run.cjs URL IMAGE [manual]
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
    const manual = process.argv[4] === "manual";
    const nodeId = manual ? "encode_png" : "encode";
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
      await page
        .locator(".catalog-item")
        .filter({ has: page.getByText("encode_png", { exact: true }) })
        .click();
      const nodeHeader = page.locator(
        '.react-flow__node[data-id="encode_png"] .node-top',
      );
      const box = await nodeHeader.boundingBox();
      assert.ok(box);
      await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
      await page.mouse.down();
      await page.mouse.move(
        box.x + box.width / 2 + 260,
        box.y + box.height / 2 + 220,
        { steps: 15 },
      );
      await page.mouse.up();
      const source = page.locator(
        '.react-flow__node[data-id="input:image"] .react-flow__handle.source',
      );
      const target = page.locator(
        '.react-flow__node[data-id="encode_png"] .react-flow__handle.target',
      );
      await source.dragTo(target);
      await expect(page.locator(".react-flow__edge")).toHaveCount(1);
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
      after.run.dag.node_states[nodeId].attempts,
      before.run.dag.node_states[nodeId].attempts,
    );
    assert.deepEqual(errors, []);
    console.log(
      JSON.stringify({
        run_id: id,
        status: after.run.status,
        attempts: after.run.dag.node_states[nodeId].attempts.length,
      }),
    );
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
