import { createServer } from "node:http";
import type { AddressInfo } from "node:net";
import { test, expect } from "@playwright/test";
test("uploaded images bind exact references only after explicit run creation", async ({
  page,
}) => {
  const imageRef = { artifact_id: "artifact_uploaded_exact" };
  const starts: any[] = [];
  let uploads = 0;
  let rejectUpload = false;
  const bytes = Buffer.from("encoded image fixture");
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    let body: unknown = {};
    if (path === "/api/catalog")
      body = {
        operators: {},
        adapters: [],
        templates: [],
        execution_enabled: true,
      };
    else if (path === "/api/drafts") body = { drafts: [] };
    else if (path === "/api/inputs/image") {
      uploads++;
      expect(route.request().headers()["content-type"]).toBe(
        "application/octet-stream",
      );
      expect(route.request().postDataBuffer()).toEqual(bytes);
      if (rejectUpload) {
        await route.fulfill({
          status: 400,
          json: { error: "invalid image encoding" },
        });
        return;
      }
      body = { image_ref: imageRef };
    } else if (path === "/api/runs" && route.request().method() === "POST") {
      starts.push(route.request().postDataJSON());
      body = {
        run: {
          run_id: "dag_uploaded",
          status: "succeeded",
          dag: { revision: 1, node_states: {} },
        },
      };
    } else if (path === "/api/runs") body = { runs: [] };
    else
      body = {
        run: {
          run_id: "dag_uploaded",
          status: "succeeded",
          dag: { revision: 1, node_states: {} },
        },
      };
    await route.fulfill({ json: body });
  });
  await page.goto("/");
  await page.getByRole("button", { name: "运行", exact: true }).click();
  await page.getByLabel("运行图片路径").fill("/data/previous.png");
  await page.getByLabel("图片来源", { exact: true }).selectOption("upload");
  const start = page.getByRole("button", { name: "启动新运行", exact: true });
  await expect(start).toBeDisabled();
  const file = { name: "robot.png", mimeType: "image/png", buffer: bytes };
  await page.getByLabel("上传运行图片").setInputFiles(file);
  await expect(
    page.getByText("图片已上传；点击启动新运行才会执行模型。"),
  ).toBeVisible();
  expect(uploads).toBe(1);
  expect(starts).toEqual([]);
  await start.click();
  await expect(
    page.getByText("运行已创建；请在下方查看真实节点状态。"),
  ).toBeVisible();
  expect(starts[0].image_ref).toEqual(imageRef);
  expect(starts[0]).not.toHaveProperty("image_path");
  rejectUpload = true;
  await page.getByLabel("上传运行图片").setInputFiles(file);
  await expect(
    page.getByText(/上传失败：.*invalid image encoding/),
  ).toBeVisible();
  await expect(start).toBeDisabled();
  expect(starts).toHaveLength(1);
  await page.getByLabel("图片来源", { exact: true }).selectOption("path");
  await start.click();
  await expect.poll(() => starts.length).toBe(2);
  expect(starts[1].image_path).toBe("/data/previous.png");
  expect(starts[1]).not.toHaveProperty("image_ref");
});

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

test("execution uses frozen server runs and explicit revisioned actions", async ({
  page,
}) => {
  const calls: { path: string; body: any }[] = [];
  let status = "waiting_for_input";
  let reviewUrl = "";
  const run = () => ({
    run_id: "dag_test",
    status,
    dag: {
      revision: 7,
      node_states: {
        choose: {
          status,
          attempts: [
            {
              attempt: 1,
              status,
              error_code: status === "failed" ? "backend_failed" : null,
              error_detail: "specific failure",
            },
          ],
        },
      },
    },
  });
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (route.request().method() === "POST")
      calls.push({ path, body: route.request().postDataJSON() });
    let body: any = {};
    if (path === "/api/catalog")
      body = {
        operators: {},
        adapters: [],
        templates: [],
        execution_enabled: true,
        execution_profile: "trellis-local",
      };
    else if (path === "/api/drafts") body = { drafts: [] };
    else if (path === "/api/runs" && route.request().method() === "GET")
      body = { runs: [{ run_id: "dag_test", status }] };
    else if (path.endsWith("/review")) body = { url: reviewUrl };
    else
      body = {
        run: run(),
        busy: false,
        outputs: [{ node_id: "generate", port: "glb", url: "/output.glb" }],
      };
    await route.fulfill({ json: body });
  });
  const reviewServer = createServer((_, response) => {
    response.writeHead(200, { "Content-Type": "text/html; charset=utf-8" });
    response.end("<p>真实审查组件边界</p><button>确认区域并生成</button>");
  });
  await new Promise<void>((resolve) =>
    reviewServer.listen(0, "127.0.0.1", resolve),
  );
  reviewUrl = `http://127.0.0.1:${(reviewServer.address() as AddressInfo).port}/`;
  reviewServer.unref();
  page.on("close", () => reviewServer.close());
  await page.goto("/");
  await page.getByRole("button", { name: "运行", exact: true }).click();
  await expect(page.getByText("已配置模型：trellis-local")).toBeVisible();
  await page.getByLabel("运行图片路径").fill("/data/robot.png");
  await page.getByRole("button", { name: "启动新运行", exact: true }).click();
  expect(calls[0]).toMatchObject({
    path: "/api/runs",
    body: {
      image_path: "/data/robot.png",
      pipeline: { pipeline: "my_asset_pipeline" },
    },
  });
  await page
    .getByRole("button", { name: "准备人工审查 · choose", exact: true })
    .click();
  await expect(
    page.getByRole("link", { name: "打开 mask 审查页面 ↗" }),
  ).toHaveAttribute("href", reviewUrl);
  const beforeReviewCalls = calls.length;
  await page.getByRole("button", { name: "在工作台审查 mask" }).click();
  await expect(
    page
      .frameLocator('iframe[title="mask 审查 choose"]')
      .getByText("真实审查组件边界"),
  ).toBeVisible();
  await page.getByRole("button", { name: "收起审查" }).click();
  await expect(page.getByRole("dialog", { name: "mask 审查" })).toHaveCount(0);
  expect(calls).toHaveLength(beforeReviewCalls);
  await page.getByLabel("管线名称").fill("changed_draft");
  await expect(
    page.getByText("草稿已修改，与此运行的计划不同。"),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "恢复 / 继续此运行", exact: true })
    .click();
  expect(calls.find((c) => c.path.endsWith("/resume"))?.body).toEqual({
    expected_revision: 7,
  });
  status = "failed";
  await expect(
    page.getByRole("button", { name: "显式重试 · choose", exact: true }),
  ).toBeVisible();
  await page.getByText("执行记录 · 1 次").click();
  await expect(page.getByText(/backend_failed specific failure/)).toBeVisible();
  await page
    .getByRole("button", { name: "显式重试 · choose", exact: true })
    .click();
  expect(calls.find((c) => c.path.endsWith("/retry"))?.body).toEqual({
    expected_revision: 7,
    node_id: "choose",
  });
  await expect(
    page.getByRole("link", { name: "generate · glb ↗" }),
  ).toHaveAttribute("href", "/output.glb");
});

test("lost create response is never automatically resubmitted", async ({
  page,
}) => {
  let creates = 0;
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/api/runs" && route.request().method() === "POST") {
      creates++;
      await route.abort("failed");
      return;
    }
    await route.fulfill({
      json:
        path === "/api/catalog"
          ? {
              operators: {},
              adapters: [],
              templates: [],
              execution_enabled: true,
            }
          : path === "/api/drafts"
            ? { drafts: [] }
            : { runs: [] },
    });
  });
  await page.goto("/");
  await page.getByRole("button", { name: "运行", exact: true }).click();
  await page.getByLabel("运行图片路径").fill("/data/robot.png");
  await page.getByRole("button", { name: "启动新运行", exact: true }).click();
  await expect(page.getByText(/未自动重发/)).toBeVisible();
  await page.waitForTimeout(1800);
  expect(creates).toBe(1);
});

test("compiled graph can be ineligible for the current execution endpoint", async ({
  page,
}) => {
  const reason =
    "canvas execution currently requires exactly one input named image";
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    const body =
      path === "/api/catalog"
        ? {
            operators: {},
            adapters: [],
            templates: [],
            execution_enabled: true,
          }
        : path === "/api/compile"
          ? { ok: true, execution_ready: false, execution_reason: reason }
          : path === "/api/runs"
            ? { runs: [] }
            : { drafts: [] };
    await route.fulfill({ json: body });
  });
  await page.goto("/");
  await page.getByRole("button", { name: "编译校验", exact: true }).click();
  await expect(page.getByRole("status")).toContainText(reason);
  await page.getByRole("button", { name: "运行", exact: true }).click();
  await page.getByLabel("运行图片路径").fill("/tmp/photo.png");
  await expect(page.getByRole("alert")).toContainText(reason);
  await expect(
    page.getByRole("button", { name: "启动新运行", exact: true }),
  ).toBeDisabled();
});

