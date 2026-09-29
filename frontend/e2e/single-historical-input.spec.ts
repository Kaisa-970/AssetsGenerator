import { expect, test } from "@playwright/test";

test("single historical input reaches prepared and created inputs; upload replaces its origin", async ({
  page,
}) => {
  let mismatch = false;
  let holdReference = false;
  let lateResponseDelivered = false;
  let releaseReference: (() => void) | undefined;
  let imageReads = 0;
  let outputReads = 0;
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
    if (path.includes("snapshot-output") || path.includes("snapshot-reference"))
      outputReads++;
    if (path.endsWith("/snapshot-input-image/image")) {
      imageReads++;
      expect(new URL(request.url()).searchParams.get("snapshot")).toBe(
        "snapshot",
      );
      return route.fulfill({
        contentType: "image/png",
        body: Buffer.from(
          "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=",
          "base64",
        ),
      });
    }
    const isHeldReference =
      path.endsWith("/snapshot-input-reference/image") && holdReference;
    if (isHeldReference) {
      await new Promise<void>((resolve) => {
        releaseReference = resolve;
      });
    }
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
    else if (path.endsWith("/snapshot-input-reference/image"))
      body = {
        source_run_id: "history",
        input_name: "image",
        kind: "rgb_image",
        reference: {
          artifact_id: mismatch ? "wrong-image" : "historical-image",
        },
        source_snapshot: { artifact_id: "snapshot" },
      };
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
    if (isHeldReference) lateResponseDelivered = true;
  });
  await page.goto("/");
  await page.getByRole("button", { name: "运行记录与诊断", exact: true }).click();
  await page.getByLabel("选择运行", { exact: true }).selectOption("history");
  await page
    .getByRole("button", { name: "将配置载入画布", exact: true })
    .click();
  await page.getByRole("button", { name: "继续替换", exact: true }).click();
  await page.getByRole("button", { name: "收起节点目录", exact: true }).click();
  const node = page.locator('.react-flow__node[data-id="input:image"]');
  await expect(node).toContainText("历史来源：运行 history");
  await node
    .getByRole("button", { name: "使用历史输入 · image", exact: true })
    .click();
  await expect(node).toContainText("下游输入来源：history");
  await expect(node.getByLabel("当前图片来源", { exact: true })).toContainText(
    "历史运行的原始输入",
  );
  await expect(node).not.toContainText("明确选用一个输出");
  await page
    .getByLabel("定位画布节点", { exact: true })
    .selectOption("input:image");
  await page.getByRole("button", { name: "展开节点预览", exact: true }).click();
  await expect(
    page.getByText("固定历史来源：history / input:image / image"),
  ).toBeVisible();
  await expect.poll(() => imageReads).toBeGreaterThan(0);
  expect(outputReads).toBe(0);
  for (const viewport of [
    { width: 1440, height: 900 },
    { width: 1920, height: 1080 },
  ]) {
    await page.setViewportSize(viewport);
    await expect(
      page.getByRole("button", { name: "放大图片", exact: true }),
    ).toBeVisible();
    const preview = page.getByLabel("当前输入预览", { exact: true });
    await expect(preview.locator("img")).toBeVisible();
    const geometry = await preview.evaluate((element) => {
      const dock = element.closest(".node-preview-window")!;
      const image = element.querySelector("img")!;
      const button = [...element.querySelectorAll("button")].find(
        (b) => b.textContent === "放大图片",
      )!;
      const bounds = (e: Element) => {
        const r = e.getBoundingClientRect();
        return { top: r.top, bottom: r.bottom, left: r.left, right: r.right };
      };
      return {
        dock: bounds(dock),
        image: bounds(image),
        button: bounds(button),
        scroll: dock.scrollTop,
      };
    });
    expect(geometry.scroll).toBe(0);
    for (const item of [geometry.image, geometry.button]) {
      expect(item.top).toBeGreaterThanOrEqual(geometry.dock.top);
      expect(item.bottom).toBeLessThanOrEqual(geometry.dock.bottom);
      expect(item.left).toBeGreaterThanOrEqual(geometry.dock.left);
      expect(item.right).toBeLessThanOrEqual(geometry.dock.right);
    }
  }

  // Re-select the original input after changing the binding; a mismatched response
  // must never expose an image or retain the old preview.
  await node.getByRole("button", { name: "改为上传图片", exact: true }).click();
  holdReference = true;
  await node
    .getByRole("button", { name: "使用历史输入 · image", exact: true })
    .click();
  await expect.poll(() => Boolean(releaseReference)).toBe(true);
  await node.getByRole("button", { name: "改为上传图片", exact: true }).click();
  await node.getByLabel("上传运行图片", { exact: true }).setInputFiles({
    name: "late-response-replacement.png",
    mimeType: "image/png",
    buffer: Buffer.from(
      "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=",
      "base64",
    ),
  });
  await expect(node).toContainText("late-response-replacement.png");
  const readsBeforeLateResponse = imageReads;
  releaseReference!();
  holdReference = false;
  await expect.poll(() => lateResponseDelivered).toBe(true);
  await page.evaluate(
    () =>
      new Promise<void>((resolve) =>
        requestAnimationFrame(() => requestAnimationFrame(() => resolve())),
      ),
  );
  await expect(
    page.getByText("固定历史来源：history / input:image / image"),
  ).toHaveCount(0);
  await expect(
    page.getByAltText("当前输入 image缩略图", { exact: true }),
  ).toBeVisible();
  expect(imageReads).toBe(readsBeforeLateResponse);
  await expect(node).not.toContainText("下游输入来源：history");
  mismatch = true;
  await node
    .getByRole("button", { name: "使用历史输入 · image", exact: true })
    .click();
  await expect(
    page.getByText("无法核实当前输入的历史图片；绑定未改变，请重新核实来源。"),
  ).toBeVisible();
  const readsBeforeRetry = imageReads;
  mismatch = false;
  await page
    .getByRole("button", { name: "重新读取输入预览", exact: true })
    .click();
  await expect.poll(() => imageReads).toBeGreaterThan(readsBeforeRetry);

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
  await node.getByLabel("上传运行图片", { exact: true }).setInputFiles({
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
