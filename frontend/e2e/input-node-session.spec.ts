import { test, expect } from "@playwright/test";

for (const viewport of [
  { width: 1440, height: 900 },
  { width: 1920, height: 1080 },
]) {
  test(`node input bindings survive execution scope changes and feed the toolbar preflight (${viewport.width})`, async ({
    page,
  }) => {
    await page.setViewportSize(viewport);
    const starts: any[] = [];
    const preparations: any[] = [];
    const pipeline = {
      pipeline: "node-inputs",
      version: "1",
      inputs: { image: { kind: "rgb_image" }, text: { kind: "text" } },
      nodes: {
        encode: {
          operator: "encode",
          inputs: { image: "pipeline.inputs.image" },
        },
        segment: {
          operator: "segment",
          inputs: {
            image: "encode.outputs.image",
            text: "pipeline.inputs.text",
          },
        },
      },
    };
    await page.route("**/api/**", async (route) => {
      const path = new URL(route.request().url()).pathname;
      const method = route.request().method();
      let body: any = {};
      if (path === "/api/catalog")
        body = {
          operators: {
            encode: {
              name: "encode",
              version: "1",
              inputs: { image: { kind: "rgb_image" } },
              outputs: { image: { kind: "rgb_image" } },
            },
            segment: {
              name: "segment",
              version: "1",
              inputs: { image: { kind: "rgb_image" }, text: { kind: "text" } },
              outputs: {},
            },
          },
          adapters: [],
          templates: [{ id: "input-test", label: "input-test", pipeline }],
          execution_enabled: true,
        };
      else if (path === "/api/drafts") body = { drafts: [] };
      else if (path === "/api/compile")
        body = { ok: true, execution_ready: true };
      else if (path === "/api/inputs/image")
        body = { image_ref: { artifact_id: "image-bound" } };
      else if (path === "/api/inputs/text")
        body =
          method === "POST"
            ? { text_ref: { artifact_id: "text-bound" } }
            : { artifact_id: "text-bound", text: "chair" };
      else if (path === "/api/runs/created/references/prompt/text")
        body = {
          source_run_id: "created",
          node_id: "prompt",
          port: "text",
          kind: "text",
          reference: { artifact_id: "text-bound" },
        };
      else if (path === "/api/prepare-inputs") {
        const sent = route.request().postDataJSON();
        preparations.push(sent);
        body = { input_refs: sent.input_refs };
      } else if (path === "/api/preflight")
        body = {
          digest: "checked",
          execution_ready: true,
          nodes: {},
          source_evidence_policy: "whole_snapshot_closure",
        };
      else if (path === "/api/runs" && method === "POST") {
        starts.push(route.request().postDataJSON());
        body = {
          run: {
            run_id: "created",
            status: "succeeded",
            dag: { revision: 1, node_states: {} },
          },
          outputs: [
            { node_id: "prompt", port: "text", kind: "text", url: "/text" },
          ],
        };
      } else if (path === "/api/runs") body = { runs: [] };
      else
        body = {
          run: {
            run_id: "created",
            status: "succeeded",
            dag: { revision: 1, node_states: {} },
          },
          outputs: [
            { node_id: "prompt", port: "text", kind: "text", url: "/text" },
          ],
        };
      await route.fulfill({ json: body });
    });
    page.on("dialog", (dialog) => dialog.accept());
    await page.goto("/");
    await page.getByRole("button", { name: "input-test", exact: true }).click();
    await page.getByRole("button", { name: "继续替换", exact: true }).click();
    const imageNode = page.locator('.react-flow__node[data-id="input:image"]');
    const textNode = page.locator('.react-flow__node[data-id="input:text"]');
    await imageNode
      .getByLabel("上传输入 image", { exact: true })
      .setInputFiles({
        name: "in.png",
        mimeType: "image/png",
        buffer: Buffer.from("image"),
      });
    await expect(
      imageNode.getByLabel("输入 image Artifact ID", { exact: true }),
    ).toHaveValue("image-bound");
    // Portal input controls must retain native editing without graph shortcuts.
    await page
      .getByLabel("定位画布节点", { exact: true })
      .selectOption("input:text");
    await expect(textNode.locator(".blueprint-node-header")).toBeInViewport();
    await textNode.locator(".blueprint-node-header").click();
    const textField = textNode.getByLabel("文本输入 text", { exact: true });
    await textField.fill("chairs");
    await textField.press("End");
    await textField.press("Backspace");
    await expect(textField).toHaveValue("chair");
    await textField.press("Home");
    await textField.press("Delete");
    await expect(textField).toHaveValue("hair");
    await expect(textNode).toBeVisible();
    const viewportBeforeWheel = await page
      .locator(".react-flow__viewport")
      .getAttribute("style");
    await textField.hover();
    await page.mouse.wheel(0, 80);
    await page.waitForTimeout(200);
    expect(
      await page.locator(".react-flow__viewport").getAttribute("style"),
    ).toBe(viewportBeforeWheel);
    await textField.fill("chair");
    await textNode
      .getByRole("button", { name: "应用文本", exact: true })
      .click();
    await expect(
      textNode.getByLabel("输入 text Artifact ID", { exact: true }),
    ).toHaveValue("text-bound");
    await textNode.locator(".blueprint-node-header").click();
    await expect(
      page.getByRole("region", { name: "当前输入预览" }),
    ).toContainText("chair");
    await page
      .getByLabel("定位画布节点", { exact: true })
      .selectOption("input:image");
    await imageNode.locator(".blueprint-node-header").click();
    await expect(
      page.getByRole("region", { name: "当前输入预览" }),
    ).toContainText("当前草稿输入 · image");
    await expect(
      page.getByRole("region", { name: "当前输入预览" }),
    ).not.toContainText("chair");
    await page
      .getByLabel("定位画布节点", { exact: true })
      .selectOption("encode");
    const scope = page.getByLabel("只运行到选中节点（包含必要上游）");
    await scope.check();
    await expect(
      textNode.getByLabel("输入 text Artifact ID", { exact: true }),
    ).toHaveValue("text-bound");
    await page
      .getByRole("button", { name: "运行到这里 · 检查执行范围", exact: true })
      .click();
    await page
      .getByRole("button", { name: "确认执行上述范围", exact: true })
      .click();
    await expect.poll(() => starts.length).toBe(1);
    expect(preparations[0].input_refs).toEqual({
      image: { artifact_id: "image-bound" },
    });
    expect(starts[0].input_refs).toEqual(preparations[0].input_refs);
    // The text input is outside the selected encode branch, but remains editable.
    await page
      .getByLabel("定位画布节点", { exact: true })
      .selectOption("input:text");
    await textNode.getByText("从历史结果选择 · text", { exact: true }).click();
    await textNode
      .getByRole("button", { name: "选用 prompt.text → text", exact: true })
      .click();
    await expect(textNode).toContainText("下游输入来源：created / prompt.text");
    await scope.uncheck();
    await expect(
      textNode.getByLabel("输入 text Artifact ID", { exact: true }),
    ).toHaveValue("text-bound");
    await page
      .getByRole("button", { name: "启动新运行 · 检查执行范围", exact: true })
      .click();
    await expect.poll(() => starts.length).toBe(2);
    expect(starts[1].input_refs).toEqual({
      image: { artifact_id: "image-bound" },
      text: { artifact_id: "text-bound" },
    });
    await page
      .getByLabel("定位画布节点", { exact: true })
      .selectOption("input:image");
    await imageNode
      .getByRole("button", { name: "清除输入 · image", exact: true })
      .click();
    await expect(
      imageNode.getByLabel("输入 image Artifact ID", { exact: true }),
    ).toHaveValue("");
    await expect(
      textNode.getByLabel("输入 text Artifact ID", { exact: true }),
    ).toHaveValue("text-bound");
    await expect(
      page.getByRole("button", {
        name: "启动新运行 · 检查执行范围",
        exact: true,
      }),
    ).toBeDisabled();
    await page
      .getByLabel("定位画布节点", { exact: true })
      .selectOption("input:text");
    await textNode
      .getByLabel("文本输入 text", { exact: true })
      .fill("unapplied from previous workspace");
    await page.getByRole("button", { name: "input-test", exact: true }).click();
    await page.getByRole("button", { name: "继续替换", exact: true }).click();
    await expect(
      textNode.getByLabel("文本输入 text", { exact: true }),
    ).toHaveValue("");
    await expect(
      textNode.getByLabel("输入 text Artifact ID", { exact: true }),
    ).toHaveValue("");
  });
}