test("parameter form sends typed values and preserves other instances", async ({
  page,
}) => {
  let submitted: any;
  const node = {
    operator: "generate@1",
    adapter: "local@1",
    inputs: {},
    parameters: {},
  };
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/api/compile")
      submitted = route.request().postDataJSON().pipeline;
    await route.fulfill({
      json:
        path === "/api/catalog"
          ? {
              operators: {
                "generate@1": {
                  name: "generate",
                  version: "1",
                  inputs: {},
                  outputs: {},
                },
              },
              adapters: [
                {
                  name: "local",
                  version: "1",
                  operators: ["generate@1"],
                  defaults: { seed: 42, size: "512", enabled: true },
                  parameter_schema: {
                    type: "object",
                    properties: {
                      seed: { type: "integer", minimum: 0 },
                      size: { type: "string", enum: ["512", "1024"] },
                      enabled: { type: "boolean" },
                      matrix: { type: "array", items: { type: "number" } },
                      options: { type: "object", properties: {} },
                    },
                  },
                },
              ],
              templates: [
                {
                  id: "forms",
                  label: "forms",
                  pipeline: {
                    pipeline: "forms",
                    version: "1",
                    inputs: {},
                    nodes: {
                      first: node,
                      second: { ...node, parameters: { seed: 7 } },
                    },
                  },
                },
              ],
            }
          : path === "/api/compile"
            ? { ok: true }
            : { drafts: [] },
    });
  });
  await page.goto("/");
  page.on("dialog", (d) => d.accept());
  await page.getByRole("button", { name: "forms", exact: true }).click();
  await page.locator('.react-flow__node[data-id="first"]').click();
  await expect(page.getByLabel("参数 seed", { exact: true })).toHaveValue("42");
  await page.getByLabel("参数 seed", { exact: true }).fill("1.5");
  await page.getByLabel("参数 seed", { exact: true }).press("Tab");
  await expect(page.getByRole("alert")).toContainText("尚未应用");
  await page
    .getByRole("button", { name: "放弃字段编辑 · seed", exact: true })
    .click();
  await expect(page.getByLabel("参数 seed", { exact: true })).toHaveValue("42");
  await expect(page.getByRole("alert")).toHaveCount(0);
  await page.getByLabel("参数 matrix JSON", { exact: true }).fill("{}");
  await page
    .getByRole("button", { name: "应用字段 · matrix", exact: true })
    .click();
  await page.getByLabel("参数 seed", { exact: true }).fill("1.5");
  await page.getByLabel("参数 seed", { exact: true }).press("Tab");
  await expect(page.getByRole("alert")).toHaveCount(2);
  await page
    .getByRole("button", { name: "放弃字段编辑 · seed", exact: true })
    .click();
  await expect(
    page.getByRole("alert", { name: "参数错误 · matrix", exact: true }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "放弃字段编辑 · matrix", exact: true })
    .click();
  await expect(page.getByRole("alert")).toHaveCount(0);
  await page.getByLabel("参数 seed", { exact: true }).fill("99");
  await page.getByLabel("参数 seed", { exact: true }).press("Tab");
  await page.getByLabel("参数 size", { exact: true }).selectOption("1");
  await page
    .getByLabel("参数 options JSON", { exact: true })
    .fill('{"label":"pending"}');
  await page.getByLabel("参数 enabled", { exact: true }).selectOption("false");
  await expect(
    page.getByLabel("参数 options JSON", { exact: true }),
  ).toHaveValue('{"label":"pending"}');
  await page.getByLabel("参数 matrix JSON", { exact: true }).fill("{}");
  await page
    .getByRole("button", { name: "应用字段 · matrix", exact: true })
    .click();
  await expect(page.getByRole("alert")).toContainText("尚未应用");
  await page
    .getByLabel("参数 matrix JSON", { exact: true })
    .fill("[1, 0, 0, 1]");
  await page
    .getByRole("button", { name: "应用字段 · matrix", exact: true })
    .click();
  await expect(
    page.getByLabel("参数 options JSON", { exact: true }),
  ).toHaveValue('{"label":"pending"}');
  await expect(
    page.getByText("options 尚未应用；保存、编译和运行使用已应用值。", {
      exact: false,
    }),
  ).toBeVisible();
  await page.getByLabel("参数 matrix JSON", { exact: true }).fill("[9]");
  await page
    .getByRole("button", { name: "放弃字段编辑 · matrix", exact: true })
    .click();
  await expect(
    page.getByLabel("参数 matrix JSON", { exact: true }),
  ).toHaveValue(JSON.stringify([1, 0, 0, 1], null, 2));
  await expect(
    page.getByLabel("参数 options JSON", { exact: true }),
  ).toHaveValue('{"label":"pending"}');
  // Applying an equivalent value must settle this field without dropping others.
  await page.getByLabel("参数 matrix JSON", { exact: true }).fill("[1,0,0,1]");
  await page
    .getByRole("button", { name: "应用字段 · matrix", exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: "应用字段 · matrix", exact: true }),
  ).toBeDisabled();
  await expect(
    page.getByLabel("参数 options JSON", { exact: true }),
  ).toHaveValue('{"label":"pending"}');
  await page.getByLabel("参数 seed", { exact: true }).fill("099");
  await page.getByLabel("参数 seed", { exact: true }).press("Tab");
  await expect(page.getByLabel("参数 seed", { exact: true })).toHaveValue("99");
  await page.getByLabel("参数 options JSON", { exact: true }).fill("null");
  await page
    .getByRole("button", { name: "应用字段 · options", exact: true })
    .click();
  await expect(page.getByRole("alert")).toContainText("尚未应用");
  await page
    .getByLabel("参数 options JSON", { exact: true })
    .fill('{"label":"example"}');
  await page
    .getByRole("button", { name: "应用字段 · options", exact: true })
    .click();
  await page.getByRole("button", { name: "编译校验", exact: true }).click();
  await expect.poll(() => submitted?.nodes.first.parameters.seed).toBe(99);
  expect(submitted.nodes.first.parameters).toEqual({
    seed: 99,
    size: "1024",
    enabled: false,
    matrix: [1, 0, 0, 1],
    options: { label: "example" },
  });
  expect(submitted.nodes.second.parameters).toEqual({ seed: 7 });
  await page.getByRole("button", { name: "配置", exact: true }).click();
  await page.locator('.react-flow__node[data-id="first"]').click();
  await page
    .getByRole("button", { name: "移除覆盖 · matrix", exact: true })
    .click();
  await page.getByRole("button", { name: "编译校验", exact: true }).click();
  await expect
    .poll(() => submitted.nodes.first.parameters.matrix)
    .toBeUndefined();
  expect(submitted.nodes.first.parameters.options).toEqual({
    label: "example",
  });
  await page.getByRole("button", { name: "配置", exact: true }).click();
  await page.getByRole("button", { name: "复制节点配置", exact: true }).click();
  await expect(
    page.locator('.react-flow__node[data-id="first_copy"]'),
  ).toBeVisible();
  await expect(page.getByLabel("参数 seed", { exact: true })).toHaveValue("99");
  await page.getByLabel("参数 seed", { exact: true }).fill("123");
  await page.getByLabel("参数 seed", { exact: true }).press("Tab");
  await page.getByRole("button", { name: "编译校验", exact: true }).click();
  await expect
    .poll(() => submitted.nodes.first_copy?.parameters.seed)
    .toBe(123);
  expect(submitted.nodes.first.parameters.seed).toBe(99);
  expect(submitted.nodes.first_copy.parameters.options).toEqual({
    label: "example",
  });
});

test("fixed run graph displays persisted plan instead of edited draft", async ({
  page,
}) => {
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    const plan = {
      plan_id: "fixed-id",
      bindings: {
        original: {
          adapter: "saved_adapter@2",
          backend: "saved_backend",
          parameters: { seed: 123 },
          implementation_digest: "saved-code",
          spec_digest: "saved-spec",
        },
      },
      static_plan: {
        pipeline_name: "fixed",
        inputs: {},
        nodes: [{ node_id: "original", operator: "copy@1", inputs: {} }],
        topological_order: ["original"],
        dependencies: { original: [] },
      },
    };
    await route.fulfill({
      json:
        path === "/api/catalog"
          ? {
              operators: {
                "copy@1": {
                  name: "copy",
                  version: "1",
                  inputs: {},
                  outputs: {},
                },
              },
              adapters: [],
              templates: [],
              execution_enabled: true,
            }
          : path === "/api/runs"
            ? { runs: [{ run_id: "dag_saved", status: "succeeded" }] }
            : path.endsWith("/plan")
              ? plan
              : path === "/api/runs/dag_saved"
                ? {
                    run: {
                      run_id: "dag_saved",
                      status: "succeeded",
                      dag: {
                        revision: 2,
                        plan_id: "fixed-id",
                        node_states: { original: { status: "succeeded" } },
                      },
                    },
                  }
                : { drafts: [] },
    });
  });
  await page.goto("/");
  await page.locator(".catalog-item").click();
  const draftCanvas = page.locator("main.canvas");
  const draftNode = draftCanvas.locator('.react-flow__node[data-id="copy"]');
  await expect(draftNode).toBeVisible();
  await expect(draftCanvas.locator(".react-flow__viewport")).toHaveAttribute(
    "style",
    /scale\(1\)/,
  );
  const viewport = await draftCanvas
    .locator(".react-flow__viewport")
    .getAttribute("style");
  await page.getByLabel("管线名称").fill("changed_draft");
  await page.getByRole("button", { name: "运行", exact: true }).click();
  await page.getByLabel("选择运行").selectOption("dag_saved");
  await page.getByRole("button", { name: "查看固定运行图" }).click();
  const dialog = page.getByRole("dialog", { name: "固定运行图" });
  await expect(
    dialog.locator('.react-flow__node[data-id="original"]'),
  ).toContainText("succeeded");
  await dialog.locator('.react-flow__node[data-id="original"]').click();
  const binding = dialog.locator(".run-binding-details pre");
  await expect(binding).toBeVisible();
  expect(JSON.parse(await binding.innerText())).toMatchObject({
    operator: "copy@1",
    adapter: "saved_adapter@2",
    backend: "saved_backend",
    parameters: { seed: 123 },
    implementation_digest: "saved-code",
  });
  await dialog.locator(".react-flow__controls-zoomout").click();
  await expect(draftNode).toBeAttached();
  await expect(
    draftCanvas.locator('.react-flow__node[data-id="original"]'),
  ).toHaveCount(0);
  await expect(draftCanvas.locator(".react-flow__viewport")).toHaveAttribute(
    "style",
    viewport!,
  );
  await page.getByRole("button", { name: "关闭运行图" }).click();
  await expect(draftNode).toBeVisible();
  await expect(page.getByLabel("管线名称")).toHaveValue("changed_draft");
});

