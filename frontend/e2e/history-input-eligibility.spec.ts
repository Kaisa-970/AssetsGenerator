import { test, expect } from "@playwright/test";

test("history selector filters schema mismatch and discards late foreign-run reads", async ({
  page,
}) => {
  let release!: () => void;
  const slow = new Promise<void>((resolve) => {
    release = resolve;
  });
  let references = 0;
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
            id: "inputs",
            label: "inputs",
            pipeline: {
              pipeline: "test",
              version: "1",
              inputs: {
                image: { kind: "rgb_image", schema_name: "png" },
                mask: { kind: "binary_mask" },
              },
              nodes: {},
            },
          },
        ],
      };
    else if (path === "/api/drafts") body = { drafts: [] };
    else if (path === "/api/compile")
      body = { ok: true, execution_ready: false };
    else if (path === "/api/runs")
      body = {
        runs: ["old", "new"].map((run_id) => ({ run_id, status: "succeeded" })),
      };
    else if (path.includes("/references/")) {
      references++;
      const runId = path.includes("/old/") ? "old" : "new";
      if (runId === "old") await slow;
      body = {
        source_run_id: runId,
        node_id: "encode",
        port: "image",
        kind: "rgb_image",
        schema_name: runId === "new" ? "wrong" : "png",
        reference: { artifact_id: runId },
      };
    } else
      body = {
        run: {
          run_id: path.endsWith("old") ? "old" : "new",
          status: "succeeded",
          dag: { revision: 1, node_states: {} },
        },
        outputs: [
          {
            node_id: "encode",
            port: "image",
            kind: "rgb_image",
            url: "/image",
          },
        ],
      };
    await route.fulfill({ json: body });
  });
  page.on("dialog", (d) => d.accept());
  await page.goto("/");
  await page.getByRole("button", { name: "展开节点目录", exact: true }).click();
  await page.getByRole("button", { name: "inputs", exact: true }).click();
  await page.getByRole("button", { name: "继续替换", exact: true }).click();
  await page.getByLabel("选择运行", { exact: true }).selectOption("old");
  const node = page.locator('[data-id="input:image"]');
  await node.getByText("从历史结果选择 · image", { exact: true }).click();
  await expect.poll(() => references).toBe(1);
  await page.getByLabel("选择运行", { exact: true }).selectOption("new");
  await expect(node).toContainText("schema_name 不匹配");
  release();
  await expect(node.getByRole("button", { name: /选用 encode/ })).toHaveCount(
    0,
  );
  await expect(node.getByLabel("输入 image Artifact ID")).toHaveValue("");
  await expect(node).toContainText("来源运行：new");
});
