import { test, expect, type Page } from "@playwright/test";

const descriptor = {
  schema_version: "model_service@1",
  display_name: "My mesh model",
  service_id: "mesh",
  backend_digest: "sha256:deployment",
  operator: "shape_generation@1",
  transport: "remote_jobs@1",
  frame_id: "triposr_glb_native",
  up_axis: "+Z",
  unit: "relative_unit",
  parameter_schema: {
    type: "object",
    properties: { steps: { type: "integer", minimum: 1, maximum: 50 } },
  },
  defaults: { steps: 12 },
  capabilities: [
    {
      capability_id: "shape_generation",
      display_name: "图像生成网格",
      operator: "shape_generation@1",
      frame_id: "triposr_glb_native",
      up_axis: "+Z",
      unit: "relative_unit",
      parameter_schema: {
        type: "object",
        properties: { steps: { type: "integer", minimum: 1, maximum: 50 } },
      },
      defaults: { steps: 12 },
    },
  ],
};
const service = {
  backend: "model_mesh",
  display_name: descriptor.display_name,
  endpoint: "http://model:8780",
  operator: descriptor.operator,
  descriptor_digest: "sha256:descriptor",
  frame_id: descriptor.frame_id,
  up_axis: descriptor.up_axis,
  unit: descriptor.unit,
};
const catalog = {
  operators: {
    "shape_generation@1": {
      name: "shape_generation",
      version: "1",
      inputs: { image: { kinds: ["rgba_image"], carriers: ["artifact_ref"] } },
      outputs: { mesh: { kinds: ["mesh"], carriers: ["artifact_ref"] } },
    },
  },
  adapters: [],
  backends: [],
  model_services: [],
  templates: [],
};
const addedCatalog = {
  ...catalog,
  model_services: [service],
  backends: [
    {
      backend: service.backend,
      adapter: "remote@1",
      name: "remote",
      version: "1",
      operators: [descriptor.operator],
      parameter_schema: descriptor.parameter_schema,
      defaults: descriptor.defaults,
    },
  ],
};
async function setup(page: Page, fail = false) {
  let added = false;
  const compiles: any[] = [];
  const requests: any[] = [];
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    let body: any = {};
    if (path === "/api/catalog") body = added ? addedCatalog : catalog;
    if (path === "/api/drafts") body = { drafts: [] };
    if (path === "/api/compile") {
      compiles.push(route.request().postDataJSON().pipeline);
      body = { ok: true };
    }
    if (path === "/api/model-services/detect") {
      requests.push(route.request().postDataJSON());
      if (fail) {
        await route.fulfill({ status: 422, json: { error: "服务协议不兼容" } });
        return;
      }
      body = {
        endpoint: service.endpoint,
        descriptor,
        descriptor_digest: service.descriptor_digest,
      };
    }
    if (path === "/api/model-services") {
      requests.push(route.request().postDataJSON());
      added = true;
      body = { ...service, catalog: addedCatalog };
    }
    await route.fulfill({ json: body });
  });
  await page.goto("/");
  await expect(
    page.getByText("地址添加目前仅支持图生 Mesh。", { exact: true }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "＋ 添加模型服务", exact: true })
    .click();
  await expect(
    page.getByText(/SAM3 文字分割等其他服务目前需要管理员配置/),
  ).toBeVisible();
  return { compiles, requests };
}
test("detect, add and create an explicitly bound model node without losing the graph", async ({
  page,
}) => {
  const { compiles, requests } = await setup(page);
  await page.getByLabel("添加输入节点").selectOption("rgba");
  await page.getByLabel("模型服务地址").fill(service.endpoint);
  await page.getByRole("button", { name: "检测服务", exact: true }).click();
  const detected = page.getByRole("region", { name: "检测到的模型" });
  await expect(detected).toContainText("rgba_image");
  await expect(detected).toContainText("triposr_glb_native");
  await expect(detected).toContainText("+Z");
  await expect(detected).toContainText("relative_unit");
  await expect(detected).toContainText("steps · integer · 默认 12");
  await page.getByRole("button", { name: "确认添加模型", exact: true }).click();
  await expect(
    page
      .getByRole("region", { name: "模型服务", exact: true })
      .getByRole("status"),
  ).toContainText("已添加 My mesh model");
  await expect(
    page.locator('.react-flow__node[data-id="input:image_2"]'),
  ).toHaveCount(1);
  await page.getByRole("button", { name: /My mesh model.*模型服务/ }).click();
  const createdNode = page.locator(
    '.react-flow__node[data-id="shape_generation"]',
  );
  await expect(createdNode).toBeVisible();
  await expect
    .poll(async () => (await createdNode.boundingBox())?.width || 0)
    .toBeGreaterThanOrEqual(250);
  await expect
    .poll(() => compiles.at(-1)?.nodes.shape_generation?.backend)
    .toBe(service.backend);
  expect(compiles.at(-1).nodes.shape_generation.adapter).toBeUndefined();
  expect(compiles.at(-1).nodes.shape_generation.parameters).toEqual({});
  const nodeModel = page.getByLabel("节点 shape_generation 的模型", {
    exact: true,
  });
  const detailsModel = page.getByLabel("节点 Backend", { exact: true });
  await expect(nodeModel).toHaveValue(service.backend);
  await expect(detailsModel).toHaveValue(service.backend);
  await expect(nodeModel.locator("option:checked")).toHaveText(
    "My mesh model · model_mesh",
  );
  await expect(detailsModel.locator("option:checked")).toHaveText(
    "My mesh model · model_mesh",
  );
  await expect(
    page.locator(".inspector").getByLabel("参数 steps 来源", { exact: true }),
  ).toHaveText("沿用实现默认值");
  await expect(
    page.locator(".inspector").getByLabel("参数 steps", { exact: true }),
  ).toHaveValue("12");
  await page
    .locator(".inspector")
    .getByLabel("参数 steps", { exact: true })
    .fill("20");
  await page
    .locator(".inspector")
    .getByLabel("参数 steps", { exact: true })
    .blur();
  await expect
    .poll(() => compiles.at(-1)?.nodes.shape_generation.parameters.steps)
    .toBe(20);
  expect(requests[1]).toEqual({
    endpoint: service.endpoint,
    descriptor_digest: service.descriptor_digest,
  });
  const card = page.getByRole("button", { name: /My mesh model.*模型服务/ });
  await expect(card).toContainText("triposr_glb_native");
  await expect(card).toContainText("+Z");
  await expect(card).toContainText("relative_unit");
  await page.reload();
  await expect(card).toContainText("triposr_glb_native");
  await expect(card).toContainText("+Z");
  await expect(card).toContainText("relative_unit");
  await expect(
    page.getByRole("button", { name: /My mesh model.*模型服务/ }),
  ).toBeVisible();
});
test("editing the URL invalidates confirmation and a late old detection cannot restore it", async ({
  page,
}) => {
  await setup(page);
  await page.getByLabel("模型服务地址").fill(service.endpoint);
  await page.getByRole("button", { name: "检测服务", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "确认添加模型", exact: true }),
  ).toBeVisible();
  await page.getByLabel("模型服务地址").fill("http://another-model:8780");
  await expect(
    page.getByRole("button", { name: "确认添加模型", exact: true }),
  ).toHaveCount(0);
  let release!: () => void;
  const delayed = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.route("**/api/model-services/detect", async (route) => {
    await delayed;
    await route
      .fulfill({
        json: {
          endpoint: service.endpoint,
          descriptor,
          descriptor_digest: service.descriptor_digest,
        },
      })
      .catch(() => {});
  });
  await page.getByRole("button", { name: "检测服务", exact: true }).click();
  await expect(page.getByRole("button", { name: "正在检测…" })).toBeVisible();
  await page.getByLabel("模型服务地址").fill("http://third-model:8780");
  release();
  await page.waitForTimeout(150);
  await expect(
    page.getByRole("button", { name: "确认添加模型", exact: true }),
  ).toHaveCount(0);
});
test("protocol failure is shown and never enables adding", async ({ page }) => {
  await setup(page, true);
  await page.getByLabel("模型服务地址").fill(service.endpoint);
  await page.getByRole("button", { name: "检测服务", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("服务协议不兼容");
  await expect(
    page.getByRole("button", { name: "确认添加模型", exact: true }),
  ).toHaveCount(0);
});

test("dragged service keeps its binding and an add conflict requires re-detection", async ({
  page,
}) => {
  const { compiles } = await setup(page);
  await page.getByLabel("模型服务地址").fill(service.endpoint);
  await page.getByRole("button", { name: "检测服务", exact: true }).click();
  await page.route("**/api/model-services", async (route) => {
    await route.fulfill({
      status: 409,
      json: { error: "服务身份已变化，请重新检测" },
    });
  });
  await page.getByRole("button", { name: "确认添加模型", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("服务身份已变化");
  await expect(
    page.getByRole("button", { name: "确认添加模型", exact: true }),
  ).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: /My mesh model.*模型服务/ }),
  ).toHaveCount(0);
  await page.unroute("**/api/model-services");
  await page.getByRole("button", { name: "检测服务", exact: true }).click();
  await page.getByRole("button", { name: "确认添加模型", exact: true }).click();
  const transfer = await page.evaluateHandle(() => new DataTransfer());
  await page
    .getByRole("button", { name: /My mesh model.*模型服务/ })
    .dispatchEvent("dragstart", { dataTransfer: transfer });
  await page.locator(".react-flow").dispatchEvent("drop", {
    dataTransfer: transfer,
    clientX: 650,
    clientY: 350,
  });
  await expect
    .poll(() => compiles.at(-1)?.nodes.shape_generation?.backend)
    .toBe(service.backend);
  await expect(
    page.locator(".inspector").getByLabel("参数 steps", { exact: true }),
  ).toHaveValue("12");
});

test("RGBA preset is sourced from shape contract and connects a fresh image input", async ({
  page,
}) => {
  const { compiles } = await setup(page);
  await page.getByLabel("添加输入节点").selectOption("rgba");
  // Adding an input already selects it; do not click overlapping canvas nodes.
  await page
    .getByLabel("输入格式快捷设置")
    .selectOption({ label: "透明 RGBA 图片（图生 Mesh 输入）" });
  await expect(page.getByLabel("输入数据类型")).toHaveValue("rgba_image");
  await page.getByLabel("模型服务地址").fill(service.endpoint);
  await page.getByRole("button", { name: "检测服务", exact: true }).click();
  await page.getByRole("button", { name: "确认添加模型", exact: true }).click();
  await page.getByRole("button", { name: /My mesh model.*模型服务/ }).click();
  await expect
    .poll(() => compiles.at(-1)?.nodes.shape_generation?.backend)
    .toBe(service.backend);
  await expect
    .poll(() => compiles.at(-1)?.inputs?.image_2?.kinds?.[0])
    .toBe("rgba_image");
  await expect(
    page.locator(".inspector").getByLabel("参数 steps", { exact: true }),
  ).toHaveValue("12");
});

test("expanded discovery does not collapse the operator directory on a laptop screen", async ({
  page,
}) => {
  await page.setViewportSize({ width: 1366, height: 768 });
  await setup(page);
  const directory = page.getByLabel("可添加节点目录", { exact: true });
  const initial = await directory.boundingBox();
  expect(initial!.height).toBeGreaterThan(250);
  await page.getByLabel("模型服务地址").fill(service.endpoint);
  await page.getByRole("button", { name: "检测服务", exact: true }).click();
  await expect(
    page.getByRole("region", { name: "检测到的模型" }),
  ).toBeVisible();
  expect((await directory.boundingBox())!.height).toBeCloseTo(
    initial!.height,
    0,
  );
  const operator = directory
    .locator(".catalog-item")
    .filter({ hasText: "shape_generation" });
  await operator.scrollIntoViewIfNeeded();
  await operator.click();
  await expect(
    page.locator(".react-flow__node").filter({ hasText: "shape_generation@1" }),
  ).toHaveCount(1);
  await page.getByRole("button", { name: "确认添加模型", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "＋ 添加模型服务", exact: true }),
  ).toHaveAttribute("aria-expanded", "false");
  await expect(page.getByRole("region", { name: "检测到的模型" })).toHaveCount(
    0,
  );
});

