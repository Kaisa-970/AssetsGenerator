import { expect, test } from "@playwright/test";

test("single historical input reaches prepared and created inputs; upload replaces its origin", async ({
  page,
}) => {
  const starts: any[] = [];
  const prepared: any[] = [];
  const pipeline = {
    pipeline: "single_history",
    version: "1",
    inputs: { image: { kind: "rgb_image", carriers: ["artifact_ref"] } },
    nodes: {},
  };
  const envelope = (id: string) => ({
    run: {
      run_id: id,
      status: "succeeded",
      dag: { plan_id: "plan", revision: 1, node_states: {} },
    },
    outputs: [],
    ...(id === "history" ? { snapshot_ref: { artifact_id: "snapshot" } } : {}),
  });
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
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
    else if (path === "/api/runs" && request.method() === "GET")
      body = { runs: [{ run_id: "history", status: "succeeded" }] };
    else if (path.endsWith("/draft"))
      body = { source_run_id: "history", source_plan_id: "plan", pipeline };
    else if (path.endsWith("/input-references"))
      body = {
        run_id: "history",
        snapshot_ref: { artifact_id: "snapshot" },
        inputs: {
          image: {
            artifact_id: "historical-image",
            identity: { kind: "rgb_image" },
          },
        },
      };
    else if (path === "/api/prepare-inputs") {
      const sent = request.postDataJSON();
      prepared.push(sent);
      body = { input_refs: { image: sent.image_ref } };
    } else if (path === "/api/preflight")
      body = {
        digest: "checked",
        execution_ready: true,
        nodes: {},
        source_evidence_policy: "whole_snapshot_closure",
      };
    else if (path === "/api/runs" && request.method() === "POST") {
      starts.push(request.postDataJSON());
      body = envelope("new");
    } else if (path === "/api/inputs/image")
      body = { image_ref: { artifact_id: "replacement-image" } };
    else body = envelope(path.split("/")[3]);
    await route.fulfill({ json: body });
  });
  await page.goto("/");
  await page.getByRole("button", { name: "运行", exact: true }).click();
  await page.getByLabel("选择运行", { exact: true }).selectOption("history");
  await page
    .getByRole("button", { name: "将配置载入画布", exact: true })
    .click();
  await page.getByRole("button", { name: "继续替换", exact: true }).click();
  const node = page.locator('.react-flow__node[data-id="input:image"]');
  await expect(node).toContainText("历史来源：运行 history");
  await node
    .getByRole("button", { name: "使用历史输入 · image", exact: true })
    .click();
  await expect(node).toContainText("下游输入来源：history");
  await page
    .getByLabel("复用所选运行的有效节点结果（后端核对身份与证据）")
    .uncheck();
  const start = page.getByRole("button", {
    name: "启动新运行 · 检查执行范围",
    exact: true,
  });
  await start.click();
  await expect.poll(() => starts.length).toBe(1);
  expect(prepared[0].image_ref).toEqual({ artifact_id: "historical-image" });
  expect(starts[0].input_refs).toEqual({
    image: { artifact_id: "historical-image" },
  });
  expect(starts[0].preflight_digest).toBe("checked");
  await node.getByRole("button", { name: "改为上传图片", exact: true }).click();
  await expect(node).not.toContainText("下游输入来源：history");
  await expect(start).toBeDisabled();
  await node
    .getByLabel("上传运行图片", { exact: true })
    .setInputFiles({
      name: "replacement.png",
      mimeType: "image/png",
      buffer: Buffer.from("fixture"),
    });
  await expect(node).toContainText("replacement.png");
  await expect(node).not.toContainText("下游输入来源：history");
  await start.click();
  await expect.poll(() => starts.length).toBe(2);
  expect(starts[1].input_refs).toEqual({
    image: { artifact_id: "replacement-image" },
  });
});
