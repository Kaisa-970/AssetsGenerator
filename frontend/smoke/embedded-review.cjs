const fs = require("fs");
const { chromium } = require("@playwright/test");
(async () => {
  const config = JSON.parse(fs.readFileSync(process.argv[2]));
  const b = await chromium.launch({ headless: true });
  const p = await b.newPage({ viewport: { width: 1600, height: 1000 } });
  p.setDefaultTimeout(120000);
  const errors = [];
  p.on("pageerror", (e) => errors.push(String(e)));
  p.on("dialog", (d) => d.accept());
  await p.goto(config.url);
  await p.getByRole("button", { name: "dag-image-asset", exact: true }).click();
  await p.getByRole("button", { name: "运行", exact: true }).click();
  await p.getByLabel("运行图片路径").fill(config.image);
  const creation = p.waitForResponse(
    (response) =>
      response.url() === config.url + "/api/runs" &&
      response.request().method() === "POST",
  );
  await p.getByRole("button", { name: "启动新运行", exact: true }).click();
  const created = await (await creation).json();
  const runId = created.run.run_id;

  await p
    .getByRole("button", { name: "准备人工审查 · choose_object", exact: true })
    .click();
  await p
    .getByRole("button", { name: "在工作台审查 mask", exact: true })
    .click();
  const f = p.frameLocator('iframe[title="mask 审查 choose_object"]');
  await f.locator('input[name="proposal"]').first().check();
  await f.locator("#confirm:not([disabled])").waitFor();
  await f
    .locator("#reviewer")
    .fill("Codex embedded CPU smoke (not user approval)");
  await f.locator("#confirm").click();
  await p.getByRole("button", { name: "收起审查", exact: true }).click();
  await p
    .getByRole("link", { name: "generate_asset · glb ↗", exact: true })
    .waitFor();
  await p.getByRole("button", { name: "查看固定运行图", exact: true }).click();
  await p
    .getByRole("dialog", { name: "固定运行图" })
    .locator('.react-flow__node[data-id="generate_asset"]')
    .filter({ hasText: "succeeded" })
    .waitFor();
  await p.screenshot({ path: config.root + "/embedded-completed.png" });
  const result = await (
    await p.request.get(config.url + "/api/runs/" + runId)
  ).json();
  if (
    result.run.run_id !== runId ||
    result.run.status !== "succeeded" ||
    errors.length ||
    Object.values(result.run.dag.node_states).some(
      (state) => state.status !== "succeeded" || state.attempts.length !== 1,
    ) ||
    Object.keys(result.run.dag.receipts).length !== 1
  )
    throw Error(JSON.stringify({ result, errors }));
  fs.writeFileSync(
    config.root + "/embedded-result.json",
    JSON.stringify({ result, errors }, null, 2),
  );
  console.log(
    result.run.run_id,
    result.run.status,
    "browser errors",
    errors.length,
  );
  await b.close();
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
