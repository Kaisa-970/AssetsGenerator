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
  await page.getByLabel("参数 seed", { exact: true }).fill("99");
  await page.getByLabel("参数 seed", { exact: true }).press("Tab");
  await page.getByLabel("参数 size", { exact: true }).selectOption("1");
  await page.getByLabel("参数 enabled", { exact: true }).selectOption("false");
  await page.getByRole("button", { name: "编译校验", exact: true }).click();
  await expect.poll(() => submitted?.nodes.first.parameters.seed).toBe(99);
  expect(submitted.nodes.first.parameters).toEqual({
    seed: 99,
    size: "1024",
    enabled: false,
  });
  expect(submitted.nodes.second.parameters).toEqual({ seed: 7 });
});

test("fixed run graph displays persisted plan instead of edited draft", async ({
  page,
}) => {
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    const plan = {
      plan_id: "fixed-id",
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

test("node backend selection uses installed schema and clears stale identity", async ({
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
                  adapter: "local@1",
                  defaults: { profile_digest: "new" },
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
  await page.getByLabel("节点 Backend", { exact: true }).selectOption("other");
  await expect(
    page.getByLabel("参数 profile_digest", { exact: true }),
  ).toContainText("new");
  await page.getByRole("button", { name: "编译校验", exact: true }).click();
  await expect.poll(() => saved?.nodes.left.backend).toBe("other");
  expect(saved.nodes.left.parameters).toEqual({ seed: 42 });
  expect(saved.nodes.right.parameters).toEqual({ profile_digest: "old" });
  expect(saved.nodes.right.backend).toBeUndefined();
});

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
    else if (path.startsWith("/api/inputs/")) {
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
  await page.getByRole("button", { name: "普通 RGB", exact: true }).click();
  await page.getByRole("button", { name: "运行", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "启动新运行", exact: true }),
  ).toBeDisabled();
  expect(starts).toHaveLength(1);
});

test("loading a saved run configuration only changes the draft", async ({
  page,
}) => {
  let writes = 0;
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
    else if (path.endsWith("/draft"))
      body = {
        source_run_id: "dag_original",
        source_plan_id: "original-plan",
        pipeline,
      };
    else
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
  await expect(page.locator('input[value="original_run"]')).toBeVisible();
  expect(writes).toBe(0);
});

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
  expect(mutations).toBe(0);
});
