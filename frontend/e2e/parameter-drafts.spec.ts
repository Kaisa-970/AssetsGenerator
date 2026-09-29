import { expect, test } from "@playwright/test";

test("unapplied parameters survive node and tab changes; history badges name their source", async ({
  page,
}) => {
  test.setTimeout(60000); // Multiple edits, panel switches and history import roundtrip.
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
  await page.getByRole("button", { name: "继续替换", exact: true }).click();
  if (!(await page.getByLabel("图片来源", { exact: true }).isVisible()))
    await page.getByText("高级：图片来源", { exact: true }).click();
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
  const inspector = page.locator(".inspector");
  await expect(
    inspector.getByLabel("参数 width", { exact: true }),
  ).toBeInViewport();
  expect(
    await inspector.evaluate((el) => {
      const parameter = el.querySelector('[aria-label="参数 width"]')!;
      const save = Array.from(el.querySelectorAll("button")).find(
        (button) => button.textContent?.trim() === "保存草稿",
      )!;
      return Boolean(
        parameter.compareDocumentPosition(save) &
        Node.DOCUMENT_POSITION_FOLLOWING,
      );
    }),
  ).toBe(true);
  await page
    .locator(".inspector")
    .getByLabel("参数 width", { exact: true })
    .fill("bad");
  await expect(
    page.getByRole("button", { name: "保存草稿", exact: true }),
  ).toBeDisabled();
  const separator = page.getByRole("separator", { name: "调整属性面板宽度" });
  const handle = (await separator.boundingBox())!;
  await page.mouse.move(handle.x + handle.width / 2, handle.y + 50);
  await page.mouse.down();
  await page.mouse.move(handle.x - 96, handle.y + 50, { steps: 5 });
  await page.mouse.up();
  await expect(separator).toHaveAttribute("aria-valuenow", "380");
  expect((await page.locator(".inspector").boundingBox())!.width).toBe(380);
  await separator.focus();
  await separator.press("ArrowLeft");
  await expect(separator).toHaveAttribute("aria-valuenow", "400");
  await expect(
    page.locator(".inspector").getByLabel("参数 width", { exact: true }),
  ).toHaveValue("bad");
  const beforeFocus = (await page.locator(".canvas").boundingBox())!;
  await page.getByRole("button", { name: "专注画布", exact: true }).click();
  await expect(page.locator(".inspector")).toBeHidden();
  await expect(page.locator("#node-preview-window")).toBeHidden();
  expect((await page.locator(".canvas").boundingBox())!.width).toBeGreaterThanOrEqual(
    beforeFocus.width,
  );
  await expect(
    page
      .locator('[data-id="first"]')
      .getByLabel("节点参数 width", { exact: true }),
  ).toHaveValue("bad");
  await page.getByRole("button", { name: "恢复面板布局", exact: true }).click();
  await expect(page.locator(".inspector")).toBeVisible();
  await expect(page.locator("#node-preview-window")).toBeHidden();
  expect((await page.locator(".inspector").boundingBox())!.width).toBe(400);
  const canvasWidth = (await page.locator(".canvas").boundingBox())!.width;
  await page.getByRole("button", { name: "收起属性面板", exact: true }).click();
  await page.getByRole("button", { name: "收起节点目录", exact: true }).click();
  await expect(page.locator(".inspector")).toBeHidden();
  await expect
    .poll(async () => (await page.locator(".canvas").boundingBox())!.width)
    .toBeGreaterThanOrEqual(canvasWidth);
  await expect(
    page
      .locator('[data-id="first"]')
      .getByLabel("节点参数 width", { exact: true }),
  ).toHaveValue("bad");
  await expect(
    page.getByRole("button", {
      name: "启动新运行 · 检查执行范围",
      exact: true,
    }),
  ).toBeDisabled();
  await page.getByRole("button", { name: "展开节点目录", exact: true }).click();
  await page
    .getByRole("button", { name: "定位未应用参数", exact: true })
    .click();
  await expect(page.locator(".inspector")).toBeVisible();
  await expect(page.locator('[data-id="first"] article')).toHaveClass(
    /blueprint-node-selected/,
  );
  await expect(
    page.locator(".inspector").getByLabel("参数 width", { exact: true }),
  ).toHaveValue("bad");
  await page
    .getByRole("button", { name: "运行记录与诊断", exact: true })
    .click();
  await expect(page.locator(".inspector")).toBeVisible();
  expect((await page.locator(".inspector").boundingBox())!.width).toBe(400);
  await expect(
    page.getByRole("region", { name: "运行记录与诊断面板", exact: true }),
  ).toBeVisible();
  await expect(
    page.locator(".inspector").getByLabel("参数 width", { exact: true }),
  ).toBeVisible();
  await expect(
    page.locator(".inspector").getByLabel("参数 width", { exact: true }),
  ).toHaveValue("bad");
  await page.getByRole("button", { name: "收起运行记录", exact: true }).click();
  await expect(
    page.getByRole("region", { name: "运行记录与诊断面板", exact: true }),
  ).toBeHidden();
  await page
    .getByRole("button", { name: "运行记录与诊断", exact: true })
    .click();

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
  await expect(
    page.locator(".inspector").getByLabel("参数 width", { exact: true }),
  ).toHaveValue("4");
  await inspector.evaluate((el) => {
    el.scrollTop = el.scrollHeight;
  });
  await page.getByLabel("定位画布节点", { exact: true }).selectOption("second");
  await expect.poll(() => inspector.evaluate((el) => el.scrollTop)).toBe(0);
  await page.getByRole("button", { name: "配置", exact: true }).click();
  await expect(
    page.locator(".inspector").getByLabel("参数 width", { exact: true }),
  ).toHaveValue("4");
  await page
    .getByRole("button", { name: "编辑待应用参数 · first", exact: true })
    .click();
  await expect(
    page.locator(".inspector").getByLabel("参数 width", { exact: true }),
  ).toHaveValue("bad");
  await page
    .locator(".inspector")
    .getByLabel("参数 width", { exact: true })
    .fill("8");
  await page
    .locator(".inspector")
    .getByLabel("参数 width", { exact: true })
    .press("Tab");
  await expect(page.getByLabel("未应用参数", { exact: true })).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "保存草稿", exact: true }),
  ).toBeEnabled();
  await expect(page.getByLabel("节点参数 JSON", { exact: true })).toBeHidden();
  await page.getByText("高级：完整参数 JSON", { exact: true }).click();
  await page.getByLabel("节点参数 JSON", { exact: true }).fill("{");
  await expect(
    page.getByRole("button", { name: "保存草稿", exact: true }),
  ).toBeDisabled();
  await page.getByRole("button", { name: "运行记录与诊断", exact: true }).click();
  await expect(page.getByLabel("未应用参数", { exact: true })).toBeVisible();
  await page
    .getByRole("button", { name: "编辑待应用参数 · first", exact: true })
    .click();
  await expect(page.getByLabel("节点参数 JSON", { exact: true })).toBeVisible();
  await expect(page.getByLabel("节点参数 JSON", { exact: true })).toHaveValue(
    "{",
  );
  await page
    .locator('[data-id="second"]')
    .getByLabel("节点参数 width", { exact: true })
    .fill("bad");
  await page
    .locator('[data-id="second"]')
    .getByLabel("节点参数 width", { exact: true })
    .blur();
  await page
    .getByRole("button", { name: "放弃当前节点 JSON 编辑", exact: true })
    .click();
  await expect(page.getByLabel("节点参数 JSON", { exact: true })).toHaveValue(
    /"width": 8/,
  );
  await expect(
    page.getByRole("button", { name: "编辑待应用参数 · first", exact: true }),
  ).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "编辑待应用参数 · second", exact: true }),
  ).toBeVisible();
  await expect(
    page
      .locator('[data-id="second"]')
      .getByLabel("节点参数 width", { exact: true }),
  ).toHaveValue("bad");
  await expect(
    page.getByRole("button", { name: "保存草稿", exact: true }),
  ).toBeDisabled();
  await page.getByRole("button", { name: "放弃全部未应用参数编辑" }).click();
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
  await page.getByRole("button", { name: "运行记录与诊断", exact: true }).click();
  await expect(page.getByLabel("画布历史状态来源")).toBeVisible();
  expect(starts).toBe(0);
});

