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
    const resize = page.getByRole("separator", { name: "调整属性面板宽度" });
    await resize.focus();
    await resize.press("End");
    await expect(resize).toHaveAttribute("aria-valuenow", "600");
    const canvas = (await page.locator(".canvas").boundingBox())!;
    const inspector = (await page.locator(".inspector").boundingBox())!;
    expect(canvas.width).toBeGreaterThanOrEqual(300);
    expect(canvas.x + canvas.width).toBeLessThanOrEqual(inspector.x + 1);
    expect(inspector.x + inspector.width).toBeLessThanOrEqual(size.width);
    await expect(
      page.getByRole("button", { name: "运行记录与诊断", exact: true }),
    ).toBeInViewport();
    await page
      .getByRole("button", { name: "运行记录与诊断", exact: true })
      .click();
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
