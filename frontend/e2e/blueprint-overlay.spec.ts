import { test, expect } from "@playwright/test";

const validGlb = Buffer.from(
  "Z2xURgIAAADoBAAA3AMAAEpTT057InNjZW5lIjowLCJzY2VuZXMiOlt7Im5vZGVzIjpbMF19XSwiYXNzZXQiOnsidmVyc2lvbiI6IjIuMCIsImdlbmVyYXRvciI6Imh0dHBzOi8vZ2l0aHViLmNvbS9taWtlZGgvdHJpbWVzaCJ9LCJhY2Nlc3NvcnMiOlt7ImNvbXBvbmVudFR5cGUiOjUxMjUsInR5cGUiOiJTQ0FMQVIiLCJidWZmZXJWaWV3IjowLCJjb3VudCI6MzYsIm1heCI6WzddLCJtaW4iOlswXX0seyJjb21wb25lbnRUeXBlIjo1MTI2LCJ0eXBlIjoiVkVDMyIsImJ5dGVPZmZzZXQiOjAsImJ1ZmZlclZpZXciOjEsImNvdW50Ijo4LCJtYXgiOlswLjUsMS4wLDEuNV0sIm1pbiI6Wy0wLjUsLTEuMCwtMS41XX1dLCJtZXNoZXMiOlt7Im5hbWUiOiJnZW9tZXRyeV8wIiwiZXh0cmFzIjp7InVuaXRzIjoibWV0ZXJzIiwiZnJvbV9nbHRmX3ByaW1pdGl2ZSI6ZmFsc2UsInNoYXBlIjoiYm94IiwiZXh0ZW50cyI6WzEuMCwyLjAsMy4wXX0sInByaW1pdGl2ZXMiOlt7ImF0dHJpYnV0ZXMiOnsiUE9TSVRJT04iOjF9LCJpbmRpY2VzIjowLCJtb2RlIjo0LCJtYXRlcmlhbCI6MH1dfV0sIm1hdGVyaWFscyI6W3sicGJyTWV0YWxsaWNSb3VnaG5lc3MiOnsiYmFzZUNvbG9yRmFjdG9yIjpbMC44LDAuNjk4MDM5MjE1Njg2Mjc0NSwwLjYsMS4wXX0sImFscGhhTW9kZSI6Ik9QQVFVRSIsImRvdWJsZVNpZGVkIjpmYWxzZX1dLCJub2RlcyI6W3sibmFtZSI6Imdlb21ldHJ5XzAiLCJtZXNoIjowLCJtYXRyaXgiOlstMC4zMzMzMzMzMzMzMzMzMzMzLDAuMCwwLjAsMC4wLDAuMCwwLjMzMzMzMzMzMzMzMzMzMzMsMC4wLDAuMCwwLjAsMC4wLC0wLjMzMzMzMzMzMzMzMzMzMzMsMC4wLDAuMCwwLjMzMzMzMzMzMzMzMzMzMzMsMC4wLDEuMF19XSwiYnVmZmVycyI6W3siYnl0ZUxlbmd0aCI6MjQwfV0sImJ1ZmZlclZpZXdzIjpbeyJidWZmZXIiOjAsImJ5dGVPZmZzZXQiOjAsImJ5dGVMZW5ndGgiOjE0NH0seyJidWZmZXIiOjAsImJ5dGVPZmZzZXQiOjE0NCwiYnl0ZUxlbmd0aCI6OTZ9XX0gICAg8AAAAEJJTgABAAAAAwAAAAAAAAAEAAAAAQAAAAAAAAAAAAAAAwAAAAIAAAACAAAABAAAAAAAAAABAAAABwAAAAMAAAAFAAAAAQAAAAQAAAAFAAAABwAAAAEAAAADAAAABwAAAAIAAAAGAAAABAAAAAIAAAACAAAABwAAAAYAAAAGAAAABQAAAAQAAAAHAAAABQAAAAYAAAAAAAC/AACAvwAAwL8AAAC/AACAvwAAwD8AAAC/AACAPwAAwL8AAAC/AACAPwAAwD8AAAA/AACAvwAAwL8AAAA/AACAvwAAwD8AAAA/AACAPwAAwL8AAAA/AACAPwAAwD8=",
  "base64",
);

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
    if (route.request().method() !== "GET" && path !== "/api/compile")
      mutations.push(path);
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
  await page.route("**/fixture.glb", (route) =>
    route.fulfill({
      status: 200,
      contentType: "model/gltf-binary",
      body: validGlb,
    }),
  );
  page.on("dialog", (d) => d.accept());
  await page.goto("/");
  await page.getByRole("button", { name: "展开节点目录", exact: true }).click();
  await page.getByRole("button", { name: "模型预览测试", exact: true }).click();
  await page.getByRole("button", { name: "继续替换", exact: true }).click();
  await page.getByLabel("选择运行", { exact: true }).selectOption("historical");
  const shape = page.locator('[data-id="shape"]');
  await expect(shape.getByLabel("输出状态 shape")).toContainText("mesh");
  await shape.getByRole("button", { name: "查看输出", exact: true }).click();
  await expect(page.locator("#node-preview-window")).toBeVisible();
  await expect(page.locator("#node-preview-window")).toContainText(
    "historical",
  );
  await page.getByRole("button", { name: "配置", exact: true }).click();
  await page
    .locator("#node-preview-window")
    .getByRole("button", { name: /加载模型预览/ })
    .click();
  await expect(page.getByRole("region", { name: "模型预览" })).toBeVisible();
  await expect(
    page.getByRole("region", { name: "模型预览" }).getByRole("status"),
  ).toContainText("模型已加载");
  await expect(page.getByRole("button", { name: /大窗口打开/ })).toBeVisible();
  await page.getByRole("button", { name: /关闭模型预览/ }).click();
  await expect(page.getByRole("region", { name: "模型预览" })).toHaveCount(0);
  expect(mutations).toEqual([]);
});
