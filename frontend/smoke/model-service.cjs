// Real HTTP CPU-only smoke. Start the example wrapper and node-editor first.
// EDITOR_URL, MODEL_URL, SMOKE_ROOT (containing image.png) override local defaults.
const path = require("path");
const root = process.env.SMOKE_ROOT;
if (!root) throw Error("SMOKE_ROOT required; provide RGBA image.png");
const editor = process.env.EDITOR_URL || "http://127.0.0.1:18867";
const service = process.env.MODEL_URL || "http://127.0.0.1:18880";
const { chromium, expect } = require("@playwright/test");
const fs = require("fs");
(async () => {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({
    viewport: { width: 1600, height: 1000 },
  });
  page.on("dialog", (d) => d.accept());
  await page.goto(editor + "/");
  await page
    .getByRole("button", { name: "＋ 添加模型服务", exact: true })
    .click();
  await page.getByLabel("模型服务地址").fill(service);
  await page.getByRole("button", { name: "检测服务", exact: true }).click();
  await expect(
    page.getByRole("region", { name: "检测到的模型" }),
  ).toContainText("CPU 示例盒子");
  await page.getByRole("button", { name: "确认添加模型", exact: true }).click();
  const model = page.getByRole("button", { name: /CPU 示例盒子.*点击或拖入/ });
  await expect(model.last()).toBeVisible();
  await model.last().click();
  await expect(
    page.locator(".react-flow__node").filter({ hasText: "shape_generation@1" }),
  ).toHaveCount(1);
  const catalog = await page.evaluate(() =>
    fetch("/api/catalog").then((r) => r.json()),
  );
  const backend = catalog.model_services.at(-1).backend;
  fs.writeFileSync(path.join(root, "catalog.json"), JSON.stringify(catalog));
  // Import the existing release chain as reusable wiring; use the model just registered in the UI.
  let yaml = fs
    .readFileSync(
      path.join(__dirname, "../../pipelines/remote_shape_asset_v1.yaml"),
      "utf8",
    )
    .replace("adapter: remote_shape@1", `backend: ${backend}`);
  await page
    .locator('input[type=file][accept=".yaml,.yml"]')
    .setInputFiles({
      name: "release.yaml",
      mimeType: "application/yaml",
      buffer: Buffer.from(yaml),
    });
  await page.getByRole("button", { name: "运行", exact: true }).click();
  await page
    .getByLabel("上传运行图片", { exact: true })
    .setInputFiles(path.join(root, "image.png"));
  await page.waitForTimeout(1500);

  const response = page.waitForResponse(
    (r) => r.url().endsWith("/api/runs") && r.request().method() === "POST",
  );
  await page
    .getByRole("button", { name: "启动新运行 · 检查执行范围", exact: true })
    .click();
  const data = await (await response).json();
  if (!data.run) throw Error(JSON.stringify(data));
  const runId = data.run.run_id;
  let state;
  for (let i = 0; i < 90; i++) {
    state = await page.evaluate(
      (id) => fetch("/api/runs/" + id).then((r) => r.json()),
      runId,
    );
    if (!state.busy) break;
    await page.waitForTimeout(500);
  }
  if (state.run.status !== "succeeded") throw Error(JSON.stringify(state));
  fs.writeFileSync(path.join(root, "completed.json"), JSON.stringify(state));
  const glb = state.outputs.find((o) => o.kind === "gltf_asset");
  if (!glb) throw Error("missing GLB");
  const responseGlb = await page.request.get(editor + glb.url);
  fs.writeFileSync(path.join(root, "visual.glb"), await responseGlb.body());
  await page.screenshot({
    path: path.join(root, "completed.png"),
    fullPage: true,
  });
  await page.reload();
  await expect(
    page.getByRole("button", { name: /CPU 示例盒子.*点击或拖入/ }).last(),
  ).toBeVisible();
  console.log(
    JSON.stringify({
      runId,
      status: state.run.status,
      backend,
      outputs: state.outputs.length,
    }),
  );
  await browser.close();
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
