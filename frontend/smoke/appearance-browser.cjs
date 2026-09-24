// CPU fixture meshes, real editor HTTP and WebGL; never submits model inference.
const fs = require("node:fs");
const assert = require("node:assert/strict");
const { chromium, expect } = require("@playwright/test");
(async () => {
  const config = JSON.parse(fs.readFileSync(process.argv[2]));
  assert.equal(config.cpu_fixture, true);
  const browser = await chromium.launch({
    headless: true,
    args: ["--use-angle=swiftshader"],
  });
  const evidence = { cpu_fixture: true, checks: [], errors: [], requests: [] };
  try {
    const page = await browser.newPage({
      viewport: { width: 1700, height: 1100 },
    });
    page.on("pageerror", (e) => evidence.errors.push(String(e)));
    page.on("response", (r) => {
      if (r.url().includes("/api/"))
        evidence.requests.push({ url: r.url(), status: r.status() });
    });
    await page.goto(config.url);
    await page.getByRole("button", { name: "运行", exact: true }).click();
    for (const [mode, id] of Object.entries(config.runs)) {
      await page.getByLabel("选择运行", { exact: true }).selectOption(id);
      for (const [node, port] of [
        ["shape", "mesh"],
        ["canonical", "mesh"],
        ["publish", "glb"],
      ]) {
        const response = await page.request.get(
          `${config.url}/api/runs/${id}/appearance/${node}/${port}`,
        );
        assert.equal(response.status(), 200);
        const facts = await response.json();
        assert.equal(facts.textured_primitives, mode === "texture" ? 1 : 0);
        assert.equal(
          facts.vertex_colored_primitives,
          mode === "vertex" ? 1 : 0,
        );
        if (node === "shape" && mode === "none")
          assert.equal(facts.postprocess_mode, "geometry_fallback_no_texture");
        const section = page.getByRole("region", {
          name: `外观事实 · ${node} · ${port}`,
          exact: true,
        });
        await expect(section).toContainText("仅检查 GLB");
        if (mode === "none" && node === "shape")
          await expect(section).toContainText("纹理后处理降级为纯几何输出");
        await page
          .getByRole("button", {
            name: `预览模型 · ${node} · ${port}`,
            exact: true,
          })
          .click();
        const preview = page.getByRole("dialog", {
          name: "模型预览",
          exact: true,
        });
        await expect(preview).toContainText("模型已加载", { timeout: 30000 });
        await page.screenshot({ path: `${config.root}/${mode}-${node}.png` });
        await preview
          .getByRole("button", { name: "关闭模型预览", exact: true })
          .click();
        evidence.checks.push({ mode, node, port, facts, webgl: "loaded" });
      }
      for (const [side, node] of [
        ["A", "shape"],
        ["B", "canonical"],
      ]) {
        await page
          .getByRole("button", {
            name: `加入比较 ${side} · ${node} · mesh`,
            exact: true,
          })
          .click();
      }
      await page
        .getByRole("button", { name: "比较两次结果", exact: true })
        .click();
      for (const side of ["A", "B"]) {
        await page
          .getByRole("button", { name: `预览模型 · ${side}`, exact: true })
          .click();
        const preview = page.getByRole("dialog", {
          name: "模型预览",
          exact: true,
        });
        await expect(preview).toContainText("模型已加载", { timeout: 30000 });
        await preview
          .getByRole("button", { name: "关闭模型预览", exact: true })
          .click();
      }
      await page.screenshot({ path: `${config.root}/${mode}-comparison.png` });
      await page.getByRole("button", { name: "关闭比较", exact: true }).click();
      evidence.checks.push({ mode, comparison: "shape/canonical A/B loaded" });
    }
    assert.deepEqual(evidence.errors, []);
    assert.ok(
      evidence.requests.some((r) =>
        r.url.includes("/snapshot-output/shape/mesh"),
      ),
    );
    assert.ok(
      evidence.requests.some((r) =>
        r.url.includes("/snapshot-output/canonical/mesh"),
      ),
    );
    assert.ok(
      evidence.requests.every((r) => r.status < 400),
      JSON.stringify(evidence.requests.filter((r) => r.status >= 400)),
    );
    evidence.ok = true;
  } catch (e) {
    evidence.failure = String(e);
    throw e;
  } finally {
    fs.writeFileSync(
      `${config.root}/browser-result.json`,
      JSON.stringify(evidence, null, 2),
    );
    await browser.close();
  }
  console.log(
    JSON.stringify({
      ok: true,
      cpu_fixture: true,
      checks: evidence.checks.length,
    }),
  );
})().catch((e) => {
  console.error(e);
  process.exitCode = 1;
});