for (const implementation of ["local", "remote"]) {
  test(`node backend selection uses ${implementation} schema and clears stale identity`, async ({
    page,
  }) => {
    let saved: any;
    const adapter = {
      name: "local",
      version: "1",
      operators: ["generate@1"],
      defaults: { profile_digest: "old", service_id: "old-service" },
      parameter_schema: {
        type: "object",
        properties: {
          profile_digest: { type: "string", enum: ["old"] },
          service_id: { type: "string", enum: ["old-service"] },
        },
      },
    };
    await page.route("**/api/**", async (route) => {
      const path = new URL(route.request().url()).pathname;
      if (path === "/api/compile")
        saved = route.request().postDataJSON().pipeline;
      await route.fulfill({
        json:
          path === "/api/catalog"
            ? {
                operators: {
                  "generate@1": {
                    name: "generate",
                    version: "1",
                    inputs: {},
                    outputs: {},
                  },
                },
                adapters: [adapter],
                backends: [
                  {
                    ...adapter,
                    backend: "other",
                    name: implementation,
                    adapter: `${implementation}@1`,
                    defaults: {
                      profile_digest: "new",
                      service_id: "new-service",
                    },
                    parameter_schema: {
                      type: "object",
                      properties: {
                        profile_digest: { type: "string", enum: ["new"] },
                        service_id: { type: "string", enum: ["new-service"] },
                      },
                    },
                  },
                ],
                templates: [
                  {
                    id: "pair",
                    label: "pair",
                    pipeline: {
                      pipeline: "pair",
                      version: "1",
                      inputs: {},
                      nodes: {
                        left: {
                          operator: "generate@1",
                          adapter: "local@1",
                          inputs: {},
                          parameters: {
                            profile_digest: "stale-deployment",
                            service_id: "old-service",
                            seed: 42,
                          },
                        },
                        right: {
                          operator: "generate@1",
                          adapter: "local@1",
                          inputs: {},
                          parameters: { profile_digest: "old" },
                        },
                      },
                    },
                  },
                ],
              }
            : path === "/api/compile"
              ? { ok: true }
              : { drafts: [] },
      });
    });
    await page.goto("/");
    page.on("dialog", (d) => d.accept());
    await page.getByRole("button", { name: "pair", exact: true }).click();
    await page.locator('.react-flow__node[data-id="left"]').click();
    await expect(page.getByRole("alert")).toContainText("stale-deployment");
    await expect(page.getByRole("alert")).toContainText('当前要求："old"');
    await page
      .getByRole("button", {
        name: "使用当前部署值 · profile_digest",
        exact: true,
      })
      .click();
    await expect(page.getByRole("alert")).toHaveCount(0);
    await page.getByRole("button", { name: "编译校验", exact: true }).click();
    await expect
      .poll(() => saved?.nodes.left.parameters.profile_digest)
      .toBe("old");
    await page.getByRole("button", { name: "配置", exact: true }).click();
    expect(saved.nodes.right.parameters).toEqual({ profile_digest: "old" });
    await page.locator('.react-flow__node[data-id="left"]').click();
    await page
      .getByLabel("节点 Backend", { exact: true })
      .selectOption("other");
    await expect(
      page.getByLabel("参数 profile_digest", { exact: true }),
    ).toContainText("new");
    await page.getByRole("button", { name: "编译校验", exact: true }).click();
    await expect.poll(() => saved?.nodes.left.backend).toBe("other");
    expect(saved.nodes.left.adapter).toBe(`${implementation}@1`);
    expect(saved.nodes.left.parameters).toEqual({ seed: 42 });
    expect(saved.nodes.right.parameters).toEqual({ profile_digest: "old" });
    expect(saved.nodes.right.backend).toBeUndefined();
    await page.getByRole("button", { name: "配置", exact: true }).click();
    await page.getByLabel("节点 Backend", { exact: true }).selectOption("");
    await expect(page.getByLabel("Adapter", { exact: true })).toHaveValue(
      "local@1",
    );
    await expect(
      page.getByLabel("参数 profile_digest", { exact: true }),
    ).toContainText("old");
    await page.getByRole("button", { name: "编译校验", exact: true }).click();
    await expect.poll(() => saved.nodes.left.backend).toBeUndefined();
    expect(saved.nodes.left.adapter).toBe("local@1");
    expect(saved.nodes.left.parameters).toEqual({ seed: 42 });
  });
}

test("lost creation response preserves exact intent through reload and explicit retry", async ({
  page,
}) => {
  const starts: any[] = [];
  let loseResponse = true;
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/api/runs" && route.request().method() === "POST") {
      starts.push(route.request().postDataJSON());
      if (loseResponse) {
        await route.abort("failed");
        return;
      }
    }
    await route.fulfill({
      json:
        path === "/api/catalog"
          ? {
              operators: {},
              adapters: [],
              templates: [],
              execution_enabled: true,
            }
          : path === "/api/drafts"
            ? { drafts: [] }
            : path === "/api/runs" && route.request().method() === "GET"
              ? { runs: [] }
              : {
                  run: {
                    run_id: "dag_receipt",
                    status: "succeeded",
                    dag: { revision: 1, node_states: {} },
                  },
                },
    });
  });
  await page.goto("/");
  await page.getByRole("button", { name: "运行", exact: true }).click();
  await page.getByLabel("运行图片路径").fill("/data/original.png");
  const start = page.getByRole("button", { name: "启动新运行", exact: true });
  await start.click();
  const retry = page.getByRole("button", {
    name: "重试原创建请求",
    exact: true,
  });
  await expect(retry).toBeEnabled();
  expect(starts).toHaveLength(1);
  expect(starts[0].idempotency_key).toMatch(/^[a-f0-9-]{36}$/);
  await expect(start).toBeDisabled();

  await page.reload();
  await page.getByRole("button", { name: "运行", exact: true }).click();
  await expect(retry).toBeEnabled();
  expect(starts).toHaveLength(1);
  await page.getByLabel("运行图片路径").fill("/data/changed.png");
  await page.getByLabel("管线名称").fill("changed_after_submission");
  loseResponse = false;
  await retry.click();
  await expect(
    page.getByText("运行已创建；请在下方查看真实节点状态。"),
  ).toBeVisible();
  expect(starts).toHaveLength(2);
  expect(starts[1]).toEqual(starts[0]);
  await expect(retry).toHaveCount(0);
  expect(
    await page.evaluate(() =>
      sessionStorage.getItem("assets-generator:pending-creation:v1"),
    ),
  ).toBeNull();

  await start.click();
  await expect.poll(() => starts.length).toBe(3);
  expect(starts[2].idempotency_key).not.toBe(starts[0].idempotency_key);
  expect(starts[2].image_path).toBe("/data/changed.png");
  expect(starts[2].pipeline.pipeline).toBe("changed_after_submission");

  // A failed next intent can only be replaced by explicit discard.
  await expect(start).toBeEnabled();
  loseResponse = true;
  await start.click();
  await expect(retry).toBeEnabled();
  expect(starts).toHaveLength(4);
  await page
    .getByRole("button", { name: "放弃待确认请求，允许新建", exact: true })
    .click();
  await expect(start).toBeEnabled();
  expect(starts).toHaveLength(4);
  loseResponse = false;
  await start.click();
  await expect.poll(() => starts.length).toBe(5);
  expect(starts[4].idempotency_key).not.toBe(starts[3].idempotency_key);
});

test("creation is not sent if browser cannot persist its receipt", async ({
  page,
}) => {
  let starts = 0;
  await page.addInitScript(() => {
    Storage.prototype.setItem = () => {
      throw new Error("storage disabled");
    };
  });
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/api/runs" && route.request().method() === "POST") starts++;
    await route.fulfill({
      json:
        path === "/api/catalog"
          ? {
              operators: {},
              adapters: [],
              templates: [],
              execution_enabled: true,
            }
          : path === "/api/drafts"
            ? { drafts: [] }
            : { runs: [] },
    });
  });
  await page.goto("/");
  await page.getByRole("button", { name: "运行", exact: true }).click();
  await page.getByLabel("运行图片路径").fill("/data/robot.png");
  await page.getByRole("button", { name: "启动新运行", exact: true }).click();
  await expect(page.getByText(/storage disabled/)).toBeVisible();
  expect(starts).toBe(0);
});

