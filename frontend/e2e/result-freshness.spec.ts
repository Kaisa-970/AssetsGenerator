import { test, expect } from "@playwright/test";

test("edited configuration marks kept historical preview stale without dispatch", async ({
  page,
}) => {
  const pipeline = {
    pipeline: "freshness",
    version: "1",
    inputs: { image: { kind: "rgb_image", carriers: ["artifact_ref"] } },
    nodes: {
      encode: {
        operator: "encode_png@1",
        inputs: { image: "pipeline.inputs.image" },
      },
    },
  };
  const node = {
    node_id: "encode",
    inputs: { image: "pipeline.inputs.image" },
    operator_contract_digest: "contract",
  };
  const old = {
    static_plan: { nodes: [node], dependencies: { encode: [] } },
    bindings: { encode: { parameters: {} } },
  };
  let changed = false;
  const pageErrors: string[] = [];
  page.on("pageerror", (error) => pageErrors.push(String(error)));
  page.on("console", (message) => {
    if (message.type() === "error") pageErrors.push(message.text());
  });
  let starts = 0;
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    let body: unknown = {};
    if (path === "/api/catalog")
      body = {
        execution_enabled: true,
        operators: {},
        adapters: [],
        templates: [{ id: "freshness", label: "freshness", pipeline }],
      };
    else if (path === "/api/drafts") body = { drafts: [] };
    else if (path === "/api/compile")
      body = {
        ok: true,
        execution_ready: true,
        bound_plan: changed
          ? { ...old, bindings: { encode: { parameters: { changed: true } } } }
          : old,
      };
    else if (path.endsWith("/plan")) body = { plan: old };
    else if (path === "/api/runs") {
      if (route.request().method() === "POST") starts++;
      body = { runs: [{ run_id: "old", status: "succeeded" }] };
    } else
      body = {
        snapshot_ref: { artifact_id: "old-snapshot" },
        run: {
          run_id: "old",
          status: "succeeded",
          dag: {
            revision: 1,
            node_states: { encode: { status: "succeeded", attempts: [] } },
          },
        },
        outputs: [
          {
            node_id: "encode",
            port: "image",
            kind: "rgb_image",
            url: "/fixture.png",
          },
        ],
      };
    await route.fulfill({ json: body });
  });
  await page.route("**/fixture.png", (route) =>
    route.fulfill({
      contentType: "image/png",
      body: Buffer.from(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=",
        "base64",
      ),
    }),
  );
  page.on("dialog", (d) => d.accept());
  await page.goto("/");
  await page.getByRole("button", { name: "freshness", exact: true }).click();
  await page.getByRole("button", { name: "继续替换", exact: true }).click();
  await page.getByLabel("选择运行").selectOption("old");
  await page.getByLabel("定位画布节点", { exact: true }).selectOption("encode");
  const preview = page.getByRole("region", { name: "选中节点预览" });
  await expect(preview.getByRole("img")).toBeVisible();
  await preview
    .getByRole("button", { name: "放大图片 · encode · image", exact: true })
    .click();
  const enlarged = page.getByRole("dialog", { name: "放大图片", exact: true });
  await expect(enlarged).toBeVisible();
  await expect(enlarged).toContainText("old / encode / image");
  await expect(enlarged.getByRole("img")).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(enlarged).toHaveCount(0);
  await expect(preview.getByRole("img")).toBeVisible();

  await expect(preview.getByLabel("预览配置状态")).toContainText("配置匹配");
  changed = true;
  await page.getByLabel("管线名称", { exact: true }).fill("changed");
  await expect(preview.getByLabel("预览配置状态")).toContainText("结果过期");
  const badge = page.locator(
    '.react-flow__node[data-id="encode"] .blueprint-stale-status',
  );
  await expect(badge).toHaveText("需更新");
  await expect(preview.getByRole("img")).toBeVisible();
  await expect(
    page.getByRole("region", { name: "配置变化影响" }),
  ).toContainText("encode");
  await expect(page.getByText("执行状态：完成", { exact: true })).toBeVisible();
  await expect(
    page.getByRole("button", {
      name: "重新执行受影响节点 · 检查执行范围",
      exact: true,
    }),
  ).toBeVisible();
  expect(starts).toBe(0);
  changed = false;
  await page.getByLabel("管线名称", { exact: true }).fill("freshness");
  await expect(preview.getByLabel("预览配置状态")).toContainText("配置匹配");
  await expect(badge).toHaveCount(0);
  await expect(preview.getByRole("img")).toBeVisible();
  expect(
    pageErrors.filter((message) =>
      /Maximum update depth|Too many re-renders/.test(message),
    ),
  ).toEqual([]);
});
