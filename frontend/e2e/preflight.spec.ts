import { test, expect } from "@playwright/test";

async function setup(page: import("@playwright/test").Page) {
  const starts: any[] = [];
  const checks: any[] = [];
  let conflict = false;
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    const method = route.request().method();
    let body: any = {};
    if (path === "/api/catalog")
      body = {
        operators: {},
        adapters: [],
        templates: [],
        execution_enabled: true,
      };
    else if (path === "/api/drafts") body = { drafts: [] };
    else if (path === "/api/compile")
      body = { ok: true, execution_ready: true };
    else if (path === "/api/inputs/image")
      body = { image_ref: { artifact_id: "uploaded" } };
    else if (path === "/api/prepare-inputs") {
      const sent = route.request().postDataJSON();
      body = {
        input_refs: {
          image: sent.image_ref || { artifact_id: "prepared-path" },
        },
      };
    } else if (path === "/api/preflight") {
      checks.push(route.request().postDataJSON());
      body = {
        digest: "digest-1",
        execution_ready: true,
        nodes: {
          encode: {
            status: "execute",
            reason: "no_verified_reuse",
            detail: "No previous result",
          },
        },
        source_evidence_policy: "whole_snapshot_closure",
      };
    } else if (path === "/api/runs" && method === "POST") {
      starts.push(route.request().postDataJSON());
      if (conflict)
        return route.fulfill({
          status: 409,
          json: {
            code: "preflight_changed",
            error: "条件已变化，请重新检查",
            preflight: {},
          },
        });
      body = {
        run: {
          run_id: "dag_checked",
          status: "succeeded",
          dag: { revision: 1, node_states: {} },
        },
        outputs: [],
      };
    } else if (path === "/api/runs") body = { runs: [] };
    else
      body = {
        run: {
          run_id: "dag_checked",
          status: "succeeded",
          dag: { revision: 1, node_states: {} },
        },
        outputs: [],
      };
    await route.fulfill({ json: body });
  });
  await page.goto("/");
  await page.getByRole("button", { name: "运行", exact: true }).click();
  await page
    .getByLabel("上传运行图片")
    .setInputFiles({
      name: "input.png",
      mimeType: "image/png",
      buffer: Buffer.from("fixture"),
    });
  return {
    starts,
    checks,
    reject: () => {
      conflict = true;
    },
  };
}

test("preflight prepares exact inputs and requires explicit confirmation before creation", async ({
  page,
}) => {
  const { starts, checks } = await setup(page);
  await page.getByRole("button", { name: "启动新运行 · 检查执行范围" }).click();
  await expect(
    page.getByText("encode · 将执行", { exact: false }),
  ).toBeVisible();
  expect(starts).toHaveLength(0);
  expect(checks[0].input_refs).toEqual({ image: { artifact_id: "uploaded" } });
  await page.getByRole("button", { name: "确认执行上述范围" }).click();
  await expect.poll(() => starts.length).toBe(1);
  expect(starts[0].preflight_digest).toBe("digest-1");
  expect(starts[0].input_refs).toEqual(checks[0].input_refs);
  expect(starts[0]).not.toHaveProperty("image_path");
});

test("changed intent invalidates checked scope and never creates a run", async ({
  page,
}) => {
  const { starts } = await setup(page);
  await page.getByRole("button", { name: "启动新运行 · 检查执行范围" }).click();
  await expect(
    page.getByRole("button", { name: "确认执行上述范围" }),
  ).toBeVisible();
  await page.getByLabel("管线名称", { exact: true }).fill("different_pipeline");
  await expect(
    page.getByRole("button", { name: "确认执行上述范围" }),
  ).toHaveCount(0);
  await expect(
    page.getByText("输入、配置或来源已变化，请重新检查。"),
  ).toBeVisible();
  expect(starts).toHaveLength(0);
});

test("a definite changed-preflight rejection does not leave an unknown creation receipt", async ({
  page,
}) => {
  const state = await setup(page);
  await page.getByRole("button", { name: "启动新运行 · 检查执行范围" }).click();
  state.reject();
  await page.getByRole("button", { name: "确认执行上述范围" }).click();
  await expect.poll(() => state.starts.length).toBe(1);
  await expect
    .poll(() =>
      page.evaluate(() =>
        sessionStorage.getItem("assets-generator:pending-creation:v1"),
      ),
    )
    .toBeNull();
  await expect(
    page.getByRole("button", { name: "确认执行上述范围" }),
  ).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "启动新运行 · 检查执行范围" }),
  ).toBeEnabled();
});