test("multi-view creation preserves observation reference across reload retry", async ({
  page,
}) => {
  page.on("dialog", (d) => d.accept());
  const requests: any[] = [];
  let fail = true;
  let uploads = 0;
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/api/inputs/image") {
      uploads++;
      return route.fulfill({
        json: { image_ref: { artifact_id: `image_${uploads}` } },
      });
    }
    if (path === "/api/inputs/observations") {
      expect(route.request().postDataJSON()).toEqual({
        images: [{ artifact_id: "image_1" }, { artifact_id: "image_2" }],
      });
      expect(requests).toHaveLength(0);
      return route.fulfill({
        json: { observations_ref: { artifact_id: "sha256:" + "a".repeat(64) } },
      });
    }
    if (path === "/api/runs" && route.request().method() === "POST") {
      requests.push(route.request().postDataJSON());
      if (fail) return route.abort("failed");
    }
    await route.fulfill({
      json:
        path === "/api/catalog"
          ? {
              operators: {},
              adapters: [],
              execution_enabled: true,
              templates: [
                {
                  id: "multiview",
                  label: "multiview",
                  pipeline: {
                    pipeline: "multiview",
                    version: "1",
                    inputs: { observations: { kind: "observation_bundle" } },
                    nodes: {},
                  },
                },
              ],
            }
          : path === "/api/drafts"
            ? { drafts: [] }
            : path === "/api/runs" && route.request().method() === "GET"
              ? { runs: [] }
              : {
                  run: {
                    run_id: "dag_multiview",
                    status: "succeeded",
                    dag: { revision: 1, node_states: {} },
                  },
                },
    });
  });
  await page.goto("/");
  await page.getByRole("button", { name: "multiview", exact: true }).click();
  await page.getByRole("button", { name: "运行", exact: true }).click();
  await page.getByLabel("上传多视图照片").setInputFiles([
    { name: "first.png", mimeType: "image/png", buffer: Buffer.from("first") },
    {
      name: "second.png",
      mimeType: "image/png",
      buffer: Buffer.from("second"),
    },
  ]);
  await expect(page.getByLabel("观测包 Artifact ID")).toHaveValue(
    "sha256:" + "a".repeat(64),
  );
  expect(uploads).toBe(2);
  expect(requests).toHaveLength(0);
  await expect(page.getByLabel("运行图片路径")).toHaveCount(0);
  await page.getByRole("button", { name: "启动新运行", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "重试原创建请求", exact: true }),
  ).toBeEnabled();
  expect(requests[0].observations_ref).toEqual({
    artifact_id: "sha256:" + "a".repeat(64),
  });
  expect(requests[0].image_ref).toBeUndefined();
  await page.reload();
  await page.getByRole("button", { name: "运行", exact: true }).click();
  fail = false;
  await page
    .getByRole("button", { name: "重试原创建请求", exact: true })
    .click();
  await expect(page.getByText("dag_multiview", { exact: true })).toBeVisible();
  expect(requests).toHaveLength(2);
  expect(requests[1]).toEqual(requests[0]);
});

test("published GLB preview loads geometry and closes without mutation", async ({
  page,
}) => {
  const positions = new Float32Array([0, 0, 0, 1, 0, 0, 0, 1, 0]);
  const uvs = new Float32Array([0, 0, 1, 0, 0, 1]);
  const png = Buffer.from(
    "iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAIAAAD91JpzAAAAFklEQVR4nGP8z8DAwMDAxMDAwMDAAAANHQEDasKb6QAAAABJRU5ErkJggg==",
    "base64",
  );
  const binary = Buffer.alloc(Math.ceil((60 + png.length) / 4) * 4);
  Buffer.from(positions.buffer).copy(binary);
  Buffer.from(uvs.buffer).copy(binary, 36);
  png.copy(binary, 60);
  const document = {
    asset: { version: "2.0" },
    scene: 0,
    scenes: [{ nodes: [0] }],
    nodes: [{ mesh: 0 }],
    meshes: [
      {
        primitives: [
          { attributes: { POSITION: 0, TEXCOORD_0: 1 }, material: 0, mode: 4 },
        ],
      },
    ],
    materials: [
      {
        doubleSided: true,
        pbrMetallicRoughness: {
          baseColorTexture: { index: 0 },
          metallicFactor: 0,
          roughnessFactor: 1,
        },
      },
    ],
    textures: [{ source: 0 }],
    images: [{ bufferView: 2, mimeType: "image/png" }],
    buffers: [{ byteLength: binary.length }],
    bufferViews: [
      { buffer: 0, byteOffset: 0, byteLength: 36 },
      { buffer: 0, byteOffset: 36, byteLength: 24 },
      { buffer: 0, byteOffset: 60, byteLength: png.length },
    ],
    accessors: [
      {
        bufferView: 0,
        componentType: 5126,
        count: 3,
        type: "VEC3",
        min: [0, 0, 0],
        max: [1, 1, 0],
      },
      { bufferView: 1, componentType: 5126, count: 3, type: "VEC2" },
    ],
  };
  const text = JSON.stringify(document);
  const json = Buffer.from(text.padEnd(Math.ceil(text.length / 4) * 4));
  const glb = Buffer.alloc(28 + json.length + binary.length);
  [0x46546c67, 2, glb.length, json.length, 0x4e4f534a].forEach((v, i) =>
    glb.writeUInt32LE(v, i * 4),
  );
  json.copy(glb, 20);
  glb.writeUInt32LE(binary.length, 20 + json.length);
  glb.writeUInt32LE(0x004e4942, 24 + json.length);
  binary.copy(glb, 28 + json.length);
  let mutations = 0;
  let failOutput = false;
  let delayOutput = false;
  let releaseOutput: (() => void) | undefined;
  let outputEntered = false;
  const output = "/api/runs/dag_preview/outputs/generate/glb";
  await page.route("**/api/**", async (route) => {
    if (route.request().method() !== "GET") mutations++;
    const path = new URL(route.request().url()).pathname;
    if (path === output && delayOutput) {
      outputEntered = true;
      await new Promise<void>((resolve) => {
        releaseOutput = resolve;
      });
    }
    if (path === output && failOutput)
      return route.fulfill({
        status: 400,
        json: { error: "invalid evidence" },
      });
    if (path === output)
      return route.fulfill({ body: glb, contentType: "model/gltf-binary" });
    await route.fulfill({
      json:
        path === "/api/catalog"
          ? {
              operators: {},
              adapters: [],
              templates: [],
              execution_enabled: true,
            }
          : path === "/api/drafts"
            ? { drafts: [] }
            : path === "/api/runs"
              ? { runs: [{ run_id: "dag_preview", status: "succeeded" }] }
              : {
                  run: {
                    run_id: "dag_preview",
                    status: "succeeded",
                    dag: { revision: 1, node_states: {} },
                  },
                  outputs: [{ node_id: "generate", port: "glb", url: output }],
                },
    });
  });
  await page.goto("/");
  await page.getByRole("button", { name: "运行", exact: true }).click();
  await page.getByLabel("选择运行").selectOption("dag_preview");
  await page
    .getByRole("button", { name: "预览模型 · generate", exact: true })
    .click();
  const dialog = page.getByRole("dialog", { name: "模型预览", exact: true });
  await expect(dialog.getByRole("status")).toContainText("模型已加载");
  await expect(dialog.locator("canvas")).toBeVisible();
  const redPixels = await dialog.locator("canvas").evaluate(async (canvas) => {
    await new Promise<void>((resolve) =>
      requestAnimationFrame(() => resolve()),
    );
    const source = canvas as HTMLCanvasElement;
    const copy = document.createElement("canvas");
    copy.width = source.width;
    copy.height = source.height;
    const context = copy.getContext("2d")!;
    context.drawImage(source, 0, 0);
    const data = context.getImageData(0, 0, copy.width, copy.height).data;
    let count = 0;
    for (let i = 0; i < data.length; i += 4)
      if (
        data[i] > 100 &&
        data[i] > data[i + 1] * 1.8 &&
        data[i] > data[i + 2] * 1.8
      )
        count++;
    return count;
  });
  expect(redPixels).toBeGreaterThan(1000);
  await dialog.getByRole("button", { name: "重置视角" }).click();
  await dialog.getByRole("button", { name: "关闭模型预览" }).click();
  await expect(dialog).toHaveCount(0);
  await page
    .getByRole("button", { name: "预览模型 · generate", exact: true })
    .click();
  await expect(dialog.getByRole("status")).toContainText("模型已加载");
  await page.getByLabel("选择运行").selectOption("");
  await expect(dialog).toHaveCount(0);
  await expect(page.locator("canvas")).toHaveCount(0);
  await page.getByLabel("选择运行").selectOption("dag_preview");
  failOutput = true;
  await page
    .getByRole("button", { name: "预览模型 · generate", exact: true })
    .click();
  await expect(dialog.getByRole("status")).toContainText("HTTP 400");
  await expect(dialog.locator("canvas")).toHaveCount(0);
  await dialog.getByRole("button", { name: "关闭模型预览" }).click();
  failOutput = false;
  delayOutput = true;
  await page
    .getByRole("button", { name: "预览模型 · generate", exact: true })
    .click();
  await expect.poll(() => outputEntered).toBe(true);
  await dialog.getByRole("button", { name: "关闭模型预览" }).click();
  releaseOutput!();
  await expect(dialog).toHaveCount(0);
  await expect(page.locator("canvas")).toHaveCount(0);
  delayOutput = false;
  await page.evaluate(() => {
    const original = HTMLCanvasElement.prototype.getContext;
    HTMLCanvasElement.prototype.getContext = function (
      kind: any,
      ...args: any[]
    ) {
      if (String(kind).includes("webgl")) return null;
      return original.call(this, kind, ...args);
    } as typeof original;
  });
  await page
    .getByRole("button", { name: "预览模型 · generate", exact: true })
    .click();
  await expect(dialog.getByRole("status")).toContainText("预览失败");
  await expect(dialog.locator("canvas")).toHaveCount(0);
  await expect(
    dialog.getByRole("link", { name: "下载原始 GLB" }),
  ).toBeVisible();
  expect(mutations).toBe(0);
});

