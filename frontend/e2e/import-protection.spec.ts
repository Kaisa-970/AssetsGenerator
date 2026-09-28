import { test, expect } from "@playwright/test";

test("valid YAML import waits for confirmation and cancel preserves the canvas", async ({
  page,
}) => {
  await page.route("**/api/**", (route) =>
    route.fulfill({
      json:
        new URL(route.request().url()).pathname === "/api/catalog"
          ? { operators: {}, adapters: [], templates: [] }
          : { drafts: [] },
    }),
  );
  await page.goto("/");
  await page.getByLabel("管线名称").fill("keep_my_graph");
  const file = {
    name: "replacement.yaml",
    mimeType: "application/yaml",
    buffer: Buffer.from(
      "pipeline: replacement\nversion: 1\ninputs: {}\nnodes: {}\n",
    ),
  };
  const input = page.locator('input[type="file"][accept=".yaml,.yml"]');
  await input.setInputFiles(file);
  const confirmation = page.getByRole("alertdialog", { name: "确认替换画布" });
  await expect(confirmation).toContainText("replacement.yaml");
  await expect(confirmation).toContainText("并清空当前实际输入");
  await expect(page.getByLabel("管线名称")).toHaveValue("keep_my_graph");
  await confirmation.getByRole("button", { name: "取消", exact: true }).click();
  await expect(page.locator(".react-flow__node")).toHaveCount(1);
  await expect(page.getByLabel("管线名称")).toHaveValue("keep_my_graph");
  await input.setInputFiles(file);
  await confirmation
    .getByRole("button", { name: "继续替换", exact: true })
    .click();
  await expect(page.getByLabel("管线名称")).toHaveValue("replacement");
  await expect(page.locator(".react-flow__node")).toHaveCount(0);
});
