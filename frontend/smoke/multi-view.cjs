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
    const errors = [];
    page.on("pageerror", (e) => errors.push(String(e)));
    page.on("dialog", (d) => d.accept());
    await page.goto(config.url);
    await page
      .getByRole("button", { name: "dag-multi-view-asset", exact: false })
      .click();
    await page.getByRole("button", { name: "运行", exact: true }).click();
    let observations = config.observations;
    if (config.images) {
      assert.ok(Array.isArray(config.images) && config.images.length >= 2);
      assert.ok(
        !config.observations,
        "Choose images or an existing observation, not both",
      );
      const uploaded = page.waitForResponse(
        (r) =>
          r.url() === config.url + "/api/inputs/observations" &&
          r.request().method() === "POST",
      );
      await page.getByLabel("上传多视图照片").setInputFiles(config.images);
      const bundle = await uploaded;
      assert.equal(bundle.ok(), true);
      const value = await bundle.json();
      observations = value.observations_ref.artifact_id;
      await expect(page.getByLabel("观测包 Artifact ID")).toHaveValue(
        observations,
      );
      fs.writeFileSync(
        config.root + "/uploaded.json",
        JSON.stringify(
          {
            images: config.images,
            request: bundle.request().postDataJSON(),
            result: value,
          },
          null,
          2,
        ),
      );
    } else {
      assert.ok(observations);
      await page.getByLabel("观测包 Artifact ID").fill(observations);
    }
    const response = page.waitForResponse(
      (r) =>
        r.url() === config.url + "/api/runs" && r.request().method() === "POST",
    );
    await page
      .getByRole("button", { name: "启动新运行 · 检查执行范围", exact: true })
      .click();
    const start = await response;
    assert.equal(start.status(), 202);
    const created = await start.json();
    const id = created.run.run_id;
    fs.writeFileSync(
      config.root + "/created.json",
      JSON.stringify(created, null, 2),
    );
    console.log("created", id);
    let completed;
    await expect
      .poll(
        async () => {
          completed = await (
            await page.request.get(config.url + "/api/runs/" + id)
          ).json();
          if (
            ["failed", "recovery_blocked", "interrupted"].includes(
              completed.run.status,
            )
          )
            throw Error(JSON.stringify(completed));
          return completed.run.status;
        },
        { timeout: 600000, intervals: [2000] },
      )
      .toBe("succeeded");
    assert.deepEqual(completed.run.dag.named_actual_inputs, {
      observations: { artifact_id: observations },
    });
    for (const state of Object.values(completed.run.dag.node_states))
      assert.equal(state.attempts.length, 1);
    for (const output of completed.outputs) {
      const data = await page.request.get(config.url + output.url);
      assert.equal(data.status(), 200);
      assert.ok((await data.body()).length > 0);
    }
    await page
      .getByRole("button", { name: "查看固定运行图", exact: true })
      .click();
    await expect(
      page
        .getByRole("dialog", { name: "固定运行图" })
        .locator('.react-flow__node[data-id="release"]'),
    ).toContainText("succeeded");
    await page.screenshot({ path: config.root + "/completed.png" });
    assert.deepEqual(errors, []);
    fs.writeFileSync(
      config.root + "/completed.json",
      JSON.stringify({ completed, errors }, null, 2),
    );
    console.log(
      id,
      "succeeded; outputs verified; browser errors",
      errors.length,
    );
  } finally {
    await browser.close();
  }
})().catch((e) => {
  console.error(e);
  process.exitCode = 1;
});
