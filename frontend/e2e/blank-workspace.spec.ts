import { test, expect } from "@playwright/test";

test("blank workspace connects uploads and starts from the canvas without opening execution details", async ({
  page,
}) => {
  const input = {
    kind: "rgb_image",
    carriers: ["artifact_ref"],
    schema_name: "raster_image",
    schema_version: "1.0",
  };
  const starts: any[] = [];
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    let body: any = { runs: [] };
    if (path === "/api/catalog")
      body = {
        execution_enabled: true,
        templates: [],
        operators: {
          "encode_png@1": {
            name: "encode_png",
            version: "1",
            inputs: { image: input },
            outputs: { image: { ...input, schema_name: "png" } },
          },
        },
        adapters: [
          { name: "encode_png", version: "1", operators: ["encode_png@1"] },
        ],
      };
    else if (path === "/api/drafts") body = { drafts: [] };
    else if (path === "/api/compile")
      body = { ok: true, execution_ready: true };
    else if (path === "/api/inputs/image")
      body = { image_ref: { artifact_id: "uploaded-image" } };
    else if (path === "/api/prepare-inputs")
      body = {
        input_refs: { image: route.request().postDataJSON().image_ref },
      };
    else if (path === "/api/preflight")
      body = {
        digest: "checked",
        execution_ready: true,
        nodes: {
          encode_png: { status: "execute", reason: "new", detail: "new" },
        },
      };
    else if (path === "/api/runs" && route.request().method() === "POST") {
      starts.push(route.request().postDataJSON());
      body = {
        run: {
          run_id: "created",
          status: "succeeded",
          dag: {
            revision: 1,
            node_states: { encode_png: { status: "succeeded", attempts: [] } },
          },
        },
        outputs: [],
      };
    } else if (path === "/api/runs/created")
      body = {
        run: {
          run_id: "created",
          status: "succeeded",
          dag: { revision: 1, node_states: {} },
        },
        outputs: [],
      };
    await route.fulfill({ json: body });
  });
  page.on("dialog", (d) => d.accept());
  await page.goto("/");
  await page.getByRole("button", { name: "新建空白", exact: true }).click();
  if (await page.getByRole("button", { name: "继续替换", exact: true }).count())
    await page.getByRole("button", { name: "继续替换", exact: true }).click();
  await expect(page.locator(".react-flow__node")).toHaveCount(0);
  await expect(page.locator("#workspace-catalog")).toBeHidden();
  await page.getByRole("button", { name: "＋ 添加节点" }).click();
  await page
    .getByRole("dialog", { name: "添加节点" })
    .getByRole("button", { name: /图片输入 RGB/ })
    .click();
  await page.getByRole("button", { name: "＋ 添加节点" }).click();
  await page
    .getByRole("dialog", { name: "添加节点" })
    .getByRole("button", { name: /编码图片 encode_png/ })
    .click();
  await expect(page.locator(".react-flow__node")).toHaveCount(2);
  await expect(page.locator('[data-id="encode_png"] header')).toContainText(
    "编码图片",
  );
  await expect(
    page.getByLabel("输入状态 encode_png", { exact: true }),
  ).toContainText("输入 0/1 已连接");
  await page.waitForTimeout(400);
  await page
    .locator('[data-id="input:image"] .react-flow__handle.source')
    .dragTo(page.locator('[data-id="encode_png"] .react-flow__handle.target'));
  await expect(page.locator(".react-flow__edge")).toHaveCount(1);
  await expect(
    page.getByLabel("输入状态 encode_png", { exact: true }),
  ).toHaveText("输入 1/1 已连接");
  await expect(
    page.getByLabel("encode_png 输入 image 来源", { exact: true }),
  ).toHaveText("← 输入 image");
  await page
    .locator('[data-id="encode_png"] .blueprint-node-header')
    .dblclick();
  await expect
    .poll(
      async () =>
        (await page.locator('[data-id="encode_png"]').boundingBox())!.width,
    )
    .toBeGreaterThanOrEqual(250);
  await expect(
    page.getByRole("button", { name: "聚焦所选节点", exact: true }),
  ).toBeEnabled();
  await page.getByRole("button", { name: "查看全图", exact: true }).click();

  await expect(page.getByLabel("缺少运行输入", { exact: true })).toContainText(
    "image",
  );
  await page
    .getByRole("button", { name: "定位输入 · image", exact: true })
    .click();
  await expect(page.locator('[data-id="input:image"]')).toBeInViewport();
  await expect(page.getByLabel("上传运行图片", { exact: true })).toBeFocused();
  await page.getByRole("button", { name: "展开节点预览", exact: true }).click();
  await page.getByLabel("上传运行图片", { exact: true }).setInputFiles({
    name: "input.png",
    mimeType: "image/png",
    buffer: Buffer.from("fixture"),
  });
  await page.locator('[data-id="input:image"] .blueprint-node-header').click();
  await expect(
    page.getByRole("region", { name: "当前输入预览" }),
  ).toContainText("当前草稿输入 · image");
  await page
    .getByRole("button", { name: "启动新运行 · 检查执行范围", exact: true })
    .click();
  await expect(page.getByLabel("缺少运行输入", { exact: true })).toHaveCount(0);
  await expect.poll(() => starts.length).toBe(1);
  expect(starts[0].pipeline.inputs.image).toEqual(input);
  expect(starts[0].pipeline.nodes.encode_png.inputs).toEqual({
    image: "pipeline.inputs.image",
  });
  expect(starts[0].input_refs).toEqual({
    image: { artifact_id: "uploaded-image" },
  });
  expect(starts[0].preflight_digest).toBe("checked");
  await expect(page.locator("#workspace-catalog")).toBeHidden();
  await page
    .getByRole("button", { name: "清除输入 · image", exact: true })
    .click();
  await expect(
    page.getByRole("region", { name: "当前输入预览" }),
  ).not.toContainText("uploaded-image");
  await expect(
    page.getByRole("button", {
      name: "启动新运行 · 检查执行范围",
      exact: true,
    }),
  ).toBeDisabled();
  expect(starts).toHaveLength(1);
});

