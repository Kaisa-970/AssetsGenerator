// Run against examples/cpu_image_editor.py. Usage: node cpu-image-run.cjs URL IMAGE
const assert = require("node:assert/strict");
const { chromium, expect } = require("@playwright/test");
(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage();
    const errors = [];
    page.on("dialog", (dialog) => dialog.accept());
    page.on("pageerror", (error) => errors.push(String(error)));
    const url = process.argv[2];
    await page.goto(url);
    await page
      .getByRole("button", { name: "cpu-image-editor", exact: true })
      .click();
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
      .getByRole("button", { name: "预览图片 · encode · image", exact: true })
      .click();
    const img = page.getByRole("img", {
      name: "节点 encode 的 image 输出",
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
      after.run.dag.node_states.encode.attempts,
      before.run.dag.node_states.encode.attempts,
    );
    assert.deepEqual(errors, []);
    console.log(
      JSON.stringify({
        run_id: id,
        status: after.run.status,
        attempts: after.run.dag.node_states.encode.attempts.length,
      }),
    );
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
