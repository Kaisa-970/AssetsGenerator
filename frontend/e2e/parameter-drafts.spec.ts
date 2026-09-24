import { expect, test } from "@playwright/test";

test("unapplied parameters survive node and tab changes; history badges name their source", async ({
  page,
}) => {
  const node = {
    operator: "resize@1",
    adapter: "resize@1",
    parameters: { width: 4 },
    inputs: { image: "pipeline.inputs.image" },
  };
  const pipeline = {
    pipeline: "draft_test",
    version: "1",
    inputs: { image: { kind: "rgb_image", carriers: ["artifact_ref"] } },
    nodes: { first: node, second: structuredClone(node) },
  };
  let starts = 0;
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    let body: unknown = {};
    if (path === "/api/catalog")
      body = {
        execution_enabled: true,
        operators: {
          "resize@1": {
            name: "resize",
            version: "1",
            inputs: { image: { kinds: ["rgb_image"] } },
            outputs: { image: { kinds: ["rgb_image"] } },
          },
        },
        adapters: [
          {
            name: "resize",
            version: "1",
            operators: ["resize@1"],
            defaults: { width: 4 },
            parameter_schema: {
              type: "object",
              properties: { width: { type: "integer", minimum: 1 } },
            },
          },
        ],
        templates: [{ id: "draft", label: "参数草稿测试", pipeline }],
      };
    else if (path === "/api/drafts") body = { drafts: [] };
    else if (path === "/api/compile")
      body = { ok: true, execution_ready: true, plan: {} };
    else if (path === "/api/runs") {
      if (route.request().method() === "POST") starts++;
      body = { runs: [] };
    }
    await route.fulfill({ json: body });
  });
  await page.setViewportSize({ width: 1600, height: 1100 });
  page.on("dialog", (dialog) => dialog.accept());
  await page.goto("/");
  await page.getByRole("button", { name: "参数草稿测试", exact: true }).click();
  await page.getByLabel("图片来源", { exact: true }).selectOption("path");
  await page
    .getByLabel("运行图片路径", { exact: true })
    .fill("/tmp/parameter-draft-test.png");
  await expect(
    page.getByRole("button", {
      name: "启动新运行 · 检查执行范围",
      exact: true,
    }),
  ).toBeEnabled();
  await page.locator('.react-flow__node[data-id="first"]').click();
  await page.getByRole("button", { name: "配置", exact: true }).click();
  await page.getByLabel("参数 width", { exact: true }).fill("bad");
  await page.getByRole("button", { name: "运行", exact: true }).click();
  await expect(page.getByLabel("未应用参数", { exact: true })).toContainText(
    "first",
  );
  await expect(
    page.getByRole("button", {
      name: "启动新运行 · 检查执行范围",
      exact: true,
    }),
  ).toBeDisabled();
  await page
    .getByRole("button", { name: "编辑待应用参数 · first", exact: true })
    .click();
  await page.getByRole("button", { name: "复制节点配置", exact: true }).click();
  await expect(page.getByLabel("参数 width", { exact: true })).toHaveValue("4");
  await page.locator('.react-flow__node[data-id="second"]').click();
  await page.getByRole("button", { name: "配置", exact: true }).click();
  await expect(page.getByLabel("参数 width", { exact: true })).toHaveValue("4");
  await page
    .getByRole("button", { name: "编辑待应用参数 · first", exact: true })
    .click();
  await expect(page.getByLabel("参数 width", { exact: true })).toHaveValue(
    "bad",
  );
  await page.getByLabel("参数 width", { exact: true }).fill("8");
  await page.getByLabel("参数 width", { exact: true }).press("Tab");
  await expect(page.getByLabel("未应用参数", { exact: true })).toHaveCount(0);
  await page.getByLabel("节点参数 JSON", { exact: true }).fill("{");
  await page.getByRole("button", { name: "运行", exact: true }).click();
  await expect(page.getByLabel("未应用参数", { exact: true })).toBeVisible();
  await page
    .getByRole("button", { name: "编辑待应用参数 · first", exact: true })
    .click();
  await expect(page.getByLabel("节点参数 JSON", { exact: true })).toHaveValue(
    "{",
  );
  await page.getByRole("button", { name: "放弃全部未应用参数编辑" }).click();
  await expect(page.getByLabel("节点参数 JSON", { exact: true })).toHaveValue(
    /"width": 8/,
  );
  await page.locator('input[type="file"][accept=".json"]').setInputFiles({
    name: "history.json",
    mimeType: "application/json",
    buffer: Buffer.from(
      JSON.stringify({
        run_id: "history_A",
        dag: { node_states: { first: { status: "succeeded" } } },
      }),
    ),
  });
  await expect(page.getByLabel("画布历史状态来源")).toContainText("history_A");
  await expect(
    page.locator('.react-flow__node[data-id="first"]'),
  ).toContainText("历史 · 完成");
  await page.getByRole("button", { name: "运行", exact: true }).click();
  await expect(page.getByLabel("画布历史状态来源")).toBeVisible();
  expect(starts).toBe(0);
});
