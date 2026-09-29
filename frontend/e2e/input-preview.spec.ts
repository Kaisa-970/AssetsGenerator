import { test, expect } from "@playwright/test";
const png = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=",
  "base64",
);
test("upload defaults and local thumbnails revoke URLs without starting runs", async ({
  page,
}) => {
  let starts = 0;
  await page.addInitScript(() => {
    const created: string[] = [],
      revoked: string[] = [];
    Object.assign(window, { previewUrls: { created, revoked } });
    const make = URL.createObjectURL.bind(URL),
      revoke = URL.revokeObjectURL.bind(URL);
    URL.createObjectURL = (value) => {
      const url = make(value);
      created.push(url);
      return url;
    };
    URL.revokeObjectURL = (url) => {
      revoked.push(url);
      revoke(url);
    };
  });
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/api/runs" && route.request().method() === "POST") starts++;
    const body =
      path === "/api/catalog"
        ? {
            operators: {},
            adapters: [],
            templates: [],
            execution_enabled: true,
          }
        : path === "/api/drafts"
          ? { drafts: [] }
          : path === "/api/inputs/image"
            ? { image_ref: { artifact_id: "uploaded" } }
            : { runs: [] };
    await route.fulfill({ json: body });
  });
  await page.goto("/");
  await page
    .getByLabel("定位画布节点", { exact: true })
    .selectOption("input:image");
  await expect(page.getByLabel("图片来源", { exact: true })).toHaveValue(
    "upload",
  );
  await page
    .getByLabel("上传运行图片")
    .setInputFiles({ name: "first.png", mimeType: "image/png", buffer: png });
  const image = page.getByAltText("运行图片缩略图", { exact: true });
  await expect(image).toBeVisible();
  const first = await image.getAttribute("src");
  await page
    .getByLabel("上传运行图片")
    .setInputFiles({ name: "second.png", mimeType: "image/png", buffer: png });
  await expect(image).toBeVisible();
  await expect(image).not.toHaveAttribute("src", first!);
  await expect
    .poll(() =>
      page.evaluate(
        (url) => (window as any).previewUrls.revoked.includes(url),
        first,
      ),
    )
    .toBe(true);
  const second = await image.getAttribute("src");
  if (!(await page.getByLabel("图片来源", { exact: true }).isVisible()))
    await page.getByText("高级：图片来源", { exact: true }).click();
  await page.getByLabel("图片来源", { exact: true }).selectOption("path");
  await expect(image).toHaveCount(0);
  await expect
    .poll(() =>
      page.evaluate(
        (url) => (window as any).previewUrls.revoked.includes(url),
        second,
      ),
    )
    .toBe(true);
  expect(starts).toBe(0);
});

test("multi-input image and mask thumbnails disappear when bindings change", async ({
  page,
}) => {
  const pipeline = {
    pipeline: "inputs",
    version: "1",
    inputs: {
      image: { kind: "rgb_image", carriers: ["artifact_ref"] },
      mask: { kind: "binary_mask", carriers: ["artifact_ref"] },
    },
    nodes: {},
  };
  let starts = 0;
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/api/runs" && route.request().method() === "POST") starts++;
    const body =
      path === "/api/catalog"
        ? {
            operators: {},
            adapters: [],
            templates: [{ id: "inputs", label: "inputs", pipeline }],
            execution_enabled: true,
          }
        : path === "/api/drafts"
          ? { drafts: [] }
          : path === "/api/inputs/image"
            ? { image_ref: { artifact_id: "image" } }
            : path === "/api/inputs/mask"
              ? { mask_ref: { artifact_id: "mask" } }
              : { runs: [] };
    await route.fulfill({ json: body });
  });
  page.on("dialog", (dialog) => dialog.accept());
  await page.goto("/");
  await page.getByRole("button", { name: "展开节点目录", exact: true }).click();
  await page.getByRole("button", { name: "inputs", exact: true }).click();
  await page.getByRole("button", { name: "继续替换", exact: true }).click();
  for (const name of ["image", "mask"]) {
    await page
      .getByLabel("定位画布节点", { exact: true })
      .selectOption(`input:${name}`);
    await page.getByLabel(`上传输入 ${name}`).setInputFiles({
      name: name + ".png",
      mimeType: "image/png",
      buffer: png,
    });
    await expect(
      page.getByAltText(`输入 ${name}缩略图`, { exact: true }),
    ).toBeVisible();
  }
  await page
    .locator('[data-id="input:mask"]')
    .getByText("高级：输入引用", { exact: true })
    .click();
  await page
    .getByLabel("输入 mask Artifact ID", { exact: true })
    .fill("other_mask");
  await expect(
    page.getByAltText("输入 mask缩略图", { exact: true }),
  ).toHaveCount(0);
  await expect(
    page.getByAltText("输入 image缩略图", { exact: true }),
  ).toBeVisible();
  expect(starts).toBe(0);
});

