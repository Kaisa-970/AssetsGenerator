import { test, expect } from "@playwright/test";

test("continue extraction checks fixed snapshot and confirms exact independent creation", async ({
  page,
}) => {
  const snapshot = { artifact_id: "snapshot_original" };
  const pipeline = {
    pipeline: "continued",
    version: "1",
    inputs: { image: { kind: "rgb_image" } },
    nodes: {
      segment: { operator: "text_segmentation@1", inputs: {} },
      extract: { operator: "apply_binary_mask@1", inputs: {} },
    },
  };
  const prepared = {
    pipeline,
    input_refs: { image: { artifact_id: "source_image" } },
    reuse_source: snapshot,
    preflight_digest: "checked_scope",
  };
  const starts: any[] = [],
    gets: string[] = [];
  await page.route("**/api/**", async (route) => {
    const u = new URL(route.request().url()),
      path = u.pathname;
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
      body = { ok: true, execution_ready: true, plan: {} };
    else if (path.includes("/continue-extraction/")) {
      gets.push(u.searchParams.get("snapshot")!);
      body = {
        eligible: true,
        ...prepared,
        source_run_id: "source_run",
        source_node_id: "segment",
        extract_node_id: "extract",
        preflight: {
          digest: "checked_scope",
          execution_ready: true,
          nodes: {
            segment: {
              status: "reuse",
              detail: "verified",
              reason: "identity",
            },
            extract: {
              status: "execute",
              detail: "new extraction",
              reason: "new",
            },
          },
        },
      };
    } else if (path === "/api/runs" && route.request().method() === "POST") {
      starts.push(route.request().postDataJSON());
      body = {
        run: {
          run_id: "new_run",
          status: "succeeded",
          dag: { revision: 1, node_states: {} },
        },
      };
    } else if (path === "/api/runs")
      body = { runs: [{ run_id: "source_run", status: "succeeded" }] };
    else
      body = {
        snapshot_ref: snapshot,
        run: {
          run_id: "source_run",
          status: "succeeded",
          dag: {
            revision: 3,
            node_states: { segment: { status: "succeeded", attempts: [] } },
          },
        },
        outputs: [
          {
            node_id: "segment",
            port: "mask",
            kind: "binary_mask",
            url: "/mask.png",
          },
        ],
      };
    await route.fulfill({ json: body });
  });
  await page.goto("/");
  await page.getByRole("button", { name: "运行", exact: true }).click();
  await page.getByLabel("选择运行", { exact: true }).selectOption("source_run");
  await page
    .getByRole("button", { name: "继续提取 · segment", exact: true })
    .click();
  await expect(page.getByText(/新运行只运行提取，不再运行分割/)).toBeVisible();
  expect(gets).toEqual([snapshot.artifact_id]);
  expect(starts).toEqual([]);
  await expect(
    page.getByText("segment · 将复用 · verified", { exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "确认继续提取", exact: true }).click();
  await expect.poll(() => starts.length).toBe(1);
  expect(starts[0]).toEqual({
    ...prepared,
    idempotency_key: expect.stringMatching(/^[a-f0-9-]{36}$/),
  });
  await expect(page.getByLabel("管线名称")).toHaveValue("my_asset_pipeline");
});

test("switching runs discards delayed continuation and refusals never create", async ({
  page,
}) => {
  let release: () => void = () => {};
  let requested = false;
  let starts = 0;
  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url()),
      path = url.pathname;
    let body: any = {};
    if (path === "/api/catalog")
      body = {
        operators: {},
        adapters: [],
        templates: [],
        execution_enabled: true,
      };
    else if (path === "/api/compile")
      body = { ok: true, execution_ready: true, plan: {} };
    else if (path === "/api/drafts") body = { drafts: [] };
    else if (path.includes("/continue-extraction/")) {
      if (path.includes("/a/")) {
        requested = true;
        await new Promise<void>((resolve) => {
          release = resolve;
        });
      }
      body = { eligible: false, reason: "人工决定不能自动继承" };
    } else if (path === "/api/runs" && route.request().method() === "POST") {
      starts++;
    } else if (path === "/api/runs")
      body = {
        runs: [
          { run_id: "a", status: "succeeded" },
          { run_id: "b", status: "succeeded" },
        ],
      };
    else {
      const runId = path.split("/").at(-1);
      body = {
        snapshot_ref: { artifact_id: `snapshot_${runId}` },
        run: {
          run_id: runId,
          status: "succeeded",
          dag: {
            revision: 1,
            node_states: { segment: { status: "succeeded", attempts: [] } },
          },
        },
        outputs: [
          {
            node_id: "segment",
            port: "mask",
            kind: "binary_mask",
            url: "/mask.png",
          },
        ],
      };
    }
    await route.fulfill({ json: body });
  });
  await page.goto("/");
  await page.getByRole("button", { name: "运行", exact: true }).click();
  await page.getByLabel("选择运行", { exact: true }).selectOption("a");
  await page
    .getByRole("button", { name: "继续提取 · segment", exact: true })
    .click();
  await expect.poll(() => requested).toBe(true);
  await page.getByLabel("选择运行", { exact: true }).selectOption("b");
  release();
  await expect(
    page.getByRole("button", { name: "继续提取 · segment", exact: true }),
  ).toBeEnabled();
  await expect(
    page.getByRole("button", { name: "确认继续提取", exact: true }),
  ).toHaveCount(0);
  await page
    .getByRole("button", { name: "继续提取 · segment", exact: true })
    .click();
  await expect(
    page.getByText("人工决定不能自动继承", { exact: true }),
  ).toBeVisible();
  expect(starts).toBe(0);
});