test("RGBA canvas upload uses the explicit endpoint and preserves returned reference", async ({
  page,
}) => {
  const imageRef = { artifact_id: "prepared_rgba_exact" };
  const starts: any[] = [];
  const uploads: string[] = [];
  const pipeline = {
    pipeline: "remote_rgba",
    version: "1",
    inputs: { image: { kind: "rgba_image", carriers: ["artifact_ref"] } },
    nodes: {},
  };
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    let body: any = {};
    if (path === "/api/catalog")
      body = {
        operators: {},
        adapters: [],
        execution_enabled: true,
        templates: [
          { id: "remote", label: "远程 RGBA", pipeline },
          {
            id: "rgb",
            label: "普通 RGB",
            pipeline: {
              ...pipeline,
              inputs: {
                image: { kind: "rgb_image", carriers: ["artifact_ref"] },
              },
            },
          },
        ],
      };
    else if (path === "/api/drafts") body = { drafts: [] };
    else if (path === "/api/runs/dag_remote/references/prepared/rgba") {
      body = {
        source_run_id: "dag_remote",
        node_id: "prepared",
        port: "rgba",
        kind: "rgba_image",
        schema_name: "png",
        schema_version: "1.0",
        reference: { artifact_id: "historical_rgba_exact" },
      };
    } else if (path.startsWith("/api/inputs/")) {
      uploads.push(path);
      body = { image_ref: imageRef };
    } else if (path === "/api/runs" && route.request().method() === "POST") {
      starts.push(route.request().postDataJSON());
      body = {
        run: {
          run_id: "dag_remote",
          status: "running",
          dag: {
            revision: 1,
            node_states: {
              shape: {
                status: "running",
                attempts: [
                  {
                    status: "running",
                    error_code: "remote_queued",
                    remote_binding: {
                      service_id: "test-service",
                      submission_key: "job-exact-one",
                    },
                  },
                ],
              },
            },
          },
        },
        busy: false,
        outputs: [
          {
            node_id: "prepared",
            port: "rgba",
            kind: "rgba_image",
            url: "/prepared.png",
          },
        ],
      };
    } else if (path === "/api/runs") body = { runs: [] };
    else
      body = {
        run: {
          run_id: "dag_remote",
          status: "running",
          dag: {
            revision: 1,
            node_states: {
              shape: {
                status: "running",
                attempts: [
                  {
                    status: "running",
                    error_code: "remote_queued",
                    remote_binding: {
                      service_id: "test-service",
                      submission_key: "job-exact-one",
                    },
                  },
                ],
              },
            },
          },
        },
        busy: false,
        outputs: [
          {
            node_id: "prepared",
            port: "rgba",
            kind: "rgba_image",
            url: "/prepared.png",
          },
        ],
      };
    await route.fulfill({ json: body });
  });
  await page.goto("/");
  page.on("dialog", (dialog) => dialog.accept());
  await page.getByRole("button", { name: "远程 RGBA", exact: true }).click();
  await page.getByRole("button", { name: "运行", exact: true }).click();
  await expect(page.getByText(/此流程不自动抠图/)).toBeVisible();
  await page.getByLabel("图片来源", { exact: true }).selectOption("upload");
  await expect(page.getByLabel("上传运行图片")).toHaveAttribute(
    "accept",
    "image/png",
  );
  await page.getByLabel("上传运行图片").setInputFiles({
    name: "prepared.png",
    mimeType: "image/png",
    buffer: Buffer.from("rgba transport fixture"),
  });
  await expect(
    page.getByText("图片已上传；点击启动新运行才会执行模型。"),
  ).toBeVisible();
  expect(uploads).toEqual(["/api/inputs/rgba"]);
  expect(starts).toEqual([]);
  await page.getByRole("button", { name: "启动新运行", exact: true }).click();
  await expect.poll(() => starts.length).toBe(1);
  await expect(page.getByText(/上次报告作业已排队/)).toBeVisible();
  await expect(page.getByLabel("远程作业标识 · shape")).toHaveValue(
    "job-exact-one",
  );
  expect(starts[0].image_ref).toEqual(imageRef);
  expect(starts[0].pipeline.inputs.image.kind).toBe("rgba_image");
  await page
    .getByRole("button", {
      name: "用作输入 image · prepared · rgba",
      exact: true,
    })
    .click();
  await expect(page.getByLabel("图片来源", { exact: true })).toHaveValue(
    "reference",
  );
  await expect(
    page.getByText("historical_rgba_exact", { exact: true }),
  ).toBeVisible();
  expect(starts).toHaveLength(1);
  await page.getByRole("button", { name: "启动新运行", exact: true }).click();
  await expect.poll(() => starts.length).toBe(2);
  expect(starts[1].image_ref).toEqual({ artifact_id: "historical_rgba_exact" });
  expect(uploads).toEqual(["/api/inputs/rgba"]);
  await page.getByRole("button", { name: "普通 RGB", exact: true }).click();
  await page.getByRole("button", { name: "运行", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "启动新运行", exact: true }),
  ).toBeDisabled();
  expect(starts).toHaveLength(2);
});

for (const editDuringLoad of [false, true]) {
  test(`loading a saved run configuration preserves intent (edited=${editDuringLoad})`, async ({
    page,
  }) => {
    let writes = 0;
    let release: (() => void) | undefined;
    const pipeline = {
      pipeline: "original_run",
      version: "1",
      inputs: { image: { kind: "rgb_image" } },
      nodes: {},
    };
    await page.route("**/api/**", async (route) => {
      const path = new URL(route.request().url()).pathname;
      if (route.request().method() === "POST") writes++;
      let body: unknown = {};
      if (path === "/api/catalog")
        body = {
          operators: {},
          adapters: [],
          templates: [],
          execution_enabled: true,
        };
      else if (path === "/api/drafts") body = { drafts: [] };
      else if (path === "/api/runs")
        body = { runs: [{ run_id: "dag_original", status: "succeeded" }] };
      else if (path.endsWith("/draft")) {
        if (editDuringLoad)
          await new Promise<void>((resolve) => {
            release = resolve;
          });
        body = {
          source_run_id: "dag_original",
          source_plan_id: "original-plan",
          pipeline,
        };
      } else
        body = {
          run: {
            run_id: "dag_original",
            status: "succeeded",
            dag: { plan_id: "original-plan", revision: 1, node_states: {} },
          },
        };
      await route.fulfill({ json: body });
    });
    await page.goto("/");
    await page.getByRole("button", { name: "运行", exact: true }).click();
    await page
      .getByLabel("选择运行", { exact: true })
      .selectOption("dag_original");
    page.once("dialog", (dialog) => dialog.dismiss());
    await page
      .getByRole("button", { name: "将配置载入画布", exact: true })
      .click();
    await expect(
      page.getByRole("button", { name: "将配置载入画布", exact: true }),
    ).toBeVisible();
    page.once("dialog", (dialog) => dialog.accept());
    await page
      .getByRole("button", { name: "将配置载入画布", exact: true })
      .click();
    if (editDuringLoad) {
      await expect.poll(() => !!release).toBe(true);
      await page.getByLabel("管线名称").fill("new_local_config");
      release!();
      await expect(page.getByText(/读取运行配置期间画布已修改/)).toBeVisible();
      await expect(page.getByLabel("管线名称")).toHaveValue("new_local_config");
    } else {
      await expect(page.locator('input[value="original_run"]')).toBeVisible();
    }
    expect(writes).toBe(0);
  });
}

test("image outputs preview on demand and report errors without dispatch", async ({
  page,
}) => {
  let reads = 0;
  let mutations = 0;
  let fail = false;
  const output = "/api/runs/dag_images/outputs/transform/image";
  const png = Buffer.from(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=",
    "base64",
  );
  await page.route("**/api/**", async (route) => {
    if (route.request().method() !== "GET") mutations++;
    const path = new URL(route.request().url()).pathname;
    if (path === output) {
      reads++;
      return route.fulfill(
        fail
          ? { status: 400, json: { error: "invalid evidence" } }
          : { body: png, contentType: "image/png" },
      );
    }
    await route.fulfill({
      json:
        path === "/api/catalog"
          ? {
              operators: {},
              adapters: [],
              templates: [],
              execution_enabled: true,
            }
          : path === "/api/drafts"
            ? { drafts: [] }
            : path === "/api/runs"
              ? { runs: [{ run_id: "dag_images", status: "succeeded" }] }
              : {
                  run: {
                    run_id: "dag_images",
                    status: "succeeded",
                    dag: { revision: 1, node_states: {} },
                  },
                  outputs: [
                    {
                      node_id: "transform",
                      port: "image",
                      kind: "rgba_image",
                      url: output,
                    },
                  ],
                },
    });
  });
  await page.goto("/");
  await page.getByRole("button", { name: "运行", exact: true }).click();
  await page.getByLabel("选择运行").selectOption("dag_images");
  const open = page.getByRole("button", {
    name: "预览图片 · transform · image",
    exact: true,
  });
  await expect(open).toBeVisible();
  expect(reads).toBe(0);
  await open.click();
  const image = page.getByRole("img", { name: "节点 transform 的 image 输出" });
  await expect(image).toBeVisible();
  expect(
    await image.evaluate((element: HTMLImageElement) => element.naturalWidth),
  ).toBe(1);
  await page
    .getByRole("button", { name: "收起图片 · transform · image", exact: true })
    .click();
  await expect(image).toHaveCount(0);
  fail = true;
  await open.click();
  await expect(page.getByRole("alert")).toContainText("图片读取失败");
  await expect(image).toBeHidden();
  const failedReads = reads;
  fail = false;
  await page
    .getByRole("button", {
      name: "重新读取图片 · transform · image",
      exact: true,
    })
    .click();
  await expect(image).toBeVisible();
  await expect(page.getByRole("alert")).toHaveCount(0);
  expect(reads).toBe(failedReads + 1);
  expect(mutations).toBe(0);
});

