// Read-only browser acceptance against existing real published runs.
// URL FRESH_EVIDENCE_DIRECTORY RUN_ID [RUN_ID ...]
const fs = require("node:fs");
const assert = require("node:assert/strict");
const { chromium, expect } = require("@playwright/test");
(async () => {
  const [url, directory, ...ids] = process.argv.slice(2);
  assert.ok(url && directory && ids.length);
  fs.mkdirSync(directory, { recursive: false });
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({
    viewport: { width: 1440, height: 900 },
  });
  const mutations = [],
    errors = [],
    records = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  page.on("request", (r) => {
    if (
      r.method() !== "GET" &&
      /\/api\/runs(?:\/|$)/.test(new URL(r.url()).pathname)
    )
      mutations.push(r.url());
  });
  try {
    await page.goto(url);
    await page
      .getByRole("button", { name: "运行记录与诊断", exact: true })
      .click();
    for (const id of ids) {
      const beforeResponse = await page.request.get(`${url}/api/runs/${id}`);
      assert.equal(beforeResponse.status(), 200);
      const before = await beforeResponse.json();
      await page.getByLabel("选择运行", { exact: true }).selectOption(id);
      const output = before.outputs.find((o) => o.kind === "gltf_asset");
      assert.ok(output);
      await page
        .getByRole("button", {
          name: `预览模型 · ${output.node_id} · ${output.port}`,
          exact: true,
        })
        .click();
      const dialog = page.getByRole("dialog", {
        name: "模型预览",
        exact: true,
      });
      await expect(dialog.getByRole("status")).toContainText("模型已加载", {
        timeout: 30000,
      });
      await expect(dialog.locator("canvas")).toBeVisible();
      const canvas = dialog.locator("canvas");
      const initialView = await canvas.screenshot();
      const bounds = await canvas.boundingBox();
      assert.ok(bounds);
      await page.mouse.move(bounds.x + bounds.width / 2, bounds.y + bounds.height / 2);
      await page.mouse.down();
      await page.mouse.move(bounds.x + bounds.width / 2 + 100, bounds.y + bounds.height / 2 + 30, { steps: 8 });
      await page.mouse.up();
      await expect.poll(async () => !(await canvas.screenshot()).equals(initialView)).toBe(true);
      await page.screenshot({ path: `${directory}/${id}-rotated.png` });
      await dialog.getByRole("button", { name: "重置视角", exact: true }).click();
      await page.screenshot({ path: `${directory}/${id}.png` });
      await dialog
        .getByRole("button", { name: "关闭模型预览", exact: true })
        .click();
      const after = await (
        await page.request.get(`${url}/api/runs/${id}`)
      ).json();
      assert.deepEqual(after.run.dag.node_states, before.run.dag.node_states);
      records.push({ run_id: id, snapshot: before.snapshot_ref, output });
    }
    assert.deepEqual(mutations, []);
    assert.deepEqual(errors, []);
    fs.writeFileSync(
      `${directory}/acceptance.json`,
      JSON.stringify({ records, mutations, errors }, null, 2),
    );
    console.log("PASS real published previews; no run mutation", ids);
  } catch (e) {
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
