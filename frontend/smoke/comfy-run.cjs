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
    const mode = process.argv[3];
    const readonly = ["preview", "damaged"].includes(mode);
    let mutations = 0;
    page.on("request", (request) => {
      if (request.method() !== "GET") mutations++;
    });
    await page.goto(config.url);
    let id;
    if (process.argv[3] === "start") {
      await page
        .getByRole("button", { name: "comfy_image_chain_v1", exact: false })
        .click();
      await page.getByRole("button", { name: "运行", exact: true }).click();
      await page.getByLabel("图片来源", { exact: true }).selectOption("path");
      await page.getByLabel("运行图片路径").fill(config.image);
      const response = page.waitForResponse(
        (r) => r.url().endsWith("/api/runs") && r.request().method() === "POST",
      );
      await page
        .getByRole("button", { name: "启动新运行 · 检查执行范围", exact: true })
        .click();
      await page
        .getByRole("button", { name: "确认执行上述范围", exact: true })
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
      if (!readonly) {
        await expect(resume).toBeEnabled();
        await resume.click();
      }
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
    if (readonly) {
      const before = JSON.stringify(snapshot.run);
      await page
        .getByRole("button", { name: "预览图片 · second · image", exact: true })
        .click();
      if (mode === "damaged") {
        await expect(page.getByRole("alert")).toContainText("图片读取失败");
      } else {
        const img = page.getByRole("img", {
          name: "节点 second 的 image 输出",
          exact: true,
        });
        await expect(img).toBeVisible();
        assert.equal(await img.evaluate((element) => element.naturalWidth), 2);
        await page.screenshot({
          path: config.root + "/preview.png",
          fullPage: true,
        });
      }
      const after = await (
        await page.request.get(config.url + "/api/runs/" + id)
      ).json();
      assert.equal(JSON.stringify(after.run), before);
      assert.equal(mutations, 0);
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
