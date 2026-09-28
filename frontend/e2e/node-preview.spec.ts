import { test, expect } from "@playwright/test";

// HTTP fixtures exercise browser presentation and read-only navigation only;
// this is not a real backend/model or asset-quality acceptance test.
test("node preview follows selected output and empty-node run status without dispatch", async ({
  page,
}) => {
  await page.setViewportSize({ width: 1920, height: 1080 });
  const nodes = Object.fromEntries(
    ["multi", "single", "empty"].map((id) => [
      id,
      {
        operator: "encode_png@1",
        inputs: { image: "pipeline.inputs.image" },
      },
    ]),
  );
  const pipeline = {
    pipeline: "preview-fixture",
    version: "1",
    inputs: { image: { kind: "rgb_image", carriers: ["artifact_ref"] } },
    nodes,
  };
  const plan = {
    static_plan: {
      nodes: Object.keys(nodes).map((node_id) => ({
        node_id,
        ...nodes[node_id],
        operator_contract_digest: "contract",
      })),
      dependencies: Object.fromEntries(
        Object.keys(nodes).map((id) => [id, []]),
      ),
    },
    bindings: Object.fromEntries(
      Object.keys(nodes).map((id) => [id, { parameters: {} }]),
    ),
  };
  let emptyStatus = "running";
  const writes: string[] = [];
  const reads: string[] = [];
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (route.request().method() !== "GET" && path !== "/api/compile")
      writes.push(path);
    let body: unknown = {};
    if (path === "/api/catalog")
      body = {
        execution_enabled: true,
        operators: {},
        adapters: [],
        templates: [{ id: "preview", label: "预览测试模板", pipeline }],
      };
    else if (path === "/api/drafts") body = { drafts: [] };
    else if (path === "/api/compile")
      body = { ok: true, execution_ready: true, bound_plan: plan };
    else if (path.endsWith("/plan")) body = { plan };
    else if (path === "/api/runs")
      body = { runs: [{ run_id: "historical", status: "running" }] };
    else if (path === "/api/runs/historical")
      body = {
        run: {
          run_id: "historical",
          status: "running",
          dag: {
            revision: 1,
            node_states: {
              multi: { status: "succeeded", attempts: [] },
              single: { status: "succeeded", attempts: [] },
              empty: { status: emptyStatus, attempts: [] },
            },
          },
        },
        outputs: [
          {
            node_id: "multi",
            port: "rgba",
            kind: "rgba_image",
            url: "/fixture-rgba.png",
          },
          {
            node_id: "multi",
            port: "mask",
            kind: "binary_mask",
            url: "/fixture-mask.png",
          },
          {
            node_id: "single",
            port: "image",
            kind: "rgb_image",
            url: "/fixture-image.png",
          },
        ],
      };
    await route.fulfill({ json: body });
  });
  await page.route("**/fixture-*.png", (route) => {
    reads.push(new URL(route.request().url()).pathname);
    return route.fulfill({
      contentType: "image/png",
      body: Buffer.from(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=",
        "base64",
      ),
    });
  });
  page.on("dialog", (dialog) => dialog.accept());
  await page.goto("/");
  await page.getByRole("button", { name: "预览测试模板", exact: true }).click();
  await page.getByRole("button", { name: "继续替换", exact: true }).click();
  await page.getByLabel("选择运行").selectOption("historical");
  const selectNode = async (id: string) => {
    await page.getByRole("button", { name: "Fit View", exact: true }).click();
    await page
      .locator(`.react-flow__node[data-id="${id}"]`)
      .locator(".blueprint-node-header")
      .click();
  };
  const preview = page.getByRole("region", { name: "选中节点预览" });
  const port = preview.getByLabel("预览输出端口");
  await selectNode("multi");
  await expect(port).toHaveValue("mask");
  await expect(preview.getByRole("img")).toHaveAttribute(
    "alt",
    "节点 multi 的 mask 输出",
  );
  await expect(preview.getByRole("img")).toBeVisible();
  await port.selectOption("rgba");
  await expect(preview.getByRole("img")).toHaveAttribute(
    "alt",
    "节点 multi 的 rgba 输出",
  );
  await expect(preview.getByRole("img")).toBeVisible();
  await preview
    .getByRole("button", { name: "放大图片 · multi · rgba", exact: true })
    .click();
  const dialog = page.getByRole("dialog", { name: "放大图片", exact: true });
  await expect(dialog).toContainText("historical / multi / rgba");
  await dialog
    .getByRole("button", { name: "关闭放大图片", exact: true })
    .click();
  await expect(dialog).toHaveCount(0);
  await selectNode("single");
  await expect(port).toHaveValue("image");
  await expect(port.locator("option")).toHaveCount(1);
  await expect(preview.getByRole("img")).toHaveAttribute(
    "alt",
    "节点 single 的 image 输出",
  );
  await expect(preview.getByRole("img")).toBeVisible();
  await selectNode("empty");
  await expect(port).toHaveValue("");
  await expect(preview.getByRole("img")).toHaveCount(0);
  await expect(preview).toContainText(
    "此节点正在运行，结果尚未发布。查看不会重复执行模型。",
  );
  await expect(
    page.getByText("执行状态：运行中", { exact: true }),
  ).toBeVisible();
  emptyStatus = "interrupted";
  await expect(preview).toContainText(
    "此节点中断，请在运行面板查看原因及允许的操作。",
  );
  emptyStatus = "succeeded";
  await expect(preview).toContainText("此节点已完成，但没有可展示的输出。");
  await selectNode("multi");
  await expect(port).toHaveValue("mask");
  await expect(preview.getByRole("img")).toHaveAttribute(
    "alt",
    "节点 multi 的 mask 输出",
  );
  await expect(preview).toContainText("来源运行：historical");
  expect(reads).toEqual(
    expect.arrayContaining([
      "/fixture-mask.png",
      "/fixture-rgba.png",
      "/fixture-image.png",
    ]),
  );
  expect(writes).toEqual([]);
});