for (const replacementKind of ["rgb_image", "rgba_image"]) {
  test(`late upload cannot bind another workspace (${replacementKind})`, async ({
    page,
  }) => {
    let releaseUpload!: () => void;
    const uploadGate = new Promise<void>((resolve) => {
      releaseUpload = resolve;
    });
    let uploadStarted = false;
    const template = (id: string, kind: string) => ({
      id,
      label: id,
      pipeline: {
        pipeline: id,
        version: "1",
        inputs: { image: { kind } },
        nodes: {},
      },
    });
    await page.route("**/api/**", async (route) => {
      const path = new URL(route.request().url()).pathname;
      if (path === "/api/inputs/image") {
        uploadStarted = true;
        await uploadGate;
        return route.fulfill({
          json: { image_ref: { artifact_id: "obsolete-upload" } },
        });
      }
      await route.fulfill({
        json:
          path === "/api/catalog"
            ? {
                operators: {},
                adapters: [],
                templates: [
                  template("rgb-template", "rgb_image"),
                  template("rgba-template", replacementKind),
                ],
                execution_enabled: true,
              }
            : path === "/api/drafts"
              ? { drafts: [] }
              : path === "/api/compile"
                ? { ok: true, execution_ready: true }
                : { runs: [] },
      });
    });
    page.on("dialog", (dialog) => dialog.accept());
    await page.goto("/");
    await page
      .getByRole("button", { name: "rgb-template", exact: true })
      .click();
    await page.getByRole("button", { name: "继续替换", exact: true }).click();
    await page.getByLabel("上传运行图片", { exact: true }).setInputFiles({
      name: "input.png",
      mimeType: "image/png",
      buffer: Buffer.from("fixture"),
    });
    await expect.poll(() => uploadStarted).toBe(true);
    await page
      .getByRole("button", { name: "rgba-template", exact: true })
      .click();
    await page.getByRole("button", { name: "继续替换", exact: true }).click();
    releaseUpload();
    const inputNode = page.locator('.react-flow__node[data-id="input:image"]');
    await expect(inputNode).toContainText("上传期间输入契约已修改");
    await expect(inputNode).not.toContainText("obsolete-upload");
    await expect(
      page.getByRole("button", {
        name: "启动新运行 · 检查执行范围",
        exact: true,
      }),
    ).toBeDisabled();
  });
}

