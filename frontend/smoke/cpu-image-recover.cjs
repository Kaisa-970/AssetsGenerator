// Usage: node cpu-image-recover.cjs URL capture RUN_ID NEW_SNAPSHOT_PATH
// After restarting the CPU example with the same directory:
// node cpu-image-recover.cjs URL recover SNAPSHOT_PATH
const fs = require("node:fs");
const assert = require("node:assert/strict");
const { chromium, expect } = require("@playwright/test");
(async () => {
  const [url, mode, argument, output] = process.argv.slice(2);
  assert.ok(["capture", "recover"].includes(mode));
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage();
    const read = async (id) => {
      const response = await page.request.get(`${url}/api/runs/${id}`);
      assert.equal(response.status(), 200);
      return response.json();
    };
    if (mode === "capture") {
      const snapshot = await read(argument);
      assert.equal(snapshot.run.status, "succeeded");
      assert.equal(snapshot.busy, false);
      fs.writeFileSync(output, JSON.stringify(snapshot, null, 2), {
        flag: "wx",
      });
      return;
    }
    const before = JSON.parse(fs.readFileSync(argument, "utf8"));
    const id = before.run.run_id;
    const errors = [],
      writes = [];
    page.on("pageerror", (e) => errors.push(String(e)));
    page.on("request", (request) => {
      if (request.method() !== "GET")
        writes.push(new URL(request.url()).pathname);
    });
    assert.deepEqual((await read(id)).run, before.run);
    await page.goto(url);
    await page.getByRole("button", { name: "运行", exact: true }).click();
    await page.getByLabel("选择运行", { exact: true }).selectOption(id);
    const response = page.waitForResponse(
      (r) =>
        r.request().method() === "POST" &&
        r.url().endsWith(`/api/runs/${id}/resume`),
    );
    await page
      .getByRole("button", { name: "恢复 / 继续此运行", exact: true })
      .click();
    assert.equal((await response).ok(), true);
    await expect.poll(async () => (await read(id)).busy).toBe(false);
    const after = await read(id);
    assert.equal(after.run.status, "succeeded");
    assert.equal(after.run.dag.plan_id, before.run.dag.plan_id);
    assert.deepEqual(
      after.run.dag.named_actual_inputs,
      before.run.dag.named_actual_inputs,
    );
    assert.deepEqual(after.run.dag.node_states, before.run.dag.node_states);
    assert.deepEqual(after.outputs, before.outputs);
    for (const item of after.outputs) {
      if (!["rgb_image", "rgba_image"].includes(item.kind)) continue;
      await page
        .getByRole("button", {
          name: `预览图片 · ${item.node_id} · ${item.port}`,
          exact: true,
        })
        .click();
      const image = page.getByRole("img", {
        name: `节点 ${item.node_id} 的 ${item.port} 输出`,
        exact: true,
      });
      await expect(image).toBeVisible();
      await expect
        .poll(() => image.evaluate((img) => img.naturalWidth))
        .toBeGreaterThan(0);
    }
    assert.deepEqual(writes, [`/api/runs/${id}/resume`]);
    assert.deepEqual(errors, []);
    console.log(
      JSON.stringify({
        run_id: id,
        status: after.run.status,
        preserved_nodes: Object.keys(after.run.dag.node_states),
      }),
    );
  } finally {
    await browser.close();
  }
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
