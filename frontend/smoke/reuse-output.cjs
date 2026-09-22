// Real CPU editor: URL RGB_IMAGE SAME_SIZE_BINARY_MASK FRESH_EVIDENCE_DIRECTORY
const fs = require("node:fs");
const assert = require("node:assert/strict");
const { chromium, expect } = require("@playwright/test");
(async () => {
  const [url, input, mask, directory] = process.argv.slice(2);
  const evidence = `${directory}/reuse-browser.json`;
  assert.ok(!fs.existsSync(evidence));
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage({
      viewport: { width: 1600, height: 1000 },
    });
    const errors = [];
    page.on("pageerror", (e) => errors.push(String(e)));
    page.on("dialog", (d) => d.accept());
    const read = async (id) => {
      const response = await page.request.get(`${url}/api/runs/${id}`);
      assert.ok(response.ok());
      return response.json();
    };
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
      const r = await response;
      assert.equal(r.status(), 202);
      const created = await r.json();
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
    const id = original.run.run_id;
    fs.writeFileSync(evidence, JSON.stringify({ original }, null, 2), {
      flag: "wx",
    });
    const reference =
      original.run.dag.node_states.encode.attempts[0].outputs.image;
    await page
      .getByRole("button", { name: "cpu-image-mask", exact: false })
      .click();
    await page.getByRole("button", { name: "编译校验", exact: true }).click();
    await expect(page.locator("footer[role=status]")).toContainText(
      "编译通过；可在运行页创建新运行",
    );
    await page.getByRole("button", { name: "运行", exact: true }).click();
    const lookup = page.waitForResponse((r) =>
      r.url().endsWith(`/api/runs/${id}/references/encode/image`),
    );
    await page
      .getByRole("button", {
        name: "用作输入 image · encode · image",
        exact: true,
      })
      .click();
    assert.equal((await lookup).status(), 200);
    await expect(page.getByLabel("输入 image Artifact ID")).toHaveValue(
      reference.artifact_id,
    );
    assert.deepEqual((await read(id)).run, original.run);
    const listed = await (await page.request.get(`${url}/api/runs`)).json();
    assert.equal(listed.runs.length, 1, "binding must not create another run");
    await page.getByLabel("上传输入 mask").setInputFiles(mask);
    const completed = await start();
    assert.notEqual(completed.run.run_id, id);
    assert.deepEqual(completed.run.dag.named_actual_inputs.image, reference);
    assert.deepEqual(
      completed.run.dag.node_states.composite.attempts[0].resolved_inputs.image,
      reference,
    );
    assert.equal(completed.run.dag.node_states.composite.attempts.length, 1);
    assert.deepEqual((await read(id)).run, original.run);
    await page
      .getByRole("button", { name: "预览图片 · composite · rgba", exact: true })
      .click();
    await expect(
      page.getByRole("img", {
        name: "节点 composite 的 rgba 输出",
        exact: true,
      }),
    ).toBeVisible();
    assert.deepEqual(errors, []);
    fs.writeFileSync(
      evidence,
      JSON.stringify({ original, reference, completed, errors }, null, 2),
    );
    await page.screenshot({ path: `${directory}/reuse-browser.png` });
    console.log(
      id,
      "->",
      completed.run.run_id,
      "exact output reuse and unchanged source run verified",
    );
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
