import { test, expect } from "@playwright/test";

test("dragging data onto a parameter cannot bind it; declared ports still connect", async ({
  page,
}) => {
  const port = { kind: "rgb_image", carriers: ["artifact_ref"] };
  const compiled: any[] = [];
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/api/compile") compiled.push(route.request().postDataJSON());
    await route.fulfill({
      json:
        path === "/api/catalog"
          ? {
              execution_enabled: true,
              operators: {
                "resize@1": {
                  name: "resize",
                  version: "1",
                  inputs: { image: port },
                  outputs: { image: port },
                },
              },
              adapters: [
                {
                  name: "resize",
                  version: "1",
                  operators: ["resize@1"],
                  defaults: { width: 128 },
                  parameter_schema: {
                    type: "object",
                    properties: { width: { type: "integer", minimum: 1 } },
                  },
                },
              ],
              templates: [
                {
                  id: "ports",
                  label: "参数与端口",
                  pipeline: {
                    pipeline: "ports",
                    version: "1",
                    inputs: { image: port, replacement: port },
                    nodes: {
                      resize: {
                        operator: "resize@1",
                        adapter: "resize@1",
                        inputs: {},
                        parameters: { width: 128 },
                      },
                    },
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
  page.on("dialog", (dialog) => dialog.accept());
  await page.setViewportSize({ width: 1920, height: 1080 });
  await page.goto("/");
  await page.getByRole("button", { name: "参数与端口", exact: true }).click();
  await page.getByRole("button", { name: "继续替换", exact: true }).click();
  await page.getByRole("button", { name: "查看全图", exact: true }).click();
  const node = page.locator('[data-id="resize"]');
  const source = page.locator(
    '[data-id="input:image"] .react-flow__handle.source',
  );
  const parameter = node.getByLabel("节点参数 width", { exact: true });
  await expect(parameter).toBeVisible();
  await expect(node.locator(".react-flow__handle.target")).toHaveCount(1);
  await expect(
    node.locator(".blueprint-parameter-fields .react-flow__handle"),
  ).toHaveCount(0);
  await source.dragTo(parameter);
  await expect(page.locator(".react-flow__edge")).toHaveCount(0);
  await expect(parameter).toHaveValue("128");
  const position = await node.getAttribute("style");
  await parameter.fill("256");
  await parameter.press("Tab");
  await expect
    .poll(() => compiled.at(-1)?.pipeline?.nodes.resize.parameters.width)
    .toBe(256);
  expect(await node.getAttribute("style")).toBe(position);
  await source.dragTo(node.locator(".react-flow__handle.target"));
  await expect(page.locator(".react-flow__edge")).toHaveCount(1);
  await expect
    .poll(() => compiled.at(-1)?.pipeline?.nodes.resize.inputs.image)
    .toBe("pipeline.inputs.image");
  expect(compiled.at(-1).pipeline.nodes.resize.parameters).toEqual({
    width: 256,
  });
  await page
    .locator('[data-id="input:replacement"] .react-flow__handle.source')
    .dragTo(node.locator(".react-flow__handle.target"));
  await expect(
    page.getByLabel("resize 输入 image 来源", { exact: true }),
  ).toHaveText("← 输入 replacement");
  await expect(page.locator("footer")).toContainText("原为 输入 image");
  await expect(page.locator(".react-flow__edge")).toHaveCount(1);
  await expect
    .poll(() => compiled.at(-1)?.pipeline?.nodes.resize.inputs.image)
    .toBe("pipeline.inputs.replacement");
});
