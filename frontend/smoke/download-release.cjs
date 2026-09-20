// Read an existing successful run and download its release, without execution.
const fs = require("node:fs");
const assert = require("node:assert/strict");
const { chromium, expect } = require("@playwright/test");
(async () => {
  const config = JSON.parse(fs.readFileSync(process.argv[2]));
  const target = config.root + "/downloaded-release.zip";
  assert.ok(!fs.existsSync(target), "use a fresh download output");
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage({ acceptDownloads: true });
    const mutations = [];
    page.on("request", request => {
      if (request.method() !== "GET") mutations.push(request.url());
    });
    await page.goto(config.url);
    await page.getByRole("button", { name: "运行", exact: true }).click();
    await page.getByLabel("选择运行", { exact: true }).selectOption(config.run_id);
    const before = await (await page.request.get(config.url + "/api/runs/" + config.run_id)).json();
    const output = before.outputs.find(item => item.kind === "asset_release");
    assert.ok(output, "run must have a published AssetRelease");
    const link = page.getByRole("link", { name: "下载发布包 · " + output.node_id, exact: true });
    await expect(link).toHaveAttribute("href", `/api/runs/${config.run_id}/archives/${output.node_id}/${output.port}`);
    const pending = page.waitForEvent("download");
    await link.click();
    const download = await pending;
    assert.equal(await download.failure(), null);
    assert.equal(download.suggestedFilename(), `${config.run_id}-${output.node_id}.zip`);
    await download.saveAs(target);
    const response = await page.request.get(config.url + await link.getAttribute("href"));
    assert.equal(response.status(), 200);
    assert.deepEqual(fs.readFileSync(target), await response.body());
    const after = await (await page.request.get(config.url + "/api/runs/" + config.run_id)).json();
    assert.deepEqual(after.run.dag, before.run.dag);
    assert.deepEqual(mutations, []);
    fs.writeFileSync(config.root + "/download-verified.json", JSON.stringify({ run_id: config.run_id, filename: download.suggestedFilename(), bytes: fs.statSync(target).size, mutations }, null, 2), { flag: "wx" });
    console.log("browser download saved; bytes match HTTP; run unchanged");
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
