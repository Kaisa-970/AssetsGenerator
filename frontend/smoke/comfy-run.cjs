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
    page.on("dialog", (d) => d.accept());
    const errors = [];
    page.on("pageerror", (e) => errors.push(String(e)));
    await page.goto(config.url);
    let id;
    if (process.argv[3] === "start") {
      await page
        .getByRole("button", { name: "comfy_image_chain_v1", exact: true })
        .click();
      await page.getByRole("button", { name: "运行", exact: true }).click();
      await page.getByLabel("运行图片路径").fill(config.image);
      const response = page.waitForResponse(
        (r) => r.url().endsWith("/api/runs") && r.request().method() === "POST",
      );
      await page
        .getByRole("button", { name: "启动新运行", exact: true })
        .click();
      const r = await response;
      assert.equal(r.status(), 202, await r.text());
      id = (await r.json()).run.run_id;
      fs.writeFileSync(config.root + "/run-id", id);
    } else {
      id = fs.readFileSync(config.root + "/run-id", "utf8");
      await page.getByRole("button", { name: "运行", exact: true }).click();
      await page.getByLabel("选择运行", { exact: true }).selectOption(id);
      const resume = page.getByRole("button", {
        name: "恢复 / 继续此运行",
        exact: true,
      });
      await expect(resume).toBeEnabled();
      await resume.click();
    }
    let snapshot;
    await expect
      .poll(
        async () => {
          snapshot = await (
            await page.request.get(config.url + "/api/runs/" + id)
          ).json();
          return snapshot.busy;
        },
        { timeout: 30000 },
      )
      .toBe(false);
    assert.ok(
      !["failed", "recovery_blocked", "interrupted"].includes(
        snapshot.run.status,
      ),
      JSON.stringify(snapshot),
    );
    if (process.argv[3] === "finish") {
      assert.equal(snapshot.run.status, "succeeded");
      for (const state of Object.values(snapshot.run.dag.node_states))
        assert.equal(state.attempts.length, 1);
      assert.equal(snapshot.outputs.length, 3);
      for (const output of snapshot.outputs) {
        const r = await page.request.get(config.url + output.url);
        assert.equal(r.status(), 200);
        assert.ok((await r.body()).length);
      }
    }
    assert.deepEqual(errors, []);
    fs.writeFileSync(
      config.root + "/" + process.argv[3] + ".json",
      JSON.stringify(snapshot, null, 2),
    );
    console.log(snapshot.run.status);
  } finally {
    await browser.close();
  }
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
