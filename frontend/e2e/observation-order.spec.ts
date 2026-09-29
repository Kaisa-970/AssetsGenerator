import { test, expect } from "@playwright/test";

test("view reordering and removal import new ordered bundles before execution", async ({
  page,
}) => {
  const bundles: string[][] = [];
  const uploaded: string[] = [];
  let rejectNextBundle = true;
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    let body: unknown = { runs: [] };
    if (path === "/api/catalog")
      body = {
        operators: {},
        adapters: [],
        execution_enabled: true,
        templates: [
          {
            id: "views",
            label: "views",
            pipeline: {
              pipeline: "views",
              version: "1",
              inputs: { observations: { kind: "observation_bundle" } },
              nodes: {},
            },
          },
        ],
      };
    else if (path === "/api/drafts") body = { drafts: [] };
    else if (path === "/api/compile")
      body = { ok: true, execution_ready: false };
    else if (path === "/api/inputs/image") {
      const content = route.request().postDataBuffer()!.toString();
      uploaded.push(content);
      body = { image_ref: { artifact_id: content } };
    } else if (path === "/api/inputs/observations") {
      if (rejectNextBundle) {
        rejectNextBundle = false;
        await route.fulfill({
          status: 503,
          json: { error: "temporary import failure" },
        });
        return;
      }
      bundles.push(
        route
          .request()
          .postDataJSON()
          .images.map((ref: { artifact_id: string }) => ref.artifact_id),
      );
      body = { observations_ref: { artifact_id: `bundle-${bundles.length}` } };
    }
    await route.fulfill({ json: body });
  });
  page.on("dialog", (d) => d.accept());
  await page.setViewportSize({ width: 1920, height: 1080 });
  await page.goto("/");
  await page.getByRole("button", { name: "展开节点目录", exact: true }).click();
  await page.getByRole("button", { name: "views", exact: true }).click();
  await page.getByRole("button", { name: "继续替换", exact: true }).click();
  await page.getByLabel("上传多视图照片").setInputFiles(
    ["a", "b", "c"].map((name) => ({
      name: `${name}.png`,
      mimeType: "image/png",
      buffer: Buffer.from(name),
    })),
  );
  await expect(
    page.getByText("待导入 3 个视图 · 查看顺序与缩略图", { exact: true }),
  ).toBeVisible();
  await expect(page.getByLabel("观测包 Artifact ID")).toHaveValue("");
  await expect(page.getByText(/照片已保留，可重试导入/)).toBeVisible();
  await page.getByRole("button", { name: "重试导入照片", exact: true }).click();
  await expect(page.getByLabel("观测包 Artifact ID")).toHaveValue("bundle-1");
  await page
    .getByText("已导入 3 个视图 · 查看顺序与缩略图", { exact: true })
    .click();
  await page.getByRole("button", { name: "前移视图 2", exact: true }).click();
  await expect(page.getByLabel("观测包 Artifact ID")).toHaveValue("bundle-2");
  expect(bundles[1]).toEqual(["b", "a", "c"]);

  await page.getByRole("button", { name: "移除视图 2", exact: true }).click();
  await expect(page.getByLabel("观测包 Artifact ID")).toHaveValue("bundle-3");
  expect(bundles[2]).toEqual(["b", "c"]);
  await expect(
    page.getByText("已导入 2 个视图 · 查看顺序与缩略图", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "移除视图 1", exact: true }),
  ).toBeDisabled();
  expect(uploaded).toEqual([
    "a",
    "b",
    "c",
    "a",
    "b",
    "c",
    "b",
    "a",
    "c",
    "b",
    "c",
  ]);
  await page.getByText("高级：观测包引用", { exact: true }).click();
  await page.getByLabel("观测包 Artifact ID").fill("bundle-B");
  await expect(page.getByText(/已导入 2 个视图/)).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: /前移视图|后移视图/ }),
  ).toHaveCount(0);
  await expect(page.getByLabel("观测包 Artifact ID")).toHaveValue("bundle-B");
  expect(bundles).toHaveLength(3);
});