test("节点内显示参数配置进度和缺少的必填参数", async ({ page }) => {
  const pipeline = {
    pipeline: "required_parameter_test",
    version: "1",
    inputs: {},
    nodes: {
      model: {
        operator: "shape@1",
        adapter: "shape@1",
        parameters: {},
        inputs: {},
      },
    },
  };
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    let body: unknown = {};
    if (path === "/api/catalog") {
      body = {
        execution_enabled: false,
        operators: {
          "shape@1": {
            name: "shape",
            version: "1",
            inputs: {},
            outputs: {},
          },
        },
        adapters: [
          {
            name: "shape",
            version: "1",
            operators: ["shape@1"],
            defaults: { steps: 32 },
            parameter_schema: {
              type: "object",
              required: ["steps", "profile"],
              properties: {
                steps: { type: "integer", minimum: 1 },
                profile: { type: "string", enum: ["fast", "quality"] },
              },
            },
          },
        ],
        templates: [{ id: "required", label: "必填参数测试", pipeline }],
      };
    } else if (path === "/api/drafts") body = { drafts: [] };
    else if (path === "/api/compile")
      body = { ok: true, execution_ready: false };
    await route.fulfill({ json: body });
  });
  await page.goto("/");
  await page.getByRole("button", { name: "必填参数测试", exact: true }).click();
  await page.getByRole("button", { name: "继续替换", exact: true }).click();
  const node = page.locator('[data-id="model"]');
  await expect(node.locator('[aria-label="参数状态 model"]')).toHaveText(
    "参数 1/2 已提供· 缺少必填：profile",
  );
  await node
    .getByLabel("节点参数 profile", { exact: true })
    .selectOption({ label: "quality" });
  await expect(node.locator('[aria-label="参数状态 model"]')).toHaveText(
    "参数 2/2 已提供",
  );
});

