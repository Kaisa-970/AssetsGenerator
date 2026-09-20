import { test, expect } from "@playwright/test";
test("edits a template, saves layout, compiles and reloads a draft", async ({
  page,
}) => {
  const pipeline = {
    pipeline: "test_pipeline",
    version: "1",
    inputs: { image: { kind: "rgb_image", carriers: ["artifact_ref"] } },
    nodes: {
      copy: { operator: "copy@1", inputs: { image: "pipeline.inputs.image" } },
    },
  };
  const catalog = {
    operators: {
      "copy@1": {
        name: "copy",
        version: "1",
        inputs: { image: { kinds: ["rgb_image"], carriers: ["artifact_ref"] } },
        outputs: {
          image: { kinds: ["rgb_image"], carriers: ["artifact_ref"] },
        },
      },
    },
    adapters: [],
    templates: [{ id: "template", label: "单图测试", pipeline }],
  };
  let saved: unknown;
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    let body: unknown = {};
    if (url.pathname === "/api/catalog") body = catalog;
    else if (url.pathname === "/api/compile")
      body = {
        ok: true,
        execution_ready: false,
        scope: "static_contracts_only",
        plan: { pipeline_name: "test_pipeline" },
      };
    else if (url.pathname === "/api/drafts")
      body = { drafts: saved ? ["my-pipeline"] : [] };
    else if (route.request().method() === "PUT") {
      saved = route.request().postDataJSON();
      body = { saved: "my-pipeline" };
    } else body = saved;
    await route.fulfill({ json: body });
  });
  await page.goto("/");
  page.on("dialog", (d) => d.accept());
  await page.getByRole("button", { name: "单图测试", exact: true }).click();
  await expect(page.locator(".react-flow__node")).toHaveCount(2);
  await expect(page.locator(".react-flow__edge")).toHaveCount(1);
  await page.locator('.react-flow__node[data-id="copy"]').click();
  await page.getByLabel("实例 ID").fill("copy_renamed");
  await page.getByLabel("实例 ID").press("Tab");
  await expect(
    page.locator('.react-flow__node[data-id="copy_renamed"]'),
  ).toBeVisible();
  await page.getByRole("button", { name: "保存草稿", exact: true }).click();
  await expect(page.getByRole("status")).toContainText("草稿已保存");
  expect(saved).toMatchObject({
    pipeline: {
      nodes: {
        copy_renamed: {
          operator: "copy@1",
          inputs: { image: "pipeline.inputs.image" },
        },
      },
    },
  });
  await page.getByRole("button", { name: "编译校验", exact: true }).click();
  await expect(page.getByRole("status")).toContainText("编译通过");
  await expect(page.locator(".inspector pre")).toContainText(
    "static_contracts_only",
  );
  await page.getByRole("button", { name: "配置", exact: true }).click();
  await page.getByLabel("加载草稿").selectOption("my-pipeline");
  await expect(page.getByRole("status")).toContainText("已加载草稿");
  expect(errors).toEqual([]);
});

test("combined deletion persists and dragging keeps canvas stable", async ({
  page,
}) => {
  const port = { kinds: ["rgb_image"], carriers: ["artifact_ref"] };
  const pipeline = {
    pipeline: "delete_test",
    version: "1",
    inputs: { image: { kind: "rgb_image", carriers: ["artifact_ref"] } },
    nodes: {
      A: { operator: "copy@1", inputs: { image: "pipeline.inputs.image" } },
      B: { operator: "copy@1", inputs: { image: "A.outputs.image" } },
      C: { operator: "copy@1", inputs: { image: "pipeline.inputs.image" } },
    },
  };
  let saved: any;
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    const body =
      path === "/api/catalog"
        ? {
            operators: {
              "copy@1": {
                name: "copy",
                version: "1",
                inputs: { image: port },
                outputs: { image: port },
              },
            },
            adapters: [],
            templates: [{ id: "t", label: "删除测试", pipeline }],
          }
        : path === "/api/drafts"
          ? { drafts: [] }
          : route.request().method() === "PUT"
            ? ((saved = route.request().postDataJSON()),
              { saved: "my-pipeline" })
            : {};
    await route.fulfill({ json: body });
  });
  page.on("dialog", (d) => d.accept());
  await page.goto("/");
  await page.getByRole("button", { name: "删除测试", exact: true }).click();
  const c = page.locator('.react-flow__node[data-id="C"]');
  await c.click();
  const viewport = await page
    .locator(".react-flow__viewport")
    .getAttribute("style");
  const box = await c.boundingBox();
  if (!box) throw Error("missing C");
  const start = { x: box.x + box.width / 2, y: box.y + 20 };
  await page.mouse.move(start.x, start.y);
  await page.mouse.down();
  const xs: number[] = [];
  for (let i = 1; i <= 8; i++) {
    await page.mouse.move(start.x + i * 8, start.y + i * 3);
    await page.waitForTimeout(25);
    xs.push((await c.boundingBox())!.x);
    await expect(c).toBeVisible();
    expect(
      await page.locator(".react-flow__viewport").getAttribute("style"),
    ).toBe(viewport);
  }
  await page.mouse.up();
  expect(xs.at(-1)! - xs[0]).toBeGreaterThan(35);
  for (let i = 1; i < xs.length; i++)
    expect(xs[i]).toBeGreaterThanOrEqual(xs[i - 1] - 1);
  await page.getByRole("button", { name: "保存草稿", exact: true }).click();
  await expect(page.getByRole("status")).toContainText("草稿已保存");
  expect(saved.layout.C).toBeDefined();
  // Focus the independent edge and use React Flow keyboard multi-selection.
  const independent = page.locator('.react-flow__edge[data-id="B:image"]');
  await independent.focus();
  await page.keyboard.down("Control");
  await page.keyboard.press("Enter");
  await page.keyboard.up("Control");
  await expect(independent).toHaveClass(/selected/);
  await expect(c).toHaveClass(/selected/);
  await page.keyboard.press("Delete");
  await expect(c).toHaveCount(0);
  await expect(page.getByLabel("实例 ID")).toHaveCount(0);
  await page.getByRole("button", { name: "保存草稿", exact: true }).click();
  await expect(page.getByRole("status")).toContainText("草稿已保存");
  expect(saved.pipeline.nodes.C).toBeUndefined();
  expect(saved.pipeline.nodes.B.inputs.image).toBeUndefined();
});
