import { test, expect } from "@playwright/test";

test("small nodes in a six-node graph remain hittable throughout double click", async ({
  page,
}) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  const nodes = Object.fromEntries(
    Array.from({ length: 6 }, (_, i) => [
      `n${i}`,
      {
        operator: "step",
        inputs: i ? { image: `n${i - 1}.outputs.image` } : {},
        parameters: {},
      },
    ]),
  );
  await page.route("**/api/**", (route) => {
    const path = new URL(route.request().url()).pathname;
    return route.fulfill({
      json:
        path === "/api/catalog"
          ? {
              execution_enabled: true,
              adapters: [],
              operators: {
                step: {
                  name: "step",
                  version: "1",
                  inputs: { image: { kind: "rgb_image" } },
                  outputs: { image: { kind: "rgb_image" } },
                },
              },
              templates: [
                {
                  id: "six",
                  label: "six",
                  pipeline: {
                    pipeline: "six",
                    version: "1",
                    inputs: {},
                    nodes,
                  },
                },
              ],
            }
          : path === "/api/drafts"
            ? { drafts: [] }
            : path === "/api/compile"
              ? { ok: true, execution_ready: false }
              : { runs: [] },
    });
  });
  page.on("dialog", (d) => d.accept());
  await page.goto("/");
  await page.getByRole("button", { name: "six", exact: true }).click();
  await page.getByRole("button", { name: "继续替换", exact: true }).click();
  await page
    .getByRole("button", { name: "运行记录与诊断", exact: true })
    .click();
  await page.getByRole("button", { name: "查看全图", exact: true }).click();
  const node = page.locator('[data-id="n2"]');
  const canvasBefore = await page.locator(".canvas > .react-flow").boundingBox();
  await node.locator(".blueprint-node-header").dblclick();
  await expect(
    page.getByRole("button", { name: "聚焦所选节点", exact: true }),
  ).toBeEnabled();
  await expect
    .poll(async () => (await node.boundingBox())!.width)
    .toBeGreaterThanOrEqual(250);
  await expect(node.locator("article")).toHaveClass(/blueprint-node-selected/);
  expect(await page.locator(".canvas > .react-flow").boundingBox()).toEqual(canvasBefore);
});