test("historical multi-input sources require explicit per-input reuse without creating a run", async ({
  page,
}) => {
  let runCreates = 0;
  const pipeline = {
    pipeline: "historical_inputs",
    version: "1",
    inputs: {
      image: { kind: "rgb_image", carriers: ["artifact_ref"] },
      text: { kind: "text", carriers: ["artifact_ref"] },
    },
    nodes: {
      shape: {
        operator: "shape_generation@1",
        inputs: {
          image: "pipeline.inputs.image",
          text: "pipeline.inputs.text",
        },
      },
    },
  };
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path === "/api/runs" && request.method() === "POST") {
      runCreates++;
      return route.fulfill({ status: 500, json: { error: "must not create" } });
    }
    let body: unknown = {};
    if (path === "/api/catalog")
      body = {
        operators: {},
        adapters: [],
        templates: [{ id: "historical", label: "历史输入", pipeline }],
        execution_enabled: true,
      };
    else if (path === "/api/drafts") body = { drafts: [] };
    else if (path === "/api/runs")
      body = { runs: [{ run_id: "dag_history", status: "succeeded" }] };
    else if (path.endsWith("/draft"))
      body = { source_run_id: "dag_history", source_plan_id: "plan", pipeline };
    else if (path.endsWith("/input-references"))
      body = {
        run_id: "dag_history",
        snapshot_ref: { artifact_id: "sha256:snapshot" },
        inputs: {
          image: {
            artifact_id: "sha256:image",
            identity: { kind: "rgb_image" },
          },
          text: { artifact_id: "sha256:text", identity: { kind: "text" } },
        },
      };
    else if (path === "/api/runs/dag_history")
      body = {
        run: {
          run_id: "dag_history",
          status: "succeeded",
          dag: {
            plan_id: "plan",
            revision: 1,
            named_actual_inputs: {
              image: { artifact_id: "sha256:image" },
              text: { artifact_id: "sha256:text" },
            },
            node_states: {},
          },
        },
        snapshot_ref: { artifact_id: "sha256:snapshot" },
        outputs: [],
        busy: false,
      };
    else if (path === "/api/compile")
      body = { ok: true, execution_ready: true };
    else if (path === "/api/inputs/text")
      body = { artifact_id: "sha256:text", text: "sofa" };
    await route.fulfill({ json: body });
  });
  await page.goto("/");
  await page
    .getByRole("button", { name: "运行记录与诊断", exact: true })
    .click();
  await page
    .getByLabel("选择运行", { exact: true })
    .selectOption("dag_history");
  await page
    .getByRole("button", { name: "将配置载入画布", exact: true })
    .click();
  await page
    .getByRole("alertdialog", { name: "确认替换画布" })
    .getByRole("button", { name: "继续替换", exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: "使用历史输入 · image", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "使用历史输入 · text", exact: true }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "使用历史输入 · image", exact: true })
    .click();
  await page
    .getByLabel("定位画布节点", { exact: true })
    .selectOption("input:text");
  await page
    .getByRole("button", { name: "使用历史输入 · text", exact: true })
    .click();
  await expect(
    page.getByText(/下游输入来源：dag_history/).first(),
  ).toBeVisible();
  expect(runCreates).toBe(0);
});
