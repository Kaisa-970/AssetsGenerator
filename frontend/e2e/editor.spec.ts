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

test("execution uses frozen server runs and explicit revisioned actions", async ({
  page,
}) => {
  const calls: { path: string; body: any }[] = [];
  let status = "waiting_for_input";
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
    else if (path.endsWith("/review")) body = { url: "http://127.0.0.1:9876/" };
    else
      body = {
        run: run(),
        busy: false,
        outputs: [{ node_id: "generate", port: "glb", url: "/output.glb" }],
      };
    await route.fulfill({ json: body });
  });
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
  ).toHaveAttribute("href", "http://127.0.0.1:9876/");
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