test("operator catalog explains add action and empty search recovery", async ({
  page,
}) => {
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    await route.fulfill({
      json:
        path === "/api/catalog"
          ? {
              execution_enabled: true,
              operators: {
                "resize_image@1": {
                  name: "resize_image",
                  version: "1",
                  inputs: {},
                  outputs: {},
                },
              },
              adapters: [],
              templates: [],
            }
          : path === "/api/drafts"
            ? { drafts: [] }
            : { runs: [] },
    });
  });
  await page.goto("/");
  await expect(page.getByText("1 个算子", { exact: true })).toBeVisible();
  await expect(
    page.getByText("点击添加到画布 · 也可拖动", { exact: true }),
  ).toBeVisible();
  await page.getByLabel("搜索算子", { exact: true }).fill("no-such-operator");
  await expect(page.getByText(/没有匹配的算子/)).toBeVisible();
  await page.getByRole("button", { name: "清除搜索", exact: true }).click();
  await expect(page.getByText("resize_image", { exact: true })).toBeVisible();
});

test("single search result can be added with Enter and becomes selected", async ({
  page,
}) => {
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    await route.fulfill({
      json:
        path === "/api/catalog"
          ? {
              execution_enabled: true,
              operators: {
                "encode_png@1": {
                  name: "encode_png",
                  version: "1",
                  inputs: {},
                  outputs: {},
                },
              },
              adapters: [],
              templates: [],
            }
          : path === "/api/drafts"
            ? { drafts: [] }
            : { runs: [] },
    });
  });
  await page.goto("/");
  const search = page.getByLabel("搜索算子", { exact: true });
  await search.fill("encode_png");
  await expect(page.getByText("按 Enter 添加", { exact: true })).toBeVisible();
  await search.fill("编码图片");
  await expect(page.locator(".catalog-item")).toHaveCount(1);
  await expect(page.locator(".catalog-item strong")).toHaveText("编码图片");
  await search.press("Enter");
  await expect(page.locator('[data-id="encode_png"]')).toHaveClass(
    /react-flow__node/,
  );
  await expect(page.locator('[data-id="encode_png"] article')).toHaveClass(
    /blueprint-node-selected/,
  );
});

