import { test, expect } from "@playwright/test";

test("missing schema explains rejected wire and RGB preset connects without JSON", async ({
  page,
}) => {
  const contract = {
    kinds: ["rgb_image"],
    carriers: ["artifact_ref"],
    schema_name: "raster_image",
    schema_version: "1.0",
    cardinality: "one",
  };
  const compiles: any[] = [];
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    let body: any = {};
    if (path === "/api/catalog")
      body = {
        operators: {
          "encode_png@1": {
            name: "encode_png",
            version: "1",
            inputs: { image: contract },
            outputs: { image: { ...contract, schema_name: "png" } },
          },
        },
        adapters: [],
        templates: [],
      };
    else if (path === "/api/drafts") body = { drafts: [] };
    else if (path === "/api/compile") {
      compiles.push(route.request().postDataJSON().pipeline);
      body = { ok: true };
    }
    await route.fulfill({ json: body });
  });
  await page.goto("/");
  await page.getByRole("button", { name: "展开节点目录", exact: true }).click();
  await page
    .locator(".catalog-item")
    .filter({ has: page.getByText("encode_png", { exact: true }) })
    .click();
  await page.getByRole("button", { name: "收起节点目录", exact: true }).click();
  const source = page.locator(
    '.react-flow__node[data-id="input:image"] .react-flow__handle.source',
  );
  const target = page.locator(
    '.react-flow__node[data-id="encode_png"] .react-flow__handle.target',
  );
  await expect(target).toBeVisible();
  // Wait for fitView to settle before dragging port handles.
  await page.waitForTimeout(400);
  await source.dragTo(target);
  await expect(page.getByRole("alert")).toContainText(
    "schema_name 不匹配：未声明 → raster_image",
  );
  await expect(page.locator(".react-flow__edge")).toHaveCount(0);
  await page
    .getByLabel("输入格式快捷设置")
    .selectOption({ label: "普通 RGB 图片（上传 JPG / PNG）" });
  await expect(page.getByLabel("输入 Schema 名称")).toHaveValue("raster_image");
  await expect(page.getByLabel("输入 Schema 版本")).toHaveValue("1.0");
  await source.dragTo(target);
  await expect(page.locator(".react-flow__edge")).toHaveCount(1);
  await expect(page.getByRole("alert")).toHaveCount(0);
  await expect
    .poll(() => compiles.at(-1)?.nodes.encode_png.inputs.image)
    .toBe("pipeline.inputs.image");
  expect(compiles.at(-1).inputs.image).toEqual(contract);
  // Editing one form field must preserve advanced spatial metadata.
  await page
    .getByLabel("输入契约 · JSON")
    .fill(JSON.stringify({ ...contract, frame_id: "custom", unit: "meter" }));
  await page.getByRole("button", { name: "应用输入契约", exact: true }).click();
  await page.getByLabel("输入 Schema 版本").fill("2.0");
  await expect
    .poll(() => compiles.at(-1)?.inputs.image.schema_version)
    .toBe("2.0");
  expect(compiles.at(-1).inputs.image.frame_id).toBe("custom");
  expect(compiles.at(-1).inputs.image.unit).toBe("meter");
});

test("structured carrier uses the backend enum and compiles", async ({
  page,
}) => {
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    const body =
      path === "/api/catalog"
        ? { operators: {}, adapters: [], templates: [] }
        : path === "/api/drafts"
          ? { drafts: [] }
          : path === "/api/compile"
            ? { ok: true, execution_ready: true }
            : {};
    await route.fulfill({ json: body });
  });
  await page.goto("/");
  await page.locator('.react-flow__node[data-id="input:image"]').click();
  await page.getByRole("button", { name: "展开属性面板", exact: true }).click();
  await page.getByLabel("输入载体").selectOption("structured");
  await expect(page.getByLabel("输入载体")).toHaveValue("structured");
  await expect(page.getByLabel("当前配置编译状态")).toContainText(/编译/);
});
