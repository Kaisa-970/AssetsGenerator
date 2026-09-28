import { expect, test } from "@playwright/test";

test("template opens execution, parameter changes compile automatically and old results stay discarded", async ({
  page,
}) => {
  const pipeline = {
    pipeline: "auto_test",
    version: "1",
    inputs: { image: { kind: "rgb_image", carriers: ["artifact_ref"] } },
    nodes: {
      resize: {
        operator: "resize_image@1",
        adapter: "resize_image@1",
        parameters: { width: 4 },
        inputs: { image: "pipeline.inputs.image" },
      },
    },
  };
  const requests: any[] = [];
  let releaseOld: (() => void) | undefined;
  let holdNext = false;
  let starts = 0;
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    let body: unknown = {};
    if (path === "/api/catalog")
      body = {
        execution_enabled: true,
        operators: {
          "resize_image@1": {
            name: "resize_image",
            version: "1",
            inputs: {
              image: { kinds: ["rgb_image"], carriers: ["artifact_ref"] },
            },
            outputs: {
              image: { kinds: ["rgb_image"], carriers: ["artifact_ref"] },
            },
          },
        },
        adapters: [
          {
            name: "resize_image",
            version: "1",
            operators: ["resize_image@1"],
            defaults: { width: 4 },
            parameter_schema: {
              type: "object",
              properties: { width: { type: "integer", minimum: 1 } },
              additionalProperties: false,
            },
          },
        ],
        templates: [{ id: "cpu", label: "CPU 自动检查", pipeline }],
      };
    else if (path === "/api/drafts") body = { drafts: [] };
    else if (path === "/api/compile") {
      requests.push(route.request().postDataJSON().pipeline);
      if (holdNext) {
        holdNext = false;
        await new Promise<void>((resolve) => {
          releaseOld = resolve;
        });
        body = {
          ok: true,
          execution_ready: false,
          execution_reason: "OLD STALE RESPONSE",
        };
      } else body = { ok: true, execution_ready: true, plan: {} };
    } else if (path === "/api/runs") {
      if (route.request().method() === "POST") starts++;
      body = { runs: [] };
    }
    await route.fulfill({ json: body }).catch(() => {});
  });
  page.on("dialog", (dialog) => dialog.accept());
  await page.goto("/");
  await page.getByRole("button", { name: "CPU 自动检查", exact: true }).click();
  if (await page.getByRole("button", { name: "继续替换", exact: true }).count())
    await page.getByRole("button", { name: "继续替换", exact: true }).click();
  await expect(page.getByLabel("当前图片来源", { exact: true })).toBeVisible();
  await expect(page.getByLabel("当前配置编译状态")).toContainText("编译通过");
  await expect
    .poll(() => requests.some((p) => p.pipeline === "auto_test"))
    .toBe(true);
  expect(starts).toBe(0);
  await page.getByLabel("定位画布节点", { exact: true }).selectOption("resize");
  await page.getByRole("button", { name: "配置", exact: true }).click();
  const width = page
    .locator(".inspector")
    .getByLabel("参数 width", { exact: true });
  await expect(width).toBeVisible();
  holdNext = true;
  await width.fill("5");
  await width.press("Tab");
  await expect.poll(() => !!releaseOld).toBe(true);
  await expect(page.getByLabel("当前配置编译状态")).toContainText("正在检查");
  await width.fill("6");
  await width.press("Tab");
  await expect
    .poll(() => requests.some((p) => p.nodes.resize?.parameters.width === 6))
    .toBe(true);
  await expect(page.getByLabel("当前配置编译状态")).toContainText("配置可执行");
  releaseOld!();
  await page.waitForTimeout(300);
  await expect(page.getByLabel("当前配置编译状态")).not.toContainText(
    "OLD STALE",
  );
  expect(starts).toBe(0);
});