test("late preflight cannot start applied values after a node acquires an invalid parameter draft", async ({
  page,
}) => {
  let releasePreflight: (() => void) | undefined;
  const waiting = new Promise<void>((resolve) => {
    releasePreflight = resolve;
  });
  let preflightStarted = false;
  let starts = 0;
  const pipeline = {
    pipeline: "preflight_draft_race",
    version: "1",
    inputs: { image: { kind: "rgb_image", carriers: ["artifact_ref"] } },
    nodes: {
      resize: {
        operator: "resize@1",
        adapter: "resize@1",
        parameters: { width: 4 },
        inputs: { image: "pipeline.inputs.image" },
      },
    },
  };
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
            parameter_schema: {
              properties: { width: { type: "integer", minimum: 1 } },
            },
          },
        ],
        templates: [{ id: "race", label: "预检草稿竞态", pipeline }],
      };
    else if (path === "/api/drafts") body = { drafts: [] };
    else if (path === "/api/compile")
      body = { ok: true, execution_ready: true };
    else if (path === "/api/prepare-inputs")
      body = { input_refs: { image: { artifact_id: "image" } } };
    else if (path === "/api/preflight") {
      preflightStarted = true;
      await waiting;
      body = {
        digest: "preflight-before-draft",
        execution_ready: true,
        nodes: {
          resize: {
            status: "execute",
            reason: "new",
            detail: "Will execute resize",
          },
        },
        source_evidence_policy: "complete_snapshot",
      };
    } else if (path === "/api/runs") {
      if (route.request().method() === "POST") starts++;
      body = { runs: [] };
    }
    await route.fulfill({ json: body });
  });
  page.on("dialog", (dialog) => dialog.accept());
  await page.goto("/");
  await page.getByRole("button", { name: "预检草稿竞态", exact: true }).click();
  await page.getByRole("button", { name: "继续替换", exact: true }).click();
  if (!(await page.getByLabel("图片来源", { exact: true }).isVisible()))
    await page.getByText("高级：图片来源", { exact: true }).click();
  await page.getByLabel("图片来源", { exact: true }).selectOption("path");
  await page.getByLabel("运行图片路径").fill("/tmp/fixture.png");
  await page
    .getByRole("button", { name: "启动新运行 · 检查执行范围", exact: true })
    .click();
  await expect.poll(() => preflightStarted).toBe(true);
  const field = page
    .locator('.react-flow__node[data-id="resize"]')
    .getByLabel("节点参数 width", { exact: true });
  await field.fill("bad");
  await field.blur();
  await expect(page.getByLabel("未应用参数", { exact: true })).toContainText(
    "resize",
  );
  const response = page.waitForResponse("**/api/preflight");
  releasePreflight!();
  await response;
  await expect(
    page.getByRole("button", {
      name: "启动新运行 · 检查执行范围",
      exact: true,
    }),
  ).toBeDisabled();
  await expect(field).toHaveValue("bad");
  await expect(
    page.getByText("运行已创建；请在下方查看真实节点状态。", { exact: true }),
  ).toHaveCount(0);
  expect(starts).toBe(0);
});

test("a parameter draft edited during save is not reported as saved", async ({
  page,
}) => {
  let release!: () => void;
  let saving = false;
  let saved: any;
  const gate = new Promise<void>((resolve) => (release = resolve));
  const pipeline = {
    pipeline: "save_race",
    version: "1",
    inputs: {},
    nodes: {
      resize: {
        operator: "resize@1",
        adapter: "resize@1",
        parameters: { width: 4 },
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
          "resize@1": { name: "resize", version: "1", inputs: {}, outputs: {} },
        },
        adapters: [
          {
            name: "resize",
            version: "1",
            operators: ["resize@1"],
            parameter_schema: {
              properties: { width: { type: "integer", minimum: 1 } },
            },
          },
        ],
        templates: [{ id: "save", label: "保存竞态", pipeline }],
      };
    else if (path === "/api/drafts") body = { drafts: [] };
    else if (path === "/api/compile") body = { ok: true };
    else if (route.request().method() === "PUT") {
      saved = route.request().postDataJSON();
      saving = true;
      await gate;
      body = { saved: "my-pipeline" };
    }
    await route.fulfill({ json: body });
  });
  page.on("dialog", (d) => d.accept());
  await page.goto("/");
  await page.getByRole("button", { name: "保存竞态", exact: true }).click();
  await page.getByRole("button", { name: "继续替换", exact: true }).click();
  await page.getByRole("button", { name: "保存草稿", exact: true }).click();
  await expect.poll(() => saving).toBe(true);
  const field = page
    .locator('[data-id="resize"]')
    .getByLabel("节点参数 width", { exact: true });
  await field.fill("bad");
  release();
  await expect(page.locator("footer")).toContainText("之后的编辑尚未保存");
  await expect(field).toHaveValue("bad");
  expect(saved.pipeline.nodes.resize.parameters.width).toBe(4);
  await expect(
    page.getByRole("button", { name: "保存草稿", exact: true }),
  ).toBeDisabled();
});