test("compile diagnostics locate known nodes without changing the graph", async ({
  page,
}) => {
  const graph = {
    pipeline: "diagnostic",
    version: "1",
    inputs: {},
    nodes: { broken: { operator: "copy@1", inputs: {} } },
  };
  const compiled: unknown[] = [];
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/api/compile")
      compiled.push(route.request().postDataJSON().pipeline);
    await route.fulfill({
      json:
        path === "/api/catalog"
          ? {
              operators: {
                "copy@1": {
                  name: "copy",
                  version: "1",
                  inputs: {},
                  outputs: {},
                },
              },
              adapters: [],
              templates: [
                { id: "diagnostic", label: "diagnostic", pipeline: graph },
              ],
            }
          : path === "/api/compile"
            ? {
                ok: false,
                diagnostics: [
                  {
                    message: "broken.image is missing",
                    node_id: "broken",
                    port: "image",
                  },
                  { message: "global configuration error" },
                  { message: "unknown location", node_id: "absent" },
                ],
              }
            : { drafts: [] },
    });
  });
  await page.goto("/");
  page.on("dialog", (d) => d.accept());
  await page.getByRole("button", { name: "diagnostic", exact: true }).click();
  await page.getByRole("button", { name: "编译校验", exact: true }).click();
  await expect(page.getByRole("alert")).toHaveCount(3);
  await expect(page.getByRole("button", { name: /定位节点/ })).toHaveCount(1);
  await page
    .getByRole("button", { name: "定位节点 · broken", exact: true })
    .click();
  await expect(page.getByLabel("实例 ID")).toHaveValue("broken");
  await page.getByRole("button", { name: "编译校验", exact: true }).click();
  await expect.poll(() => compiled.length).toBe(2);
  expect(compiled[0]).toEqual(compiled[1]);
});

test("current failure is visible while old successful-retry errors remain in history", async ({
  page,
}) => {
  let mutations = 0;
  const failure = {
    attempt: 1,
    status: "failed",
    error_code: "BACKEND_TIMEOUT",
    error_detail: "Backend exceeded the configured deadline",
  };
  await page.route("**/api/**", async (route) => {
    if (route.request().method() !== "GET") mutations++;
    const path = new URL(route.request().url()).pathname;
    await route.fulfill({
      json:
        path === "/api/catalog"
          ? {
              operators: {},
              adapters: [],
              templates: [],
              execution_enabled: true,
            }
          : path === "/api/drafts"
            ? { drafts: [] }
            : path === "/api/runs"
              ? { runs: [{ run_id: "dag_failure", status: "failed" }] }
              : {
                  run: {
                    run_id: "dag_failure",
                    status: "failed",
                    dag: {
                      revision: 3,
                      node_states: {
                        broken: { status: "failed", attempts: [failure] },
                        recovered: {
                          status: "succeeded",
                          attempts: [
                            failure,
                            { attempt: 2, status: "succeeded" },
                          ],
                        },
                      },
                    },
                  },
                },
    });
  });
  await page.goto("/");
  await page.getByRole("button", { name: "运行", exact: true }).click();
  await page.getByLabel("选择运行").selectOption("dag_failure");
  const alert = page.getByRole("alert", { name: "执行错误 · broken" });
  await expect(alert).toContainText("BACKEND_TIMEOUT");
  await expect(alert).toContainText("configured deadline");
  await expect(
    page.getByRole("alert", { name: "执行错误 · recovered" }),
  ).toHaveCount(0);
  expect(mutations).toBe(0);
});

test("operator catalog distinguishes registered implementations from contracts", async ({
  page,
}) => {
  const operator = (name: string) => ({
    name,
    version: "1",
    inputs: {},
    outputs: {},
  });
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    await route.fulfill({
      json:
        path === "/api/catalog"
          ? {
              operators: {
                "local@1": operator("local"),
                "remote@1": operator("remote"),
                "contract@1": operator("contract"),
              },
              adapters: [
                { name: "local", version: "1", operators: ["local@1"] },
              ],
              backends: [
                {
                  name: "remote",
                  version: "1",
                  adapter: "remote@1",
                  backend: "installed",
                  operators: ["remote@1"],
                },
              ],
              templates: [],
            }
          : { drafts: [] },
    });
  });
  await page.goto("/");
  const items = page.locator(".catalog-item");
  await expect(items).toHaveCount(3);
  await expect(items.filter({ hasText: "contract" })).toContainText("仅契约");
  await page.getByLabel("只看已注册实现").check();
  await expect(items).toHaveCount(2);
  await expect(items.filter({ hasText: "remote" })).toContainText("已注册实现");
  await page.getByPlaceholder("搜索算子…").fill("remote");
  await expect(items).toHaveCount(1);
  await page.getByLabel("只看已注册实现").uncheck();
  await page.getByPlaceholder("搜索算子…").fill("");
  await expect(items).toHaveCount(3);
  await items.filter({ hasText: "remote" }).click();
  await page
    .getByLabel("节点 Backend", { exact: true })
    .selectOption("installed");
  await expect(page.getByLabel("Adapter", { exact: true })).toHaveValue(
    "remote@1",
  );
  await expect(
    page.getByLabel("Adapter", { exact: true }).locator("option"),
  ).toContainText(["未指定", "remote@1"]);
});

test("new nodes do not choose between ambiguous adapters by catalog order", async ({
  page,
}) => {
  let compiled: any;
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/api/compile")
      compiled = route.request().postDataJSON().pipeline;
    await route.fulfill({
      json:
        path === "/api/catalog"
          ? {
              operators: {
                "generate@1": {
                  name: "generate",
                  version: "1",
                  inputs: {},
                  outputs: {},
                },
              },
              adapters: ["first", "second"].map((name) => ({
                name,
                version: "1",
                operators: ["generate@1"],
                defaults: { seed: name === "first" ? 1 : 2 },
              })),
              templates: [],
            }
          : path === "/api/compile"
            ? {
                ok: false,
                diagnostics: [
                  {
                    message: "requires exactly one compatible adapter",
                    node_id: "generate",
                  },
                ],
              }
            : { drafts: [] },
    });
  });
  await page.goto("/");
  await page.locator(".catalog-item").filter({ hasText: "generate" }).click();
  await expect(page.getByLabel("Adapter", { exact: true })).toHaveValue("");
  await page.getByRole("button", { name: "编译校验", exact: true }).click();
  await expect.poll(() => !!compiled).toBe(true);
  expect(compiled.nodes.generate.adapter).toBeUndefined();
  expect(compiled.nodes.generate.parameters).toBeUndefined();
  await page
    .getByRole("button", { name: "定位节点 · generate", exact: true })
    .click();
  await page.getByLabel("Adapter", { exact: true }).selectOption("second@1");
  await page.getByRole("button", { name: "编译校验", exact: true }).click();
  await expect.poll(() => compiled.nodes.generate.adapter).toBe("second@1");
});

test("pending dispatch blockers are visible without inventing attempts or polling processes", async ({
  page,
}) => {
  let mutations = 0;
  await page.route("**/api/**", async (route) => {
    if (route.request().method() !== "GET") mutations++;
    const path = new URL(route.request().url()).pathname;
    await route.fulfill({
      json:
        path === "/api/catalog"
          ? {
              operators: {},
              adapters: [],
              templates: [],
              execution_enabled: true,
            }
          : path === "/api/drafts"
            ? { drafts: [] }
            : path === "/api/runs"
              ? { runs: [{ run_id: "dag_gated", status: "recovery_blocked" }] }
              : {
                  run: {
                    run_id: "dag_gated",
                    status: "recovery_blocked",
                    dag: {
                      revision: 1,
                      node_states: {
                        generate: {
                          status: "pending",
                          dispatch_block_reason:
                            "old worker process still alive",
                          attempts: [],
                        },
                      },
                    },
                  },
                },
    });
  });
  await page.goto("/");
  await page.getByRole("button", { name: "运行", exact: true }).click();
  await page.getByLabel("选择运行").selectOption("dag_gated");
  const status = page.getByRole("status", { name: "派发受阻 · generate" });
  await expect(status).toContainText("old worker process still alive");
  await expect(status).toContainText("已保存的检查结果");
  await expect(
    page.getByRole("button", { name: "显式重试 · generate", exact: true }),
  ).toHaveCount(0);
  expect(mutations).toBe(0);
});

test("graph edits invalidate successful and in-flight compilation feedback", async ({
  page,
}) => {
  let release: (() => void) | undefined;
  let calls = 0;
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/api/compile") {
      calls++;
      if (calls === 2)
        await new Promise<void>((resolve) => {
          release = resolve;
        });
      await route.fulfill({
        json: { ok: true, execution_ready: true, plan: { marker: "old-plan" } },
      });
      return;
    }
    await route.fulfill({
      json:
        path === "/api/catalog"
          ? {
              operators: {},
              adapters: [],
              templates: [],
              execution_enabled: true,
            }
          : path === "/api/runs"
            ? { runs: [] }
            : { drafts: [] },
    });
  });
  await page.goto("/");
  const compile = page.getByRole("button", { name: "编译校验", exact: true });
  await compile.click();
  await expect(page.getByRole("status")).toContainText("编译通过");
  await page.getByLabel("管线名称").fill("changed_graph");
  await expect(page.getByRole("status")).toContainText("请重新编译");
  await expect(
    page.getByText("尚无当前图的编译结果。", { exact: true }),
  ).toBeVisible();
  await compile.click();
  await expect.poll(() => !!release).toBe(true);
  await page.getByLabel("管线版本").fill("2");
  release!();
  await expect(page.getByRole("status")).toContainText("忽略旧版本的编译结果");
  await expect(
    page.getByText("尚无当前图的编译结果。", { exact: true }),
  ).toBeVisible();
  await expect(compile).toBeEnabled();
  await compile.click();
  await expect(page.getByRole("status")).toContainText("编译通过");
  expect(calls).toBe(3);
});

