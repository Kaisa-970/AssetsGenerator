import { test, expect } from "@playwright/test";

test("docked mesh preview opens over the configuration tab", async ({
  page,
}) => {
  const mutations: string[] = [];
  const pipeline = {
    pipeline: "mesh",
    version: "1",
    inputs: {},
    nodes: { shape: { operator: "shape@1", inputs: {} } },
  };
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (route.request().method() !== "GET" && path !== "/api/compile") mutations.push(path);
    const body =
      path === "/api/catalog"
        ? {
            execution_enabled: true,
            operators: {},
            adapters: [],
            templates: [{ id: "mesh", label: "模型预览测试", pipeline }],
          }
        : path === "/api/drafts"
          ? { drafts: [] }
          : path === "/api/compile"
            ? { ok: true, execution_ready: true }
            : path === "/api/runs"
              ? { runs: [{ run_id: "historical", status: "succeeded" }] }
              : {
                  run: {
                    run_id: "historical",
                    status: "succeeded",
                    dag: {
                      revision: 1,
                      node_states: {
                        shape: { status: "succeeded", attempts: [] },
                      },
                    },
                  },
                  outputs: [
                    {
                      node_id: "shape",
                      port: "mesh",
                      kind: "triangle_mesh",
                      url: "/fixture.glb",
                    },
                  ],
                };
    await route.fulfill({ json: body });
  });
  await page.route("**/fixture.glb", (route) => route.fulfill({ status: 404 }));
  page.on("dialog", (d) => d.accept());
  await page.goto("/");
  await page.getByRole("button", { name: "模型预览测试", exact: true }).click();
  await page.getByRole("button", { name: "继续替换", exact: true }).click();
  await page.getByLabel("选择运行", { exact: true }).selectOption("historical");
  const shape = page.locator('[data-id="shape"]');
  await expect(shape.getByLabel("输出状态 shape")).toContainText("mesh");
  await page.getByRole("button", { name: "收起节点预览", exact: true }).click();
  await shape.getByRole("button", { name: "查看输出", exact: true }).click();
  await expect(page.locator("#node-preview-window")).toBeVisible();
  await expect(page.locator("#node-preview-window")).toContainText("historical");
  await page.getByRole("button", { name: "配置", exact: true }).click();
  await page
    .locator("#node-preview-window")
    .getByRole("button", { name: /预览模型/ })
    .click();
  await expect(page.getByRole("dialog", { name: "模型预览" })).toBeVisible();
  expect(mutations).toEqual([]);
});
