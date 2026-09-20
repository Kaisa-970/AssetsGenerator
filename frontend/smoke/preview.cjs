const fs = require("node:fs");
const assert = require("node:assert/strict");
const { chromium, expect } = require("@playwright/test");
(async () => {
  const config = JSON.parse(fs.readFileSync(process.argv[2]));
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage({
      viewport: { width: 1600, height: 1000 },
    });
    const errors = [],
      mutations = [],
      resources = [];
    page.on("pageerror", (e) => errors.push(String(e)));
    page.on("request", (r) => {
      if (r.method() !== "GET") mutations.push(r.url());
      resources.push(r.url());
    });
    await page.goto(config.url);
    await page.getByRole("button", { name: "运行", exact: true }).click();
    await page.getByLabel("选择运行").selectOption(config.run_id);
    const endpoint = config.url + "/api/runs/" + config.run_id;
    const before = await (await page.request.get(endpoint)).json();
    await page
      .getByRole("button", {
        name: "预览模型 · " + config.node_id,
        exact: true,
      })
      .click();
    const dialog = page.getByRole("dialog", { name: "模型预览", exact: true });
    await expect(dialog.getByRole("status")).toContainText("模型已加载", {
      timeout: 60000,
    });
    await expect(dialog.locator("canvas")).toBeVisible();
    await dialog.getByRole("button", { name: "重置视角" }).click();
    await page.screenshot({ path: config.root + "/model-preview.png" });
    await dialog.getByRole("button", { name: "关闭模型预览" }).click();
    await expect(dialog).toHaveCount(0);
    const after = await (await page.request.get(endpoint)).json();
    assert.deepEqual(after.run, before.run);
    assert.deepEqual(errors, []);
    assert.deepEqual(mutations, []);
    assert.ok(resources.every((url) => url.startsWith(config.url)));
    fs.writeFileSync(
      config.root + "/preview-validation.json",
      JSON.stringify(
        {
          run_id: config.run_id,
          node_id: config.node_id,
          errors,
          mutations,
          run_unchanged: true,
          resources,
        },
        null,
        2,
      ),
    );
    console.log(
      config.run_id,
      "preview loaded; no mutations/errors; run unchanged",
    );
  } finally {
    await browser.close();
  }
})().catch((e) => {
  console.error(e);
  process.exitCode = 1;
});
