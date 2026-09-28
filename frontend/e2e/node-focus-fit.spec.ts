import { expect, test } from "@playwright/test";

for (const size of [
  { width: 1440, height: 900 },
  { width: 1920, height: 1080 },
]) {
  test(`locating a tall node fits its title, fields and pins at ${size.width}`, async ({
    page,
  }) => {
    await page.setViewportSize(size);
    const port = { kind: "rgb_image", carriers: ["artifact_ref"] };
    await page.route("**/api/**", (route) => {
      const path = new URL(route.request().url()).pathname;
      return route.fulfill({
        json:
          path === "/api/catalog"
            ? {
                execution_enabled: true,
                operators: {
                  "resize_image@1": {
                    name: "resize_image",
                    version: "1",
                    inputs: { image: port },
                    outputs: { image: port },
                  },
                },
                adapters: [
                  {
                    name: "resize_image",
                    version: "1",
                    operators: ["resize_image@1"],
                    parameter_schema: {
                      type: "object",
                      required: ["height", "resampling", "width"],
                      properties: {
                        height: { type: "integer", minimum: 1 },
                        resampling: {
                          type: "string",
                          enum: ["lanczos", "nearest"],
                        },
                        width: { type: "integer", minimum: 1 },
                      },
                    },
                  },
                ],
                templates: [
                  {
                    id: "tall",
                    label: "高节点定位",
                    pipeline: {
                      pipeline: "tall",
                      version: "1",
                      inputs: { image: port },
                      nodes: {
                        resize_image: {
                          operator: "resize_image@1",
                          adapter: "resize_image@1",
                          inputs: { image: "pipeline.inputs.image" },
                          parameters: {
                            height: 64,
                            resampling: "lanczos",
                            width: 128,
                          },
                        },
                      },
                    },
                  },
                ],
              }
            : path === "/api/drafts"
              ? { drafts: [] }
              : path === "/api/compile"
                ? { ok: true, execution_ready: true }
                : { runs: [] },
      });
    });
    await page.goto("/");
    await page.getByRole("button", { name: "高节点定位", exact: true }).click();
    await page.getByRole("button", { name: "继续替换", exact: true }).click();
    // Keep preview and Details open. Focus changes only the viewport.
    await expect(
      page.getByRole("button", { name: "收起节点预览", exact: true }),
    ).toBeVisible();
    await page
      .getByLabel("定位画布节点", { exact: true })
      .selectOption("resize_image");
    const node = page.locator('[data-id="resize_image"]');
    const canvas = page.locator(".canvas > .react-flow");
    const nodeTransform = await node.evaluate((el) => el.style.transform);
    const contained = async () => {
      const bounds = (await canvas.boundingBox())!;
      const rect = (await node.boundingBox())!;
      return (
        rect.y >= bounds.y + 30 &&
        rect.y + rect.height <= bounds.y + bounds.height - 15 &&
        rect.x >= bounds.x &&
        rect.x + rect.width <= bounds.x + bounds.width
      );
    };
    await expect.poll(contained).toBe(true);
    for (const locator of [
      node.locator(".blueprint-node-header"),
      node.getByLabel("节点参数 width", { exact: true }),
      node.locator(".react-flow__handle.source"),
      node.locator(".react-flow__handle.target"),
    ]) {
      expect(
        await locator.evaluate((el) => {
          const rect = el.getBoundingClientRect();
          const hit = document.elementFromPoint(
            rect.x + rect.width / 2,
            rect.y + rect.height / 2,
          );
          return !!hit && (el.contains(hit) || hit.contains(el));
        }),
      ).toBe(true);
    }
    await node.getByLabel("节点参数 width", { exact: true }).fill("256");
    await node.getByLabel("节点参数 width", { exact: true }).press("Tab");
    await page
      .getByRole("button", { name: "聚焦所选节点", exact: true })
      .click();
    await expect.poll(contained).toBe(true);
    expect(await node.evaluate((el) => el.style.transform)).toBe(nodeTransform);
    await expect(
      node.getByLabel("节点参数 width", { exact: true }),
    ).toHaveValue("256");
  });
}
