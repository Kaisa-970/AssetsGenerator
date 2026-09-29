import { test, expect } from "@playwright/test";

for (const size of [
  { width: 1440, height: 900 },
  { width: 1920, height: 1080 },
]) {
  test(`workspace resizing keeps canvas available at ${size.width}`, async ({
    page,
  }) => {
    await page.setViewportSize(size);
    await page.route("**/api/**", async (route) => {
      const path = new URL(route.request().url()).pathname;
      await route.fulfill({
        json:
          path === "/api/catalog"
            ? {
                operators: {},
                adapters: [],
                templates: [],
                execution_enabled: true,
              }
            : path === "/api/compile"
              ? { ok: true, execution_ready: false }
              : path === "/api/drafts"
                ? { drafts: [] }
                : { runs: [] },
      });
    });
    await page.goto("/");
    await expect(page.locator("#workspace-catalog")).toBeHidden();
    await page
      .getByRole("button", { name: "展开属性面板", exact: true })
      .click();
    const resize = page.getByRole("separator", { name: "调整属性面板宽度" });
    await resize.focus();
    await resize.press("End");
    await expect(resize).toHaveAttribute("aria-valuenow", "600");
    const canvas = (await page.locator(".canvas").boundingBox())!;
    const inspector = (await page.locator(".inspector").boundingBox())!;
    expect(canvas.width).toBeGreaterThanOrEqual(300);
    // Side panels are overlays; opening Details must not shrink the canvas.
    expect(canvas.width).toBeGreaterThanOrEqual(size.width - 2);
    expect(inspector.x + inspector.width).toBeGreaterThanOrEqual(
      size.width - 1,
    );
    expect(inspector.x + inspector.width).toBeLessThanOrEqual(size.width);
    await expect(
      page.getByRole("button", { name: "运行记录与诊断", exact: true }),
    ).toBeInViewport();
    await page
      .getByRole("button", { name: "运行记录与诊断", exact: true })
      .click();
    await expect(
      page.getByRole("separator", { name: "调整运行记录高度" }),
    ).toHaveAttribute("aria-valuenow", "220");
    const historyResize = page.getByRole("separator", {
      name: "调整运行记录高度",
    });
    await historyResize.focus();
    await historyResize.press("Home");
    await expect(historyResize).toHaveAttribute("aria-valuenow", "140");
    await historyResize.press("End");
    await expect(historyResize).toHaveAttribute("aria-valuenow", "560");
    await historyResize.dblclick();
    await expect(historyResize).toHaveAttribute("aria-valuenow", "300");
    await resize.dblclick();
    await expect(resize).toHaveAttribute("aria-valuenow", "280");
    await resize.focus();
    await resize.press("Home");
    await expect(resize).toHaveAttribute("aria-valuenow", "260");
    await resize.press("ArrowRight");
    await expect(resize).toHaveAttribute("aria-valuenow", "260");
  });
}

test("workspace controls are grouped and remain usable on a narrow screen", async ({
  page,
}) => {
  await page.setViewportSize({ width: 720, height: 900 });
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    await route.fulfill({
      json:
        path === "/api/catalog"
          ? {
              operators: {},
              adapters: [],
              templates: [],
              execution_enabled: true,
            }
          : path === "/api/compile"
            ? { ok: true, execution_ready: false }
            : path === "/api/drafts"
              ? { drafts: [] }
              : { runs: [] },
    });
  });
  await page.goto("/");
  await expect(
    page.getByText("注册表示配置可绑定，不代表模型已验收。", { exact: true }),
  ).toBeHidden();
  await page.getByRole("button", { name: "展开节点目录", exact: true }).click();
  await page.getByText("目录说明", { exact: true }).click();
  await expect(
    page.getByText("注册表示配置可绑定，不代表模型已验收。", { exact: true }),
  ).toBeVisible();
  await expect(
    page.locator(".workspace-panel-group[aria-label='画布视图']"),
  ).toBeVisible();
  await expect(
    page.locator(".workspace-panel-group[aria-label='工作区面板开关']"),
  ).toBeVisible();
  await expect(
    page.locator(".workspace-panel-group[aria-label='节点定位']"),
  ).toBeVisible();
  const locator = page.locator(".workspace-panel-group-locator");
  const box = await locator.boundingBox();
  expect(box?.width).toBeGreaterThan(200);
  await expect(
    page.getByRole("combobox", { name: "定位画布节点" }),
  ).toBeVisible();
});

test("selecting a node keeps the advanced panel closed until explicitly opened", async ({
  page,
}) => {
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    await route.fulfill({
      json:
        path === "/api/catalog"
          ? {
              operators: {
                step: {
                  name: "step",
                  version: "1",
                  inputs: {},
                  outputs: {},
                },
              },
              adapters: [],
              templates: [
                {
                  id: "single",
                  label: "single",
                  pipeline: {
                    pipeline: "single",
                    version: "1",
                    inputs: {},
                    nodes: { step: { operator: "step", inputs: {} } },
                  },
                },
              ],
              execution_enabled: true,
            }
          : path === "/api/compile"
            ? { ok: true, execution_ready: false }
            : path === "/api/drafts"
              ? { drafts: [] }
              : { runs: [] },
    });
  });
  await page.goto("/");
  await page.getByRole("button", { name: "展开节点目录", exact: true }).click();
  await page.getByRole("button", { name: "single", exact: true }).click();
  await page.getByRole("button", { name: "继续替换", exact: true }).click();
  await page.getByRole("button", { name: "收起属性面板", exact: true }).click();
  await page.locator('[data-id="step"] .blueprint-node-header').click();
  await expect(page.locator(".inspector")).toBeHidden();
  await page.getByRole("button", { name: "展开属性面板", exact: true }).click();
  await expect(page.locator(".inspector")).toBeVisible();
});

for (const width of [720, 960]) {
  test(`opening properties preserves the canvas and shows the whole panel at ${width}`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await page.route("**/api/**", route => route.fulfill({ json:
      new URL(route.request().url()).pathname === "/api/catalog"
        ? { operators: {}, adapters: [], templates: [], execution_enabled: false }
        : { drafts: [] },
    }));
    await page.goto("/");
    const canvas = page.locator(".react-flow");
    const before = await canvas.boundingBox();
    await page.getByRole("button", { name: "展开属性面板", exact: true }).click();
    await page.mouse.move(0, 0);
    const panel = page.locator("#workspace-inspector");
    await expect(panel).toBeVisible();
    await expect.poll(async () => (await panel.boundingBox())!.x + (await panel.boundingBox())!.width).toBeLessThanOrEqual(width);
    const after = await canvas.boundingBox();
    expect(after!.width).toBeCloseTo(before!.width, 0);
    expect(after!.x).toBeCloseTo(before!.x, 0);
  });
}

for (const width of [720, 960, 1440]) {
  test(`compact navigation leaves the pipeline viewport dominant at ${width}`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await page.route("**/api/**", route => route.fulfill({ json:
      new URL(route.request().url()).pathname === "/api/catalog"
        ? { operators: {}, adapters: [], templates: [], execution_enabled: false }
        : { drafts: [] },
    }));
    await page.goto("/");
    const canvas = page.locator(".canvas");
    const box = await canvas.boundingBox();
    expect(box?.height).toBeGreaterThan(650);
    await expect(page.getByRole("button", { name: "＋ 添加节点", exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "展开节点目录", exact: true })).toBeVisible();
  });
}
