import { expect, test } from "@playwright/test";

test("preview can collapse to select every node at 1280x720 without remounting", async ({
  page,
}) => {
  await page.setViewportSize({ width: 1280, height: 720 });
  const pipeline = {
    pipeline: "collapse_test",
    version: "1",
    inputs: { image: { kind: "rgb_image", carriers: ["artifact_ref"] } },
    nodes: Object.fromEntries(
      ["first", "second", "third"].map((id) => [
        id,
        {
          operator: "encode_png@1",
          inputs: { image: "pipeline.inputs.image" },
        },
      ]),
    ),
  };
  let imageReads = 0;
  let writes = 0;
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (route.request().method() !== "GET" && path !== "/api/compile") writes++;
    let body: unknown = {};
    if (path === "/api/catalog")
      body = {
        execution_enabled: true,
        operators: {},
        adapters: [],
        templates: [{ id: "collapse", label: "收起预览测试", pipeline }],
      };
    else if (path === "/api/drafts") body = { drafts: [] };
    else if (path === "/api/compile")
      body = { ok: true, execution_ready: true };
    else if (path === "/api/runs")
      body = { runs: [{ run_id: "history", status: "succeeded" }] };
    else if (path === "/api/runs/history")
      body = {
        run: {
          run_id: "history",
          status: "succeeded",
          dag: { node_states: { first: { status: "succeeded" } } },
        },
        outputs: [
          {
            node_id: "first",
            port: "image",
            kind: "rgb_image",
            url: "/collapse.png",
          },
        ],
      };
    await route.fulfill({ json: body });
  });
  await page.route("**/collapse.png", (route) => {
    imageReads++;
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
  await page.getByRole("button", { name: "展开节点目录", exact: true }).click();
  await page.getByRole("button", { name: "收起预览测试", exact: true }).click();
  await page.getByRole("button", { name: "继续替换", exact: true }).click();
  await page.getByLabel("选择运行").selectOption("history");
  const expand = page.getByRole("button", {
    name: "展开节点预览",
    exact: true,
  });
  await expect(expand).toHaveAttribute("aria-expanded", "false");
  await page.getByRole("button", { name: "收起节点目录", exact: true }).click();
  for (const id of ["input:image", "third", "second", "first"]) {
    await page.getByRole("button", { name: "Fit View", exact: true }).click();
    await page.getByRole("button", { name: "Zoom Out", exact: true }).click();
    await page.getByRole("button", { name: "Zoom Out", exact: true }).click();
    await page
      .locator(`.react-flow__node[data-id="${id}"]`)
      .locator(".blueprint-node-header")
      .click();
    await expect(
      page.locator(`.react-flow__node[data-id="${id}"]`),
    ).toHaveClass(/selected/);
  }
  const collapse = page.getByRole("button", {
    name: "收起节点预览",
    exact: true,
  });
  await expand.click();
  const preview = page.getByRole("region", { name: "选中节点预览" });
  await expect(preview.getByRole("img")).toBeVisible();
  await expect(preview).toContainText("来源运行：history");
  const before = imageReads;
  await collapse.click();
  await expand.click();
  await expect(preview.getByRole("img")).toBeVisible();
  expect(imageReads).toBe(before);
  expect(writes).toBe(0);
});
