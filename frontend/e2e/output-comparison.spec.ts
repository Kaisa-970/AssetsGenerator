import { test, expect } from "@playwright/test";
const png = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jHwsAAAAASUVORK5CYII=",
  "base64",
);
async function fixture(
  page: import("@playwright/test").Page,
  kind = "rgb_image",
) {
  let changed = false;
  let corrupt = false;
  const prepared: any[] = [],
    starts: any[] = [],
    reads: string[] = [];
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
    else if (path === "/api/drafts") body = { drafts: [] };
    else if (path === "/api/compile")
      body = { ok: true, execution_ready: true };
    else if (path === "/api/prepare-inputs") {
      const sent = route.request().postDataJSON();
      prepared.push(sent);
      body = { input_refs: { image: sent.image_ref } };
    } else if (path === "/api/preflight")
      body = {
        digest: "comparison-checked",
        execution_ready: true,
        nodes: {},
        source_evidence_policy: "whole_snapshot_closure",
      };
    else if (path === "/api/runs" && route.request().method() === "POST") {
      starts.push(route.request().postDataJSON());
      body = {
        run: {
          run_id: "dag_new",
          status: "succeeded",
          dag: { revision: 1, node_states: {} },
        },
        outputs: [],
      };
    } else if (path === "/api/runs")
      body = {
        runs: [
          { run_id: "dag_A", status: "succeeded" },
          { run_id: "dag_B", status: "succeeded" },
        ],
      };
    else if (path.includes("/snapshot-reference/")) {
      const run = path.split("/")[3];
      reads.push(url.pathname + url.search);
      body = {
        source_run_id: corrupt ? "dag_wrong" : run,
        node_id: "encode",
        port: "image",
        source_snapshot: { artifact_id: url.searchParams.get("snapshot") },
        reference: { artifact_id: `${run}-original` },
        kind,
        schema_name: "png",
        schema_version: "1.0",
      };
    } else if (path.includes("/snapshot-output/")) {
      reads.push(url.pathname + url.search);
      return route.fulfill({ contentType: "image/png", body: png });
    } else {
      const run = path.split("/")[3];
      body = {
        run: {
          run_id: run,
          status: "succeeded",
          dag: {
            revision: changed ? 2 : 1,
            node_states: {
              encode: {
                status: "succeeded",
                attempts: [{ attempt: 1, status: "succeeded" }],
              },
            },
          },
        },
        snapshot_ref: { artifact_id: `${run}-${changed ? "new" : "snapshot"}` },
        outputs: [
          {
            node_id: "encode",
            port: "image",
            kind,
            url: `/api/runs/${run}/outputs/encode/image`,
          },
        ],
      };
    }
    await route.fulfill({ json: body });
  });
  await page.goto("/");
  await page.getByRole("button", { name: "运行", exact: true }).click();
  return {
    prepared,
    starts,
    reads,
    change: () => {
      changed = true;
    },
    corrupt: () => {
      corrupt = true;
    },
  };
}
async function add(
  page: import("@playwright/test").Page,
  run: string,
  side: string,
) {
  await page.getByLabel("选择运行").selectOption(run);
  await page
    .getByRole("button", {
      name: `加入比较 ${side} · encode · image`,
      exact: true,
    })
    .click();
  await expect(
    page.getByText(`已固定比较项 ${side}，不改变下游输入。`),
  ).toBeVisible();
}
test("two pinned previews never replace explicit A input even after current snapshots advance", async ({
  page,
}) => {
  const state = await fixture(page);
  await add(page, "dag_A", "A");
  await add(page, "dag_B", "B");
  state.change();
  await page.getByRole("button", { name: "比较两次结果", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "比较两次结果" });
  const a = dialog.getByRole("article", { name: "比较 A" }),
    b = dialog.getByRole("article", { name: "比较 B" });
  await expect(a.getByRole("img")).toBeVisible();
  await expect(b.getByRole("img")).toBeVisible();
  await a
    .getByRole("button", { name: "用作下游输入 image · A", exact: true })
    .click();
  await expect(
    a.getByText("已选作下游输入 image；绑定此运行、节点、端口及快照。", {
      exact: true,
    }),
  ).toBeVisible();
  await b
    .getByRole("button", { name: "收起图片 · encode · image", exact: true })
    .click();
  await b
    .getByRole("button", { name: "预览图片 · encode · image", exact: true })
    .click();
  await expect(b.getByRole("img")).toBeVisible();
  await expect(b.getByText(/已选作下游输入/)).toHaveCount(0);
  await expect(a.getByText(/已选作下游输入/)).toBeVisible();
  await dialog.getByRole("button", { name: "关闭比较" }).click();
  await page
    .getByRole("button", { name: "启动新运行 · 检查执行范围", exact: true })
    .click();
  await page
    .getByRole("button", { name: "确认执行上述范围", exact: true })
    .click();
  await expect.poll(() => state.starts.length).toBe(1);
  expect(state.prepared[0].image_ref).toEqual({
    artifact_id: "dag_A-original",
  });
  expect(state.starts[0].input_refs).toEqual({
    image: { artifact_id: "dag_A-original" },
  });
  expect(
    state.reads.filter((url) => url.includes("snapshot-output")),
  ).toContain(
    "/api/runs/dag_A/snapshot-output/encode/image?snapshot=dag_A-snapshot",
  );
  expect(state.reads.every((url) => !url.includes("snapshot=dag_A-new"))).toBe(
    true,
  );
});
test("foreign snapshot-reference response cannot enter comparison", async ({
  page,
}) => {
  const state = await fixture(page);
  state.corrupt();
  await page.getByLabel("选择运行").selectOption("dag_A");
  await page
    .getByRole("button", { name: "加入比较 A · encode · image", exact: true })
    .click();
  await expect(page.getByText(/比较输出来源无法核实/)).toBeVisible();
  await expect(
    page.getByRole("button", { name: "比较两次结果", exact: true }),
  ).toHaveCount(0);
  expect(state.prepared).toEqual([]);
  expect(state.starts).toEqual([]);
});
test("mask previews without a matching scalar input do not offer downstream binding", async ({
  page,
}) => {
  await fixture(page, "binary_mask");
  await add(page, "dag_A", "A");
  await page.getByRole("button", { name: "比较两次结果", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "比较两次结果" });
  await expect(dialog.getByRole("img")).toBeVisible();
  await expect(
    dialog.getByText("当前草稿没有类型、schema 与载体匹配的标量输入。", {
      exact: true,
    }),
  ).toBeVisible();
  await expect(
    dialog.getByRole("button", { name: /用作下游输入/ }),
  ).toHaveCount(0);
});
