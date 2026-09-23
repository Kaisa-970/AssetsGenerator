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
  await page
    .getByRole("button", { name: "＋ 添加模型服务", exact: true })
    .click();
  return { compiles, requests };
}
test("detect, add and create an explicitly bound model node without losing the graph", async ({
  page,
}) => {
  const { compiles, requests } = await setup(page);
  await page.getByRole("button", { name: "＋ 管线输入", exact: true }).click();
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
    page.locator('.react-flow__node[data-id="input:input"]'),
  ).toHaveCount(1);
  await page.getByRole("button", { name: /My mesh model.*模型服务/ }).click();
  await expect
    .poll(() => compiles.at(-1)?.nodes.shape_generation?.backend)
    .toBe(service.backend);
  expect(compiles.at(-1).nodes.shape_generation.adapter).toBeUndefined();
  expect(compiles.at(-1).nodes.shape_generation.parameters).toEqual({
    steps: 12,
  });
  await expect(page.getByLabel("参数 steps", { exact: true })).toHaveValue(
    "12",
  );
  await page.getByLabel("参数 steps", { exact: true }).fill("20");
  await page.getByLabel("参数 steps", { exact: true }).blur();
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
  await expect(page.getByLabel("参数 steps", { exact: true })).toHaveValue(
    "12",
  );
});

test("RGBA preset is sourced from shape contract and connects a fresh image input", async ({
  page,
}) => {
  const { compiles } = await setup(page);
  await page.getByRole("button", { name: "＋ 管线输入", exact: true }).click();
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
    .poll(() => compiles.at(-1)?.inputs?.input?.kinds?.[0])
    .toBe("rgba_image");
  await expect(page.getByLabel("参数 steps", { exact: true })).toHaveValue(
    "12",
  );
});

test("expanded discovery does not collapse the operator directory on a laptop screen", async ({ page }) => {
  await page.setViewportSize({ width: 1366, height: 768 });
  await setup(page);
  const directory = page.getByLabel("可添加节点目录", { exact: true });
  const initial = await directory.boundingBox();
  expect(initial!.height).toBeGreaterThan(250);
  await page.getByLabel("模型服务地址").fill(service.endpoint);
  await page.getByRole("button", { name: "检测服务", exact: true }).click();
  await expect(page.getByRole("region", { name: "检测到的模型" })).toBeVisible();
  expect((await directory.boundingBox())!.height).toBeCloseTo(initial!.height, 0);
  const operator = directory.getByRole("button", { name: /^shape_generation/ });
  await operator.scrollIntoViewIfNeeded();
  await operator.click();
  await expect(page.locator('.react-flow__node').filter({ hasText: "shape_generation@1" })).toHaveCount(1);
  await page.getByRole("button", { name: "确认添加模型", exact: true }).click();
  await expect(page.getByRole("button", { name: "＋ 添加模型服务", exact: true })).toHaveAttribute("aria-expanded", "false");
  await expect(page.getByRole("region", { name: "检测到的模型" })).toHaveCount(0);
});
