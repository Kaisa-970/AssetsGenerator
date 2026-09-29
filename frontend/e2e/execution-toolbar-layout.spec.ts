import { expect, test } from "@playwright/test";

test("normal execution toolbar stays compact and preflight diagnostics remain reachable", async ({
  page,
}) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    let body: unknown = {};
    if (path === "/api/catalog")
      body = {
        operators: {},
        adapters: [],
        templates: [],
        execution_enabled: true,
      };
    else if (path === "/api/drafts") body = { drafts: [] };
    else if (path === "/api/compile")
      body = { ok: true, execution_ready: true };
    else if (path === "/api/runs")
      body = { runs: [{ run_id: "history", status: "succeeded" }] };
    else if (path === "/api/runs/history")
      body = {
        run: {
          run_id: "history",
          status: "succeeded",
          dag: { revision: 1, node_states: {} },
        },
        snapshot_ref: { artifact_id: "snapshot" },
        outputs: [],
      };
    else if (path === "/api/inputs/image")
      body = { image_ref: { artifact_id: "image" } };
    else if (path === "/api/prepare-inputs")
      body = { input_refs: { image: { artifact_id: "image" } } };
    else if (path === "/api/preflight")
      body = {
        digest: "checked",
        execution_ready: true,
        nodes: Object.fromEntries(
          Array.from({ length: 60 }, (_, index) => [
            `encode_${index}`,
            {
              status: "execute",
              reason: "no_verified_reuse",
              detail: "No previous result",
            },
          ]),
        ),
        source_evidence_policy: "whole_snapshot_closure",
      };
    await route.fulfill({ json: body });
  });
  await page.goto("/");
  const toolbar = page.getByLabel("运行工具栏", { exact: true });
  await page.getByLabel("选择运行", { exact: true }).selectOption("history");
  await expect(
    page.getByLabel("复用所选运行的有效节点结果（后端核对身份与证据）"),
  ).toBeVisible();
  await expect
    .poll(async () => (await toolbar.boundingBox())!.height)
    .toBeLessThanOrEqual(150);
  await page.getByLabel("上传运行图片", { exact: true }).setInputFiles({
    name: "image.png",
    mimeType: "image/png",
    buffer: Buffer.from("fixture"),
  });
  await page
    .getByRole("button", { name: "启动新运行 · 检查执行范围", exact: true })
    .click();
  const confirm = page.getByRole("button", {
    name: "确认执行上述范围",
    exact: true,
  });
  await expect(confirm).toBeVisible();
  await expect(toolbar).toContainText("启动时再次核验");
  const preflight = page.getByLabel("节点执行范围摘要", { exact: true });
  expect(
    await preflight.evaluate((el) => el.scrollHeight > el.clientHeight),
  ).toBe(true);
  expect((await preflight.boundingBox())!.height).toBeLessThanOrEqual(280);
  await preflight.evaluate((el) => {
    el.scrollTop = el.scrollHeight;
  });
  await expect(confirm).toBeInViewport();
  await expect(
    toolbar.getByText(
      "启动时再次核验；执行或复用条件变化会停止创建，要求重新检查。",
      { exact: true },
    ),
  ).toBeInViewport();
  await page.setViewportSize({ width: 760, height: 900 });
  await expect(confirm).toBeVisible();
  expect(
    await toolbar.evaluate((el) => el.scrollWidth <= el.clientWidth + 1),
  ).toBe(true);
  await page.route("**/api/preflight", (route) =>
    route.fulfill({
      status: 400,
      json: { error: "输入证据无法核实，请重新选择来源" },
    }),
  );
  await page
    .getByRole("button", { name: "启动新运行 · 检查执行范围", exact: true })
    .click();
  const error = toolbar.getByRole("alert");
  await expect(error).toContainText("输入证据无法核实");
  await expect(error).toBeInViewport();
});

test("many historical problems do not consume the canvas", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    let body: unknown = {};
    if (path === "/api/catalog")
      body = {
        operators: {},
        adapters: [],
        templates: [],
        execution_enabled: true,
      };
    else if (path === "/api/drafts") body = { drafts: [] };
    else if (path === "/api/compile")
      body = { ok: true, execution_ready: true };
    else if (path === "/api/runs")
      body = { runs: [{ run_id: "many", status: "failed" }] };
    else if (path === "/api/runs/many")
      body = {
        run: {
          run_id: "many",
          status: "failed",
          dag: {
            revision: 1,
            node_states: Object.fromEntries(
              Array.from({ length: 60 }, (_, i) => [
                `node_${i}`,
                { status: "failed" },
              ]),
            ),
          },
        },
        outputs: [],
      };
    await route.fulfill({ json: body });
  });
  await page.goto("/");
  await page.getByLabel("选择运行", { exact: true }).selectOption("many");
  const summary = page.getByText("运行问题与人工待办 · 60", { exact: true });
  await expect(summary).toBeVisible();
  const toolbar = page.getByLabel("运行工具栏", { exact: true });
  expect((await toolbar.boundingBox())!.height).toBeLessThan(200);
  await summary.click();
  const list = page.getByLabel("运行问题与人工待办列表", { exact: true });
  expect((await list.boundingBox())!.height).toBeLessThanOrEqual(120);
  expect(await list.evaluate((el) => el.scrollHeight > el.clientHeight)).toBe(
    true,
  );
  await list.evaluate((el) => {
    el.scrollTop = el.scrollHeight;
  });
  await expect(
    page.getByRole("button", { name: "查看运行问题 · node_59", exact: true }),
  ).toBeInViewport();
  expect((await toolbar.boundingBox())!.height).toBeLessThan(340);
});
