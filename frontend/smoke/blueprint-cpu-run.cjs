// Real Core/Pillow browser acceptance: URL RGB_IMAGE FRESH_EVIDENCE_DIRECTORY.
// No mocked routes; graph construction, input and parameters use the visible UI.
const fs = require("node:fs");
const assert = require("node:assert/strict");
const { chromium, expect } = require("@playwright/test");
(async () => {
  const [url, input, directory] = process.argv.slice(2);
  assert.ok(
    url && input && directory,
    "Supply URL, RGB image and evidence directory",
  );
  fs.mkdirSync(directory, { recursive: false });
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({
    viewport: { width: 1600, height: 1000 },
  });
  const errors = [],
    starts = [],
    runs = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  page.on("dialog", (d) => d.accept());
  page.on("request", (r) => {
    if (new URL(r.url()).pathname === "/api/runs" && r.method() === "POST")
      starts.push(r.postDataJSON());
  });
  const read = async (id) => {
    const response = await page.request.get(`${url}/api/runs/${id}`);
    assert.equal(response.status(), 200);
    return response.json();
  };
  try {
    await page.goto(url);
    await page.getByRole("button", { name: "新建空白", exact: true }).click();
    const replace = page.getByRole("button", { name: "继续替换", exact: true });
    if (await replace.count()) await replace.click();
    await expect(page.locator(".react-flow__node")).toHaveCount(0);
    await page.getByLabel("添加输入节点").selectOption("rgb");
    for (const op of ["encode_png", "resize_image"]) {
      await page
        .locator(".catalog-item")
        .filter({ has: page.getByText(op, { exact: true }) })
        .click();
    }
    await page.getByRole("button", { name: "查看全图", exact: true }).click();
    await page.waitForTimeout(400); // React Flow fit animation must settle before dragging.
    for (const [from, to] of [
      ["input:image", "encode_png"],
      ["encode_png", "resize_image"],
    ]) {
      await page
        .locator(`[data-id="${from}"] .react-flow__handle.source`)
        .dragTo(page.locator(`[data-id="${to}"] .react-flow__handle.target`));
    }
    await expect(page.locator(".react-flow__edge")).toHaveCount(2);
    await page.getByLabel("上传运行图片", { exact: true }).setInputFiles(input);
    await page
      .locator('[data-id="resize_image"] .blueprint-node-header')
      .dblclick();
    for (const size of [
      [64, 32],
      [96, 48],
      [128, 64],
    ]) {
      for (const [key, value] of [
        ["width", size[0]],
        ["height", size[1]],
      ]) {
        const field = page.getByLabel(`节点参数 ${key}`, { exact: true });
        await field.fill(String(value));
        await field.press("Enter");
      }
      if (runs.length) {
        await expect(page.getByLabel("预览配置状态")).toContainText("结果过期");
        assert.deepEqual(
          (await read(runs[0].run.run_id)).run.dag.node_states,
          runs[0].run.dag.node_states,
        );
      }
      const reuse = runs.length === 1;
      if (runs.length === 2)
        await page
          .getByLabel("复用所选运行的有效节点结果（后端核对身份与证据）", {
            exact: true,
          })
          .uncheck();
      const response = page
        .waitForResponse(
          (r) =>
            new URL(r.url()).pathname === "/api/runs" &&
            r.request().method() === "POST",
        )
        .then(
          (value) => ({ value }),
          (error) => ({ error }),
        );
      const startedAt = Date.now();
      await page
        .getByRole("button", {
          name: reuse
            ? "重新执行受影响节点 · 检查执行范围"
            : "启动新运行 · 检查执行范围",
          exact: true,
        })
        .click();
      if (reuse)
        await page
          .getByRole("button", { name: "确认执行上述范围", exact: true })
          .click();
      const received = await response;
      if (received.error) throw received.error;
      const created = received.value;
      assert.equal(created.status(), 202, await created.text());
      const id = (await created.json()).run.run_id;
      await expect
        .poll(async () => (await read(id)).run.status, { timeout: 30000 })
        .toBe("succeeded");
      const completed = await read(id);
      runs.push(completed);
      const preview = page.getByRole("region", {
        name: "选中节点预览",
        exact: true,
      });
      const img = preview.getByRole("img", {
        name: "节点 resize_image 的 image 输出",
        exact: true,
      });
      await expect(img).toBeVisible();
      await expect
        .poll(() => img.evaluate((i) => [i.naturalWidth, i.naturalHeight]))
        .toEqual(size);
      const states = completed.run.dag.node_states;
      assert.equal(states.resize_image.attempts.length, 1);
      assert.equal(states.encode_png.attempts.length, 1);
      assert.equal(Boolean(states.encode_png.attempts[0].reused_from), reuse);
      assert.equal(Boolean(states.resize_image.attempts[0].reused_from), false);
      assert.deepEqual(
        states.resize_image.attempts[0].resolved_inputs.image,
        runs[0].run.dag.node_states.encode_png.attempts[0].outputs.image,
      );
      fs.writeFileSync(
        `${directory}/${id}.json`,
        JSON.stringify(
          { completed, size, previewMs: Date.now() - startedAt },
          null,
          2,
        ),
      );
      await img.scrollIntoViewIfNeeded();
      await page.screenshot({ path: `${directory}/${id}.png` });
    }
    assert.equal(
      starts.length,
      3,
      "Each ordinary start must create exactly one run",
    );
    assert.notEqual(runs[0].run.run_id, runs[1].run.run_id);
    assert.deepEqual(
      (await read(runs[0].run.run_id)).run.dag.node_states,
      runs[0].run.dag.node_states,
    );
    // Save/load uses the same visible workspace controls and real draft API.
    await page
      .getByRole("button", { name: "保存 / 加载", exact: true })
      .click();
    const draftName = `cpu-roundtrip-${Date.now()}`;
    await page.getByLabel("保存名称", { exact: true }).fill(draftName);
    await page.getByRole("button", { name: "保存草稿", exact: true }).click();
    await expect(page.locator("footer[role=status]")).toContainText(
      "实际输入未保存",
    );
    const savedResponse = await page.request.get(
      `${url}/api/drafts/${draftName}`,
    );
    assert.equal(savedResponse.status(), 200);
    const saved = await savedResponse.json();
    assert.equal(saved.pipeline.nodes.resize_image.parameters.width, 128);
    assert.equal(saved.pipeline.nodes.resize_image.parameters.height, 64);
    assert.equal(
      saved.pipeline.nodes.resize_image.inputs.image,
      "encode_png.outputs.image",
    );
    assert.deepEqual(Object.keys(saved).sort(), ["layout", "pipeline"]);
    await page
      .getByLabel("定位画布节点", { exact: true })
      .selectOption("resize_image");
    await page.getByLabel("节点参数 width", { exact: true }).fill("256");
    await page.getByLabel("节点参数 width", { exact: true }).press("Enter");
    await page
      .getByRole("button", { name: "保存 / 加载", exact: true })
      .click();
    await page.getByLabel("加载草稿").selectOption(draftName);
    await expect(
      page.getByRole("alertdialog", { name: "确认替换画布" }),
    ).toContainText("清空当前实际输入");
    await page.getByRole("button", { name: "继续替换", exact: true }).click();
    await expect(
      page.getByLabel("缺少运行输入", { exact: true }),
    ).toContainText("image");
    await page
      .getByLabel("定位画布节点", { exact: true })
      .selectOption("resize_image");
    await expect(
      page.getByLabel("节点参数 width", { exact: true }),
    ).toHaveValue("128");
    await expect(
      page.getByLabel("节点参数 height", { exact: true }),
    ).toHaveValue("64");
    // Re-save through the public UI to verify world-space layout too.
    await page
      .getByRole("button", { name: "保存 / 加载", exact: true })
      .click();
    const roundtripName = `${draftName}-reloaded`;
    await page.getByLabel("保存名称", { exact: true }).fill(roundtripName);
    await page.getByRole("button", { name: "保存草稿", exact: true }).click();
    await expect(page.locator("footer[role=status]")).toContainText(
      "实际输入未保存",
    );
    const roundtripResponse = await page.request.get(
      `${url}/api/drafts/${roundtripName}`,
    );
    assert.equal(roundtripResponse.status(), 200);
    const roundtrip = await roundtripResponse.json();
    assert.deepEqual(
      roundtrip.pipeline,
      saved.pipeline,
      "Loaded graph and parameters must round trip exactly",
    );
    assert.deepEqual(
      roundtrip.layout,
      saved.layout,
      "Loaded node positions must round trip exactly",
    );
    assert.equal(starts.length, 3, "Loading must not launch inference");
    fs.writeFileSync(
      `${directory}/saved-draft.json`,
      JSON.stringify(saved, null, 2),
    );
    assert.deepEqual(errors, []);
    fs.writeFileSync(
      `${directory}/acceptance.json`,
      JSON.stringify({ starts, runs, errors }, null, 2),
    );
    console.log(
      "PASS: blank graph, wiring, node parameters, one-click initial run, confirmed parameter iteration, decoded previews, immutable previous run",
      runs.map((r) => r.run.run_id),
    );
  } catch (error) {
    await page.screenshot({ path: `${directory}/failure.png` });
    fs.writeFileSync(
      `${directory}/failure.txt`,
      await page.locator("body").innerText(),
    );
    throw error;
  } finally {
    await browser.close();
  }
})().catch((e) => {
  console.error(e);
  process.exitCode = 1;
});