test("slow draft load preserves newer edits and save reports its snapshot", async ({
  page,
}) => {
  let finishLoad: (() => void) | undefined;
  let finishSave: (() => void) | undefined;
  let saved: any;
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    let body: unknown = {};
    if (path === "/api/catalog")
      body = { operators: {}, adapters: [], templates: [] };
    else if (path === "/api/drafts") body = { drafts: ["stored"] };
    else if (route.request().method() === "PUT") {
      saved = route.request().postDataJSON();
      await new Promise<void>((resolve) => {
        finishSave = resolve;
      });
      body = { saved: "stored" };
    } else {
      await new Promise<void>((resolve) => {
        finishLoad = resolve;
      });
      body = {
        pipeline: {
          pipeline: "old_remote",
          version: "1",
          inputs: {},
          nodes: {},
        },
        layout: {},
      };
    }
    await route.fulfill({ json: body });
  });
  page.on("dialog", (dialog) => dialog.accept());
  await page.goto("/");
  await page.getByLabel("加载草稿").selectOption("stored");
  await expect.poll(() => !!finishLoad).toBe(true);
  await page.getByLabel("管线名称").fill("newer_local");
  finishLoad!();
  await expect(page.getByRole("status")).toContainText("保留当前编辑");
  await expect(page.getByLabel("管线名称")).toHaveValue("newer_local");
  await page.getByRole("button", { name: "保存草稿", exact: true }).click();
  await expect.poll(() => !!finishSave).toBe(true);
  await page.getByLabel("管线名称").fill("after_save_request");
  finishSave!();
  await expect(page.getByRole("status")).toContainText("之后的编辑尚未保存");
  expect(saved.pipeline.pipeline).toBe("newer_local");
  await expect(page.getByLabel("管线名称")).toHaveValue("after_save_request");
});

test("malformed YAML import reports an error without replacing the canvas", async ({
  page,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.route("**/api/**", (route) =>
    route.fulfill({
      json:
        new URL(route.request().url()).pathname === "/api/catalog"
          ? { operators: {}, adapters: [], templates: [] }
          : { drafts: [] },
    }),
  );
  await page.goto("/");
  await page.getByLabel("管线名称").fill("keep_my_graph");
  await page.locator('input[type="file"][accept=".yaml,.yml"]').setInputFiles({
    name: "broken.yaml",
    mimeType: "application/yaml",
    buffer: Buffer.from(
      "pipeline: broken\nversion: 1\ninputs:\n  image:\n    kinds: rgb_image\nnodes: {}\n",
    ),
  });
  await expect(page.getByRole("status")).toContainText("无效输入");
  await expect(page.getByLabel("管线名称")).toHaveValue("keep_my_graph");
  await expect(page.locator(".react-flow__node")).toHaveCount(1);
  expect(errors).toEqual([]);
});

test("node inspector exposes authoritative input and output port contracts", async ({
  page,
}) => {
  const inputs = {
    depth: {
      kinds: ["depth_image"],
      cardinality: "one_or_more",
      carriers: ["artifact_ref"],
      schema_name: "Depth",
      schema_version: "1",
      require_frame: true,
      require_unit: true,
    },
  };
  const outputs = {
    points: {
      kinds: ["point_cloud"],
      cardinality: "one",
      carriers: ["artifact_ref"],
    },
  };
  await page.route("**/api/**", (route) =>
    route.fulfill({
      json:
        new URL(route.request().url()).pathname === "/api/catalog"
          ? {
              operators: {
                "inspect@1": { name: "inspect", version: "1", inputs, outputs },
              },
              adapters: [],
              templates: [
                {
                  id: "contracts",
                  label: "contracts",
                  pipeline: {
                    pipeline: "contracts",
                    version: "1",
                    inputs: {},
                    nodes: {
                      reconstruct: { operator: "inspect@1", inputs: {} },
                    },
                  },
                },
              ],
            }
          : { drafts: [] },
    }),
  );
  page.on("dialog", (dialog) => dialog.accept());
  await page.goto("/");
  await page.getByRole("button", { name: "contracts", exact: true }).click();
  await page.locator('.react-flow__node[data-id="reconstruct"]').click();
  await page.getByText("输入输出端口契约", { exact: true }).click();
  const panel = page
    .locator("details")
    .filter({ has: page.getByText("输入输出端口契约", { exact: true }) });
  await expect(panel.locator("pre")).toBeVisible();
  expect(JSON.parse(await panel.locator("pre").innerText())).toEqual({
    inputs,
    outputs,
  });
});

test("input contract editing rejects malformed kinds without corrupting the canvas", async ({
  page,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.route("**/api/**", (route) =>
    route.fulfill({
      json:
        new URL(route.request().url()).pathname === "/api/catalog"
          ? { operators: {}, adapters: [], templates: [] }
          : { drafts: [] },
    }),
  );
  await page.goto("/");
  await page.locator('.react-flow__node[data-id="input:image"]').click();
  const input = page.getByLabel("输入契约 · JSON");
  await input.fill('{"kinds":"rgb_image"}');
  await page.getByRole("button", { name: "应用输入契约", exact: true }).click();
  await expect(page.getByRole("status")).toContainText("无效输入：image");
  await expect(
    page.locator('.react-flow__node[data-id="input:image"]'),
  ).toContainText("rgb_image");
  await input.fill('{"kind":"rgba_image","carriers":["artifact_ref"]}');
  await page.getByRole("button", { name: "应用输入契约", exact: true }).click();
  await expect(page.getByRole("status")).toContainText("输入契约已更新");
  await expect(
    page.locator('.react-flow__node[data-id="input:image"]'),
  ).toContainText("rgba_image");
  expect(errors).toEqual([]);
});

test("repeated catalog additions keep distinct instances clear of existing nodes", async ({
  page,
}) => {
  let saved: any;
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (route.request().method() === "PUT")
      saved = route.request().postDataJSON();
    await route.fulfill({
      json:
        path === "/api/catalog"
          ? {
              operators: {
                "copy@1": {
                  name: "copy",
                  version: "1",
                  inputs: { image: { kinds: ["rgb_image"] } },
                  outputs: { image: { kinds: ["rgb_image"] } },
                },
              },
              adapters: [{ name: "copy", version: "1", operators: ["copy@1"] }],
              templates: [],
            }
          : { drafts: [] },
    });
  });
  await page.goto("/");
  const add = page
    .locator(".catalog-item")
    .filter({ has: page.getByText("copy", { exact: true }) });
  await add.click();
  await expect(page.locator('.react-flow__node[data-id="copy"]')).toBeVisible();
  await add.click();
  await expect(
    page.locator('.react-flow__node[data-id="copy_2"]'),
  ).toBeVisible();
  await page.getByRole("button", { name: "保存草稿", exact: true }).click();
  await expect.poll(() => saved?.pipeline.nodes.copy_2).toBeTruthy();
  expect(Object.keys(saved.pipeline.nodes)).toEqual(["copy", "copy_2"]);
  expect(saved.layout.copy_2.x).toBeGreaterThan(saved.layout.copy.x + 200);
  expect(saved.pipeline.nodes.copy.inputs).toEqual({});
  expect(saved.pipeline.nodes.copy_2.inputs).toEqual({});
  const boxes = await page.locator(".react-flow__node").evaluateAll((nodes) =>
    nodes.map((node) => {
      const box = node.getBoundingClientRect();
      return {
        left: box.left,
        right: box.right,
        top: box.top,
        bottom: box.bottom,
      };
    }),
  );
  for (let i = 0; i < boxes.length; i++)
    for (let j = i + 1; j < boxes.length; j++) {
      const a = boxes[i],
        b = boxes[j];
      expect(
        a.right <= b.left ||
          b.right <= a.left ||
          a.bottom <= b.top ||
          b.bottom <= a.top,
      ).toBe(true);
    }
});

test("recovery evidence diagnostics expose exact references without dispatch", async ({
  page,
}) => {
  let mutations = 0;
  const bad = { "sha256:missing-output": "blob not found" };
  await page.route("**/api/**", (route) => {
    if (route.request().method() !== "GET") mutations++;
    const path = new URL(route.request().url()).pathname;
    return route.fulfill({
      json:
        path === "/api/catalog"
          ? {
              operators: {},
              adapters: [],
              templates: [],
              execution_enabled: true,
            }
          : path === "/api/drafts"
            ? { drafts: [] }
            : path === "/api/runs"
              ? {
                  runs: [{ run_id: "dag_blocked", status: "recovery_blocked" }],
                }
              : {
                  run: {
                    run_id: "dag_blocked",
                    status: "recovery_blocked",
                    dag: {
                      revision: 3,
                      node_states: {},
                      invalid_evidence: bad,
                      unassigned_evidence_blocks: bad,
                    },
                  },
                },
    });
  });
  await page.goto("/");
  await page.getByRole("button", { name: "运行", exact: true }).click();
  await page
    .getByLabel("选择运行", { exact: true })
    .selectOption("dag_blocked");
  await expect(
    page.getByRole("alert", { name: "未定位的证据阻塞" }),
  ).toContainText("sha256:missing-output");
  const summary = page.getByText("已登记的证据问题", { exact: true });
  await summary.click();
  expect(
    JSON.parse(await summary.locator("..").locator("pre").innerText()),
  ).toEqual(bad);
  expect(mutations).toBe(0);
});

