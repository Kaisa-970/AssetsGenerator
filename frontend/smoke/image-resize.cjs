// Run against examples/cpu_image_editor.py: URL RGB_IMAGE EXTERNAL_EVIDENCE_DIRECTORY
const fs = require("node:fs");
const assert = require("node:assert/strict");
const { chromium, expect } = require("@playwright/test");
(async () => {
  const [url, input, directory] = process.argv.slice(2);
  const evidence = `${directory}/resize-browser.json`;
  assert.ok(!fs.existsSync(evidence), "Use a fresh evidence directory");
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage({
      viewport: { width: 1600, height: 1000 },
    });
    const errors = [];
    page.on("pageerror", (error) => errors.push(String(error)));
    page.on("dialog", (dialog) => dialog.accept());
    await page.goto(url);
    await page
      .getByRole("button", { name: "cpu-image-editor", exact: false })
      .click();
    for (const [index, size] of [
      [0, [64, 32]],
      [1, [96, 48]],
    ]) {
      const id = index ? "resize_image_2" : "resize_image";
      await page
        .locator(".catalog-item")
        .filter({ has: page.getByText("resize_image", { exact: true }) })
        .click();
      await page.waitForTimeout(300); // Wait for the catalog insertion's fitView animation.
      await page
        .locator(
          '.react-flow__node[data-id="encode"] .react-flow__handle.source',
        )
        .dragTo(
          page.locator(
            `.react-flow__node[data-id="${id}"] .react-flow__handle.target`,
          ),
        );
      await expect(page.locator(".react-flow__edge")).toHaveCount(index + 2);
      await page.locator(`.react-flow__node[data-id="${id}"]`).click();
      await page
        .getByLabel("参数 width", { exact: true })
        .fill(String(size[0]));
      await page.getByLabel("参数 width", { exact: true }).blur();
      await page
        .getByLabel("参数 height", { exact: true })
        .fill(String(size[1]));
      await page.getByLabel("参数 height", { exact: true }).blur();
      await page
        .getByLabel("参数 resampling", { exact: true })
        .selectOption("nearest");
    }
    await page.getByRole("button", { name: "编译校验", exact: true }).click();
    await expect(page.getByRole("status")).toContainText(
      "编译通过；可在运行页创建新运行",
    );
    await page.getByRole("button", { name: "运行", exact: true }).click();
    await page.getByLabel("图片来源").selectOption("upload");
    await page.getByLabel("上传运行图片").setInputFiles(input);
    const response = page.waitForResponse(
      (r) => r.url().endsWith("/api/runs") && r.request().method() === "POST",
    );
    await page
      .getByRole("button", { name: "启动新运行 · 检查执行范围", exact: true })
      .click();
    await page
      .getByRole("button", { name: "确认执行上述范围", exact: true })
      .click();
    const started = await response;
    assert.equal(started.status(), 202);
    const created = await started.json();
    fs.writeFileSync(evidence, JSON.stringify({ created }, null, 2), {
      flag: "wx",
    });
    const id = created.run.run_id;
    const read = async () => {
      const result = await page.request.get(`${url}/api/runs/${id}`);
      assert.ok(result.ok());
      return result.json();
    };
    await expect.poll(async () => (await read()).run.status).toBe("succeeded");
    const completed = await read();
    for (const [node, size] of [
      ["resize_image", [64, 32]],
      ["resize_image_2", [96, 48]],
    ]) {
      await page
        .getByRole("button", {
          name: `预览图片 · ${node} · image`,
          exact: true,
        })
        .click();
      const image = page.getByRole("img", {
        name: `节点 ${node} 的 image 输出`,
        exact: true,
      });
      await expect(image).toBeVisible();
      await expect
        .poll(() =>
          image.evaluate((img) => [img.naturalWidth, img.naturalHeight]),
        )
        .toEqual(size);
      const attempt = completed.run.dag.node_states[node].attempts[0];
      assert.deepEqual(
        attempt.resolved_inputs.image,
        completed.run.dag.node_states.encode.attempts[0].outputs.image,
      );
    }
    const resumed = page.waitForResponse(
      (r) =>
        r.url().endsWith(`/api/runs/${id}/resume`) &&
        r.request().method() === "POST",
    );
    await page
      .getByRole("button", { name: "恢复 / 继续此运行", exact: true })
      .click();
    assert.equal((await resumed).status(), 202);
    await expect.poll(async () => (await read()).busy).toBe(false);
    const restored = await read();
    assert.equal(restored.run.status, "succeeded");
    assert.deepEqual(
      restored.run.dag.node_states,
      completed.run.dag.node_states,
    );
    assert.ok(
      Object.values(restored.run.dag.node_states).every(
        (s) => s.attempts.length === 1,
      ),
    );
    assert.deepEqual(errors, []);
    fs.writeFileSync(
      evidence,
      JSON.stringify({ created, completed, restored, errors }, null, 2),
    );
    await page.screenshot({ path: `${directory}/resize-browser.png` });
    console.log(
      id,
      "manual fanout, parameter editing, preview sizes and recovery verified",
    );
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