test("fixed deployment fields stay behind details while editable parameters remain direct", async ({
  page,
}) => {
  await setup(page);
  await page.route("**/api/catalog", (route) =>
    route.fulfill({
      json: {
        ...addedCatalog,
        backends: [
          {
            ...addedCatalog.backends[0],
            defaults: { steps: 12, deployment: "fixed-v1" },
            parameter_schema: {
              type: "object",
              properties: {
                deployment: { type: "string", enum: ["fixed-v1"] },
                steps: { type: "integer", minimum: 1 },
              },
            },
          },
        ],
      },
    }),
  );
  await page.reload();
  await page.getByRole("button", { name: /My mesh model.*模型服务/ }).click();
  const details = page.locator(".inspector");
  await expect(details.getByLabel("参数 steps", { exact: true })).toBeVisible();
  await expect(
    details.getByLabel("参数 deployment", { exact: true }),
  ).toBeHidden();
  await details.getByText("固定配置 · 1 项", { exact: true }).click();
  await expect(
    details.getByLabel("参数 deployment", { exact: true }),
  ).toBeVisible();
  await expect(
    details.getByLabel("参数 deployment", { exact: true }),
  ).toBeDisabled();
  await details.getByLabel("参数 steps", { exact: true }).fill("19");
  await details.getByLabel("参数 steps", { exact: true }).blur();
  await expect(page.getByLabel("节点参数 steps", { exact: true })).toHaveValue(
    "19",
  );
});