test("multi-input editor uploads RGB and mask and submits complete bindings", async ({
  page,
}) => {
  const starts: any[] = [];
  let rejectMask = false;
  let delayMask: Promise<void> | undefined;
  let releaseMask: (() => void) | undefined;
  const template = {
    pipeline: "mask_shape",
    version: "1",
    inputs: {
      image: { kind: "rgb_image", carriers: ["artifact_ref"] },
      mask: {
        kind: "binary_mask",
        carriers: ["artifact_ref"],
        schema_name: "png",
        schema_version: "1.0",
      },
    },
    nodes: {
      composite: {
        operator: "apply_binary_mask@1",
        inputs: {
          image: "pipeline.inputs.image",
          mask: "pipeline.inputs.mask",
        },
      },
    },
  };
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    let body: unknown = {};
    if (path === "/api/catalog")
      body = {
        operators: {
          "apply_binary_mask@1": {
            name: "apply_binary_mask",
            version: "1",
            inputs: {
              image: { kinds: ["rgb_image"], carriers: ["artifact_ref"] },
              mask: {
                kinds: ["binary_mask"],
                carriers: ["artifact_ref"],
                schema_name: "png",
                schema_version: "1.0",
              },
            },
            outputs: {
              rgba: {
                kinds: ["rgba_image"],
                carriers: ["artifact_ref"],
              },
            },
          },
        },
        adapters: [
          {
            name: "apply_binary_mask",
            version: "1",
            operators: ["apply_binary_mask@1"],
          },
        ],
        templates: [{ id: "mask", label: "RGB + mask", pipeline: template }],
        execution_enabled: true,
      };
    else if (path === "/api/runs/dag_mask/references/encode/image")
      body = {
        source_run_id: "dag_mask",
        node_id: "encode",
        port: "image",
        kind: "rgb_image",
        schema_name: "png",
        schema_version: "1.0",
        reference: { artifact_id: "reusable_rgb" },
      };
    else if (path === "/api/drafts") body = { drafts: [] };
    else if (path === "/api/inputs/image")
      body = { image_ref: { artifact_id: "image_ref" } };
    else if (path === "/api/inputs/mask") {
      if (rejectMask) {
        await route.fulfill({
          status: 400,
          json: { error: "invalid replacement mask" },
        });
        return;
      }
      if (delayMask) await delayMask;
      body = { mask_ref: { artifact_id: "mask_ref" } };
    } else if (path === "/api/runs" && route.request().method() === "POST") {
      starts.push(route.request().postDataJSON());
      body = {
        outputs: [
          {
            node_id: "encode",
            port: "image",
            kind: "rgb_image",
            url: "/output.png",
          },
        ],
        run: {
          run_id: "dag_mask",
          status: "running",
          dag: { revision: 1, node_states: {} },
        },
      };
    } else if (path === "/api/runs") body = { runs: [] };
    else
      body = {
        outputs: [
          {
            node_id: "encode",
            port: "image",
            kind: "rgb_image",
            url: "/output.png",
          },
        ],
        run: {
          run_id: "dag_mask",
          status: "running",
          dag: { revision: 1, node_states: {} },
        },
      };
    await route.fulfill({ json: body });
  });
  await page.goto("/");
  page.on("dialog", (dialog) => dialog.accept());
  await page.getByRole("button", { name: "RGB + mask", exact: true }).click();
  await page.getByRole("button", { name: "运行", exact: true }).click();
  await page.getByLabel("上传输入 image").setInputFiles({
    name: "image.png",
    mimeType: "image/png",
    buffer: Buffer.from("rgb"),
  });
  await page.getByLabel("上传输入 mask").setInputFiles({
    name: "mask.png",
    mimeType: "image/png",
    buffer: Buffer.from("mask"),
  });
  const start = page.getByRole("button", { name: "启动新运行", exact: true });
  await expect(start).toBeEnabled();
  await start.click();
  await expect.poll(() => starts.length).toBe(1);
  expect(starts[0].input_refs).toEqual({
    image: { artifact_id: "image_ref" },
    mask: { artifact_id: "mask_ref" },
  });
  expect(starts[0]).not.toHaveProperty("image_ref");
  await page
    .getByRole("button", {
      name: "用作输入 image · encode · image",
      exact: true,
    })
    .click();
  await expect(page.getByLabel("输入 image Artifact ID")).toHaveValue(
    "reusable_rgb",
  );
  expect(starts).toHaveLength(1);
  rejectMask = true;
  await page.getByLabel("上传输入 mask").setInputFiles({
    name: "bad.png",
    mimeType: "image/png",
    buffer: Buffer.from("invalid"),
  });
  await expect(
    page.getByText(/输入 mask 上传失败：.*invalid replacement mask/),
  ).toBeVisible();
  await expect(page.getByLabel("输入 mask Artifact ID")).toHaveValue("");
  await expect(start).toBeDisabled();
  await expect(page.getByLabel("输入 image Artifact ID")).toHaveValue(
    "reusable_rgb",
  );
  expect(starts).toHaveLength(1);
  rejectMask = false;
  delayMask = new Promise<void>((resolve) => {
    releaseMask = resolve;
  });
  const sent = page.waitForRequest((r) => r.url().endsWith("/api/inputs/mask"));
  await page.getByLabel("上传输入 mask").setInputFiles({
    name: "late.png",
    mimeType: "image/png",
    buffer: Buffer.from("mask"),
  });
  await sent;
  await page.getByRole("button", { name: "＋ 管线输入", exact: true }).click();
  await expect(page.getByLabel("输入 input Artifact ID")).toBeVisible();
  releaseMask!();
  await expect(
    page.getByText("上传期间输入契约已修改，请为当前输入重新选择文件。"),
  ).toBeVisible();
  await expect(page.getByLabel("输入 mask Artifact ID")).toHaveValue("");
  await expect(start).toBeDisabled();
  expect(starts).toHaveLength(1);
});

test("changing selected run clears historical input bindings", async ({
  page,
}) => {
  const runs = ["run_a", "run_b"];
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    let body: any = {};
    if (path === "/api/catalog") {
      body = {
        operators: {
          "apply_binary_mask@1": {
            name: "apply_binary_mask",
            version: "1",
            inputs: {
              image: { kinds: ["rgb_image"], carriers: ["artifact_ref"] },
              mask: {
                kinds: ["binary_mask"],
                carriers: ["artifact_ref"],
                schema_name: "png",
                schema_version: "1.0",
              },
            },
            outputs: {
              rgba: { kinds: ["rgba_image"], carriers: ["artifact_ref"] },
            },
          },
        },
        adapters: [
          {
            name: "apply_binary_mask",
            version: "1",
            operators: ["apply_binary_mask@1"],
          },
        ],
        templates: [
          {
            id: "mask",
            label: "RGB + mask",
            pipeline: {
              pipeline: "mask",
              version: "1",
              inputs: {
                image: { kind: "rgb_image", carriers: ["artifact_ref"] },
                mask: {
                  kind: "binary_mask",
                  carriers: ["artifact_ref"],
                  schema_name: "png",
                  schema_version: "1.0",
                },
              },
              nodes: {
                composite: {
                  operator: "apply_binary_mask@1",
                  inputs: {
                    image: "pipeline.inputs.image",
                    mask: "pipeline.inputs.mask",
                  },
                },
              },
            },
          },
        ],
        execution_enabled: true,
      };
    } else if (path === "/api/drafts") body = { drafts: [] };
    else if (path === "/api/runs")
      body = { runs: runs.map((run_id) => ({ run_id, status: "succeeded" })) };
    else if (path.endsWith("/references/encode/image"))
      body = {
        source_run_id: "run_a",
        node_id: "encode",
        port: "image",
        kind: "rgb_image",
        reference: { artifact_id: "old" },
      };
    else
      body = {
        run: {
          run_id: path.includes("run_b") ? "run_b" : "run_a",
          status: "succeeded",
          dag: { revision: 1, node_states: {} },
        },
        outputs: [
          {
            node_id: "encode",
            port: "image",
            kind: "rgb_image",
            url: "/output.png",
          },
        ],
      };
    await route.fulfill({ json: body });
  });
  await page.goto("/");
  page.on("dialog", (d) => d.accept());
  await page.getByRole("button", { name: "RGB + mask", exact: true }).click();
  await page.getByRole("button", { name: "运行", exact: true }).click();
  await page.getByLabel("选择运行").selectOption("run_a");
  await page
    .getByRole("button", {
      name: "用作输入 image · encode · image",
      exact: true,
    })
    .click();
  await expect(page.getByLabel("输入 image Artifact ID")).toHaveValue("old");
  await page.getByLabel("选择运行").selectOption("run_b");
  await expect(page.getByLabel("输入 image Artifact ID")).toHaveValue("");
  await expect(page.getByLabel("输入 mask Artifact ID")).toHaveValue("");
  await expect(
    page.getByRole("button", { name: "启动新运行", exact: true }),
  ).toBeDisabled();
});

test("backend-only node exposes the unique adapter parameter form", async ({
  page,
}) => {
  page.on("dialog", (dialog) => dialog.accept());
  let compiled: any;
  const pipeline = {
    pipeline: "mask_only",
    version: "1",
    inputs: {},
    nodes: {
      segment: {
        operator: "text_segmentation@1",
        backend: "sam3",
        parameters: { prompt: "chair" },
        inputs: {},
      },
    },
  };
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    let body: any = {};
    if (path === "/api/catalog")
      body = {
        operators: {
          "text_segmentation@1": {
            name: "text_segmentation",
            version: "1",
            inputs: {},
            outputs: {},
          },
        },
        adapters: [],
        backends: [
          {
            name: "remote_text_segmentation",
            version: "1",
            adapter: "remote_text_segmentation@1",
            backend: "sam3",
            operators: ["text_segmentation@1"],
            parameter_schema: { properties: { prompt: { type: "string" } } },
          },
        ],
        templates: [{ id: "mask_only", label: "mask_only", pipeline }],
      };
    else if (path === "/api/drafts") body = { drafts: [] };
    else if (path === "/api/compile") {
      compiled = route.request().postDataJSON().pipeline;
      body = { valid: true };
    }
    await route.fulfill({ json: body });
  });
  await page.goto("/");
  await page.getByRole("button", { name: "mask_only", exact: true }).click();
  await page
    .locator(".react-flow__node")
    .filter({ hasText: "text_segmentation@1" })
    .click();
  await page.getByLabel("参数 prompt", { exact: true }).fill("robot");
  await page.getByLabel("参数 prompt", { exact: true }).blur();
  await page.getByRole("button", { name: "编译校验", exact: true }).click();
  await expect
    .poll(() => compiled?.nodes.segment.parameters.prompt)
    .toBe("robot");
});
