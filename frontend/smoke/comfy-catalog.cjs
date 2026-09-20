// Real editor HTTP/catalog smoke. Does not submit inference.
const assert = require("node:assert/strict");
const { chromium, expect } = require("@playwright/test");
(async () => {
  const url = process.argv[2];
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage({
      viewport: { width: 1600, height: 1000 },
    });
    const errors = [];
    page.on("pageerror", (e) => errors.push(String(e)));
    page.on("dialog", (dialog) => dialog.accept());
    await page.goto(url);
    await page
      .getByRole("button", { name: "comfy_image_chain_v1", exact: true })
      .click();
    await expect(page.locator(".react-flow__node")).toHaveCount(4);
    await page.locator('.react-flow__node[data-id="first"]').click();
    await expect(page.getByLabel("节点 Backend", { exact: true })).toHaveValue(
      "comfy_first",
    );
    await page
      .getByLabel("节点 Backend", { exact: true })
      .selectOption("comfy_second");
    const response = page.waitForResponse(
      (r) =>
        r.url().endsWith("/api/compile") && r.request().method() === "POST",
    );
    await page.getByRole("button", { name: "编译校验", exact: true }).click();
    const result = await (await response).json();
    assert.equal(result.ok, true, JSON.stringify(result));
    assert.equal(result.execution_ready, true, JSON.stringify(result));
    assert.deepEqual(errors, []);
    console.log(
      JSON.stringify({
        ok: true,
        nodes: 3,
        backend: "comfy_second",
        execution_ready: true,
        inference_submitted: false,
      }),
    );
  } finally {
    await browser.close();
  }
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