test("node controls and Details share the same applied parameter state", async ({
  page,
}) => {
  const pipeline = {
    pipeline: "sync",
    version: "1",
    inputs: {},
    nodes: {
      resize: {
        operator: "resize@1",
        adapter: "resize@1",
        parameters: { width: 4 },
        inputs: {},
      },
    },
  };
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    const body =
      path === "/api/catalog"
        ? {
            operators: {
              "resize@1": {
                name: "resize",
                version: "1",
                inputs: {},
                outputs: {},
              },
            },
            adapters: [
              {
                name: "resize",
                version: "1",
                operators: ["resize@1"],
                defaults: { width: 8 },
                parameter_schema: {
                  properties: { width: { type: "integer", minimum: 1 } },
                },
              },
            ],
            templates: [{ id: "sync", label: "同步", pipeline }],
            execution_enabled: true,
          }
        : path === "/api/drafts"
          ? { drafts: [] }
          : path === "/api/compile"
            ? { ok: true, execution_ready: false }
            : { runs: [] };
    await route.fulfill({ json: body });
  });
  page.on("dialog", (d) => d.accept());
  await page.goto("/");
  await page.getByRole("button", { name: "同步", exact: true }).click();
  await page.getByRole("button", { name: "继续替换", exact: true }).click();
  await page.locator('[data-id="resize"] header').click();
  await page.getByLabel("定位画布节点", { exact: true }).selectOption("resize");
  const viewport = page.locator(".react-flow__viewport");
  await page.waitForTimeout(500);
  const beforeParameterEdit = await viewport.getAttribute("style");
  const nodeField = page
    .locator('[data-id="resize"]')
    .getByLabel("节点参数 width", { exact: true });
  await nodeField.fill("64");
  await nodeField.press("Enter");
  await page.waitForTimeout(500);
  await expect(viewport).toHaveAttribute("style", beforeParameterEdit || "");
  await expect(
    page
      .locator('[data-id="resize"]')
      .getByLabel("节点参数 width 来源", { exact: true }),
  ).toHaveText("显式配置");
  await expect(
    page.locator(".inspector").getByLabel("参数 width", { exact: true }),
  ).toHaveValue("64");
  await nodeField.fill("-1");
  await nodeField.press("Enter");
  await expect(nodeField).toHaveValue("-1");
  await expect(page.getByLabel("未应用参数", { exact: true })).toContainText(
    "resize",
  );
  await nodeField.press("Escape");
  await expect(nodeField).toHaveValue("64");
  await expect(page.getByLabel("未应用参数", { exact: true })).toHaveCount(0);
  await nodeField.fill("99");
  await nodeField.dispatchEvent("keydown", { key: "Enter", isComposing: true });
  await expect(page.getByLabel("未应用参数", { exact: true })).toContainText(
    "resize",
  );
  await nodeField.press("Escape");
  await expect(nodeField).toHaveValue("64");
  await page
    .locator(".inspector")
    .getByLabel("参数 width", { exact: true })
    .fill("bad");
  await page
    .locator(".inspector")
    .getByLabel("参数 width", { exact: true })
    .blur();
  await expect(nodeField).toHaveValue("bad");
  await expect(page.getByLabel("未应用参数", { exact: true })).toContainText(
    "resize",
  );
  await page
    .locator('[data-id="resize"]')
    .getByRole("button", { name: "恢复默认值 · width", exact: true })
    .click();
  await expect(nodeField).toHaveValue("8");
  await expect(
    page
      .locator('[data-id="resize"]')
      .getByLabel("节点参数 width 来源", { exact: true }),
  ).toHaveText("沿用实现默认值");
  await expect(
    page.locator(".inspector").getByLabel("参数 width 来源", { exact: true }),
  ).toHaveText("沿用实现默认值");
  await expect(
    page.locator(".inspector").getByLabel("参数 width", { exact: true }),
  ).toHaveValue("8");
  await expect(page.getByLabel("未应用参数", { exact: true })).toHaveCount(0);
});
