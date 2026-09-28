import { test, expect } from "@playwright/test";

test("node palette adds an input and an operator without opening the inspector", async ({
  page,
}) => {
  const pipeline = {
    pipeline: "palette",
    version: "1",
    inputs: {},
    nodes: {},
  };
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    await route.fulfill({
      json:
        path === "/api/catalog"
          ? {
              execution_enabled: false,
              operators: {
                "text_segmentation@2": {
                  name: "text_segmentation",
                  version: "2",
                  inputs: {
                    image: { kind: "rgb_image", carriers: ["artifact_ref"] },
                    text: { kind: "text", carriers: ["structured"] },
                  },
                  outputs: {
                    mask: { kind: "binary_mask", carriers: ["artifact_ref"] },
                  },
                },
              },
              adapters: [],
              templates: [{ id: "palette", label: "空白", pipeline }],
            }
          : path === "/api/drafts"
            ? { drafts: [] }
            : path === "/api/compile"
              ? { ok: true, execution_ready: false }
              : {},
    });
  });
  await page.goto("/");
  await page.getByRole("button", { name: "＋ 添加节点" }).click();
  const palette = page.getByRole("dialog", { name: "添加节点" });
  await expect(palette).toBeVisible();
  await expect(page.getByLabel("搜索节点")).toBeFocused();
  // Tabbing past every control remains in the native modal.
  for (let index = 0; index < 10; index++) await page.keyboard.press("Tab");
  expect(await palette.evaluate((element) => element.contains(document.activeElement))).toBe(true);
  await page.keyboard.press("Escape");
  await expect(page.getByRole("button", { name: "＋ 添加节点" })).toBeFocused();
  await page.getByRole("button", { name: "＋ 添加节点" }).click();
  await page.getByRole("button", { name: /文字输入/ }).click();
  await expect(page.locator('[data-id="input:text"]')).toBeVisible();

  await page.getByRole("button", { name: "＋ 添加节点" }).click();
  await page.getByLabel("搜索节点").fill("文字分割");
  await page
    .getByRole("dialog", { name: "添加节点" })
    .getByRole("button", { name: /文字分割 text_segmentation/ })
    .click();
  await expect(page.locator('[data-id="text_segmentation"]')).toBeVisible();
});

test("right click on the canvas opens the node palette", async ({ page }) => {
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    await route.fulfill({
      json:
        path === "/api/catalog"
          ? { execution_enabled: false, operators: {}, adapters: [], templates: [] }
          : path === "/api/drafts"
            ? { drafts: [] }
            : path === "/api/compile"
              ? { ok: true, execution_ready: false }
              : {},
    });
  });
  await page.goto("/");
  await page.locator(".react-flow__pane").click({ button: "right", position: { x: 500, y: 300 } });
  await expect(page.getByRole("dialog", { name: "添加节点" })).toBeVisible();
});

test("node palette search filters input, operator, and service groups together", async ({
  page,
}) => {
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    await route.fulfill({
      json:
        path === "/api/catalog"
          ? {
              execution_enabled: false,
              operators: {
                "text_segmentation@2": {
                  name: "text_segmentation",
                  version: "2",
                  inputs: {},
                  outputs: {},
                },
              },
              adapters: [],
              templates: [],
            }
          : path === "/api/drafts"
            ? { drafts: [] }
            : path === "/api/compile"
              ? { ok: true, execution_ready: false }
              : {},
    });
  });
  await page.goto("/");
  await page.getByRole("button", { name: "＋ 添加节点" }).click();
  const palette = page.getByRole("dialog", { name: "添加节点" });
  await page.getByLabel("搜索节点").fill("文字");
  await expect(palette.getByRole("button", { name: /文字输入/ })).toBeVisible();
  await expect(palette.getByRole("button", { name: /文字分割 text_segmentation/ })).toBeVisible();
  await expect(palette.getByRole("button", { name: /图片输入/ })).toHaveCount(0);
});

test("shift-a opens the palette and escape closes it without editing fields", async ({
  page,
}) => {
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    await route.fulfill({
      json:
        path === "/api/catalog"
          ? { execution_enabled: false, operators: {}, adapters: [], templates: [] }
          : path === "/api/drafts"
            ? { drafts: [] }
            : path === "/api/compile"
              ? { ok: true, execution_ready: false }
              : {},
    });
  });
  await page.goto("/");
  await page.locator(".react-flow__pane").click({ position: { x: 500, y: 300 } });
  await page.keyboard.press("Shift+A");
  await expect(page.getByRole("dialog", { name: "添加节点" })).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog", { name: "添加节点" })).toBeHidden();
});