test("adding a mask preserves the uploaded image and removing it uses the updated binding", async ({
  page,
}) => {
  const image = { kind: "rgb_image", carriers: ["artifact_ref"] };
  await page.route("**/api/**", (route) => {
    const path = new URL(route.request().url()).pathname;
    return route.fulfill({
      json:
        path === "/api/catalog"
          ? {
              execution_enabled: true,
              adapters: [],
              templates: [],
              operators: {
                "apply_binary_mask@1": {
                  name: "apply_binary_mask",
                  version: "1",
                  inputs: { mask: { kind: "binary_mask" } },
                  outputs: {},
                },
              },
            }
          : path === "/api/drafts"
            ? { drafts: [] }
            : path === "/api/inputs/image"
              ? { image_ref: { artifact_id: "original-image" } }
              : path === "/api/compile"
                ? { ok: true, execution_ready: true }
                : { runs: [] },
    });
  });
  await page.goto("/");
  await page.getByLabel("上传运行图片", { exact: true }).setInputFiles({
    name: "image.png",
    mimeType: "image/png",
    buffer: Buffer.from("fixture"),
  });
  await expect(page.locator('[data-id="input:image"]')).toContainText(
    "original-image",
  );
  await page.getByRole("button", { name: "收起属性面板", exact: true }).click();
  await expect(page.locator('[data-id="input:image"]')).toContainText(
    "original-image",
  );
  await expect(
    page.getByRole("button", {
      name: "启动新运行 · 检查执行范围",
      exact: true,
    }),
  ).toBeVisible();
  await page.getByRole("button", { name: "展开属性面板", exact: true }).click();
  await page.getByLabel("添加输入节点").selectOption("mask");
  await page.getByLabel("定位画布节点", { exact: true }).selectOption("input:image");
  const imageRef = page.getByLabel("输入 image Artifact ID", { exact: true });
  await expect(imageRef).toHaveValue("original-image");
  await page
    .locator('[data-id="input:image"]')
    .getByText("高级：输入引用", { exact: true })
    .click();
  await imageRef.fill("replacement-image");
  await page.getByLabel("定位画布节点", { exact: true }).selectOption("input:mask");
  await page.locator('[data-id="input:mask"] .blueprint-node-header').click();
  await page.keyboard.press("Delete");
  await expect(page.locator('[data-id="input:mask"]')).toHaveCount(0);
  await expect(page.locator('[data-id="input:image"]')).toContainText(
    "replacement-image",
  );
  await expect(page.locator('[data-id="input:image"]')).not.toContainText(
    "original-image",
  );
});
