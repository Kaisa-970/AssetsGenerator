import { test, expect } from "@playwright/test";
const text = {
  kinds: ["text"],
  carriers: ["artifact_ref"],
  schema_name: "plain_text",
  schema_version: "1.0",
  cardinality: "one",
};
const image = {
  kinds: ["rgb_image"],
  carriers: ["artifact_ref"],
  schema_name: "png",
  schema_version: "1.0",
  cardinality: "one",
};
const name = "remote_capability_0123456789abcdef01234567",
  key = `${name}@1`,
  backend = "model_cpu";
const capability = {
  capability_id: "text_to_image_v1",
  operator: name,
  display_name: "CPU 文生图",
  transport: "remote_jobs@1",
  inputs: { prompt: text },
  outputs: { image },
  parameter_schema: {
    type: "object",
    properties: { width: { type: "integer", minimum: 1 } },
  },
  defaults: { width: 64 },
};
const descriptor = {
  schema_version: "model_service@1",
  display_name: "CPU 图片服务",
  service_id: "cpu-images",
  backend_digest: "sha256:deployment",
  capabilities: [capability],
};
const initial = {
  operators: {
    "resize_image@1": {
      name: "resize_image",
      version: "1",
      inputs: { image },
      outputs: { image },
    },
  },
  adapters: [{ name: "resize", version: "1", operators: ["resize_image@1"] }],
  backends: [],
  model_services: [],
  templates: [],
};
const installedCatalog = {
  ...initial,
  operators: {
    ...initial.operators,
    [key]: {
      name,
      version: "1",
      inputs: capability.inputs,
      outputs: capability.outputs,
    },
  },
  backends: [
    {
      backend,
      adapter: "generic_remote_capability@1",
      name: "generic_remote_capability",
      version: "1",
      operators: [key],
      parameter_schema: capability.parameter_schema,
      defaults: capability.defaults,
    },
  ],
  model_services: [
    {
      backend,
      endpoint: "http://cpu:8780",
      descriptor_digest: "sha256:descriptor",
      display_name: descriptor.display_name,
      operator: name,
      capabilities: [capability],
    },
  ],
};
for (const action of ["click", "drag"] as const) {
  test(`dynamic capability can ${action}, expose ports and connect to compatible downstream`, async ({
    page,
  }) => {
    let installed = false;
    const compiles: any[] = [],
      installs: any[] = [];
    await page.route("**/api/**", async (route) => {
      const path = new URL(route.request().url()).pathname;
      let body: unknown = {};
      if (path === "/api/catalog")
        body = installed ? installedCatalog : initial;
      else if (path === "/api/drafts") body = { drafts: [] };
      else if (path === "/api/model-services/detect")
        body = {
          endpoint: "http://cpu:8780",
          descriptor_digest: "sha256:descriptor",
          descriptor,
          capability_availability: { text_to_image_v1: { installable: true } },
        };
      else if (path === "/api/model-services") {
        installs.push(route.request().postDataJSON());
        installed = true;
        body = {
          display_name: descriptor.display_name,
          catalog: installedCatalog,
        };
      } else if (path === "/api/compile") {
        compiles.push(route.request().postDataJSON().pipeline);
        body = { ok: true };
      }
      await route.fulfill({ json: body });
    });
    await page.goto("/");
    await page
      .getByRole("button", { name: "展开节点目录", exact: true })
      .click();
    await page
      .getByRole("button", { name: "＋ 添加模型服务", exact: true })
      .click();
    await page.getByLabel("模型服务地址").fill("http://cpu:8780");
    await page.getByRole("button", { name: "检测服务", exact: true }).click();
    const detected = page.getByRole("region", { name: "检测到的模型" });
    await expect(detected).toContainText("prompt (text)");
    await expect(detected).toContainText("image (rgb_image)");
    await expect(
      detected.getByRole("button", { name: "确认添加模型", exact: true }),
    ).toBeEnabled();
    await detected
      .getByRole("button", { name: "确认添加模型", exact: true })
      .click();
    expect(installs).toEqual([
      {
        endpoint: "http://cpu:8780",
        descriptor_digest: "sha256:descriptor",
        capability_id: "text_to_image_v1",
      },
    ]);
    await page.getByRole("button", { name: "＋ 添加节点", exact: true }).click();
    await page.getByRole("button", { name: /文字输入.*文本提示/ }).click();
    await expect.poll(() => compiles.at(-1)?.inputs?.text?.carriers).toEqual(["artifact_ref"]);
    await expect.poll(() => compiles.at(-1)?.inputs?.text?.schema_name).toBe("plain_text");
    // Reload verifies creation works from the persisted catalog, without detection state.
    await page.reload();
    await page
      .getByRole("button", { name: "展开节点目录", exact: true })
      .click();
    const service = page.getByRole("button", { name: /CPU 文生图.*模型服务/ });
    await expect(service).toBeEnabled();
    if (action === "click") await service.click();
    else {
      const transfer = await page.evaluateHandle(() => new DataTransfer());
      await service.dispatchEvent("dragstart", { dataTransfer: transfer });
      await page.locator(".react-flow").dispatchEvent("drop", {
        dataTransfer: transfer,
        clientX: 620,
        clientY: 320,
      });
    }
    const node = page.locator(`.react-flow__node[data-id="${name}"]`);
    await expect(node).toBeVisible();
    await expect(
      node.locator('.react-flow__handle.target[data-handleid="prompt"]'),
    ).toBeVisible();
    await expect(
      node.locator('.react-flow__handle.source[data-handleid="image"]'),
    ).toBeVisible();
    await expect
      .poll(() => compiles.at(-1)?.nodes[name]?.backend)
      .toBe(backend);
    expect(compiles.at(-1).nodes[name].operator).toBe(key);
    await page
      .locator(".catalog-item")
      .filter({ has: page.getByText("resize_image", { exact: true }) })
      .click();
    await page
      .getByRole("button", { name: "收起节点目录", exact: true })
      .click();
    const hide = page.getByRole("button", {
      name: "收起属性面板",
      exact: true,
    });
    if (await hide.count()) await hide.click();
    await page.getByRole("button", { name: "查看全图", exact: true }).click();
    await page.waitForTimeout(400);
    await node
      .locator('.react-flow__handle.source[data-handleid="image"]')
      .dragTo(
        page.locator(
          '.react-flow__node[data-id="resize_image"] .react-flow__handle.target[data-handleid="image"]',
        ),
      );
    await expect
      .poll(() => compiles.at(-1)?.nodes.resize_image.inputs.image)
      .toBe(`${name}.outputs.image`);
    await expect(page.locator(".react-flow__edge")).toHaveCount(1);
  });
}
