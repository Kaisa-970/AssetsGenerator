// Real CPU editor (no mocked routes): URL RGB_IMAGE MASK EXTERNAL_EVIDENCE_DIRECTORY
const fs = require("node:fs");
const assert = require("node:assert/strict");
const { chromium, expect } = require("@playwright/test");
(async () => {
  const [url, input, mask, directory] = process.argv.slice(2);
  const evidence = `${directory}/mask-browser.json`;
  assert.ok(!fs.existsSync(evidence), "Use a fresh evidence directory");
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage({
      viewport: { width: 1600, height: 1000 },
    });
    const errors = [];
    page.on("pageerror", (error) => errors.push(String(error)));
    page.on("dialog", (dialog) => dialog.accept());
    await page.goto(url);
    await page
      .getByRole("button", { name: "cpu-image-mask", exact: true })
      .click();
    const compiledResponse = page.waitForResponse((r) =>
      r.url().endsWith("/api/compile"),
    );
    await page.getByRole("button", { name: "编译校验", exact: true }).click();
    const compiled = await (await compiledResponse).json();
    assert.equal(compiled.execution_ready, true);
    await page.getByRole("button", { name: "运行", exact: true }).click();
    const start = page.getByRole("button", { name: "启动新运行", exact: true });
    await expect(start).toBeDisabled();
    for (const [name, file, endpoint] of [
      ["image", input, "image"],
      ["mask", mask, "mask"],
    ]) {
      const uploaded = page.waitForResponse((r) =>
        r.url().endsWith(`/api/inputs/${endpoint}`),
      );
      await page
        .getByLabel(`上传输入 ${name}`, { exact: true })
        .setInputFiles(file);
      assert.equal((await uploaded).status(), 201);
      if (name === "image") await expect(start).toBeDisabled();
    }
    const response = page.waitForResponse(
      (r) => r.url().endsWith("/api/runs") && r.request().method() === "POST",
    );
    await start.click();
    const started = await response;
    assert.equal(started.status(), 202);
    const submitted = started.request().postDataJSON();
    assert.deepEqual(Object.keys(submitted.input_refs).sort(), [
      "image",
      "mask",
    ]);
    const created = await started.json();
    fs.writeFileSync(
      evidence,
      JSON.stringify({ created, submitted }, null, 2),
      { flag: "wx" },
    );
    const id = created.run.run_id;
    const read = async () => {
      const r = await page.request.get(`${url}/api/runs/${id}`);
      assert.ok(r.ok());
      return r.json();
    };
    await expect.poll(async () => (await read()).run.status).toBe("succeeded");
    const completed = await read();
    assert.deepEqual(
      completed.run.dag.named_actual_inputs,
      submitted.input_refs,
    );
    const attempt = completed.run.dag.node_states.composite.attempts[0];
    assert.deepEqual(attempt.resolved_inputs, submitted.input_refs);
    await page
      .getByRole("button", { name: "预览图片 · composite · rgba", exact: true })
      .click();
    const image = page.getByRole("img", {
      name: "节点 composite 的 rgba 输出",
      exact: true,
    });
    await expect(image).toBeVisible();
    const pixels = await image.evaluate((img) => {
      const canvas = document.createElement("canvas");
      canvas.width = img.naturalWidth;
      canvas.height = img.naturalHeight;
      const ctx = canvas.getContext("2d");
      ctx.drawImage(img, 0, 0);
      return {
        width: canvas.width,
        height: canvas.height,
        alpha: Array.from(
          ctx.getImageData(0, 0, canvas.width, canvas.height).data,
        ).filter((_, i) => i % 4 === 3),
      };
    });
    assert.ok(
      pixels.alpha.includes(0) && pixels.alpha.includes(255),
      "Fixture must exercise foreground and background",
    );
    assert.ok(pixels.alpha.every((v) => v === 0 || v === 255));
    const resumed = page.waitForResponse((r) =>
      r.url().endsWith(`/api/runs/${id}/resume`),
    );
    await page
      .getByRole("button", { name: "恢复 / 继续此运行", exact: true })
      .click();
    assert.equal((await resumed).status(), 202);
    await expect.poll(async () => (await read()).busy).toBe(false);
    const restored = await read();
    assert.deepEqual(
      restored.run.dag.node_states,
      completed.run.dag.node_states,
    );
    assert.equal(restored.run.dag.node_states.composite.attempts.length, 1);
    assert.deepEqual(errors, []);
    fs.writeFileSync(
      evidence,
      JSON.stringify(
        { compiled, submitted, created, completed, restored, pixels, errors },
        null,
        2,
      ),
    );
    await page.screenshot({ path: `${directory}/mask-browser.png` });
    console.log(
      id,
      "compile, two uploads, exact bindings, binary alpha preview and recovery verified",
    );
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
