// Real CPU editor: URL RGB_IMAGE FRESH_EVIDENCE_DIRECTORY
const fs = require("node:fs");
const assert = require("node:assert/strict");
const { chromium, expect } = require("@playwright/test");
(async () => {
  const [url, input, directory] = process.argv.slice(2);
  const evidence = `${directory}/reuse-single-browser.json`;
  assert.ok(!fs.existsSync(evidence));
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage({
      viewport: { width: 1600, height: 1000 },
    });
    const errors = [];
    page.on("pageerror", (e) => errors.push(String(e)));
    page.on("dialog", (d) => d.accept());
    const read = async (id) =>
      (await page.request.get(`${url}/api/runs/${id}`)).json();
    const start = async () => {
      const response = page.waitForResponse(
        (r) => r.url().endsWith("/api/runs") && r.request().method() === "POST",
      );
      await page
        .getByRole("button", { name: "启动新运行 · 检查执行范围", exact: true })
        .click();
      await page
        .getByRole("button", { name: "确认执行上述范围", exact: true })
        .click();
      const result = await response;
      assert.equal(result.status(), 202);
      const created = await result.json();
      await expect
        .poll(async () => (await read(created.run.run_id)).run.status)
        .toBe("succeeded");
      return read(created.run.run_id);
    };
    await page.goto(url);
    await page
      .getByRole("button", { name: "cpu-image-editor", exact: false })
      .click();
    await page.getByRole("button", { name: "运行", exact: true }).click();
    await page.getByLabel("图片来源", { exact: true }).selectOption("upload");
    await page.getByLabel("上传运行图片").setInputFiles(input);
    const original = await start();
    const sourceId = original.run.run_id;
    const reference =
      original.run.dag.node_states.encode.attempts[0].outputs.image;
    await page.getByLabel("选择运行").selectOption(sourceId);
    await expect(
      page.getByRole("button", {
        name: "用作输入 image · encode · image",
        exact: true,
      }),
    ).toBeEnabled();
    const lookup = page.waitForResponse((r) =>
      r.url().endsWith(`/api/runs/${sourceId}/references/encode/image`),
    );
    await page
      .getByRole("button", {
        name: "用作输入 image · encode · image",
        exact: true,
      })
      .click();
    assert.equal((await lookup).status(), 200);
    await expect(page.getByLabel("图片来源", { exact: true })).toHaveValue(
      "reference",
    );
    await expect(
      page.getByText(reference.artifact_id, { exact: true }),
    ).toBeVisible();
    const second = await start();
    assert.notEqual(second.run.run_id, sourceId);
    assert.deepEqual(second.run.dag.named_actual_inputs.image, reference);
    assert.deepEqual(
      second.run.dag.node_states.encode.attempts[0].resolved_inputs.image,
      reference,
    );
    assert.equal(second.run.dag.node_states.encode.attempts.length, 1);
    assert.deepEqual((await read(sourceId)).run, original.run);
    assert.deepEqual(errors, []);
    fs.writeFileSync(
      evidence,
      JSON.stringify({ original, reference, second, errors }, null, 2),
      { flag: "wx" },
    );
    await page.screenshot({ path: `${directory}/reuse-single-browser.png` });
    console.log(
      sourceId,
      "->",
      second.run.run_id,
      "single-input historical image reuse verified",
    );
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