test("loading a multi-input template keeps the first input readable and offers an overview", async ({
  page,
}) => {
  const image = { kind: "rgb_image", carriers: ["artifact_ref"] };
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
                  inputs: { image },
                  outputs: { image },
                },
                "resize_image@1": {
                  name: "resize_image",
                  version: "1",
                  inputs: { image },
                  outputs: { image },
                },
              },
              adapters: [],
              templates: [
                {
                  id: "long",
                  label: "长链模板",
                  pipeline: {
                    pipeline: "long",
                    version: "1",
                    inputs: { image, second_image: image, third_image: image },
                    nodes: {
                      encode: {
                        operator: "encode_png@1",
                        inputs: { image: "pipeline.inputs.image" },
                      },
                      resize: {
                        operator: "resize_image@1",
                        inputs: { image: "encode.outputs.image" },
                      },
                      far: {
                        operator: "resize_image@1",
                        inputs: { image: "resize.outputs.image" },
                      },
                    },
                  },
                },
              ],
            }
          : path === "/api/drafts"
            ? { drafts: [] }
            : { runs: [] },
    });
  });
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/");
  await page.getByRole("button", { name: "展开节点目录", exact: true }).click();
  await page.getByRole("button", { name: "长链模板", exact: true }).click();
  await page.getByRole("button", { name: "继续替换", exact: true }).click();
  await expect(page.locator('[data-id="input:image"]')).toBeInViewport();
  await expect(
    page.getByLabel("上传输入 image", { exact: true }),
  ).toBeInViewport();
  await expect
    .poll(
      async () =>
        (await page.locator('[data-id="input:image"]').boundingBox())!.width,
    )
    .toBeGreaterThanOrEqual(288);
  await page.getByRole("button", { name: "查看全图", exact: true }).click();
  await expect(page.locator('[data-id="far"]')).toBeInViewport();
  await expect(page.locator('[data-id="input:third_image"]')).toBeInViewport();
  await page.getByRole("button", { name: "聚焦所选节点", exact: true }).click();
  await expect
    .poll(
      async () =>
        (await page.locator('[data-id="input:image"]').boundingBox())!.width,
    )
    .toBeGreaterThanOrEqual(254);
});
