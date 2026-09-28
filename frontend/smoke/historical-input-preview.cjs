// Actual CPU service, existing successful run: URL RUN_ID FRESH_EVIDENCE_DIRECTORY.
const fs = require('node:fs');
const assert = require('node:assert/strict');
const { chromium, expect } = require('@playwright/test');
(async () => {
  const [url, id, directory] = process.argv.slice(2);
  assert.ok(url && id && directory);
  fs.mkdirSync(directory, { recursive: false });
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  const starts = [], errors = [], reads = [];
  page.on('pageerror', error => errors.push(String(error)));
  page.on('request', request => {
    if (request.url().includes('/snapshot-input-image/')) reads.push(request.url());
    if (new URL(request.url()).pathname === '/api/runs' && request.method() === 'POST') starts.push(request.postDataJSON());
  });
  try {
    const beforeResponse = await page.request.get(`${url}/api/runs/${id}`);
    assert.equal(beforeResponse.status(), 200);
    const before = await beforeResponse.json();
    await page.goto(url);
    await page.getByLabel('选择运行', { exact: true }).selectOption(id);
    await page.getByRole('button', { name: '运行记录与诊断', exact: true }).click();
    await page.getByRole('button', { name: '将配置载入画布', exact: true }).click();
    await page.getByRole('button', { name: '继续替换', exact: true }).click();
    await page.getByLabel('定位画布节点', { exact: true }).selectOption('input:image');
    const node = page.locator('[data-id="input:image"]');
    await node.getByRole('button', { name: '使用历史输入 · image', exact: true }).click();
    const preview = page.getByRole('region', { name: '当前输入预览', exact: true });
    const image = preview.locator('img');
    await expect(image).toBeVisible();
    await expect.poll(() => image.evaluate(el => el.naturalWidth)).toBeGreaterThan(0);
    const decoded = await image.evaluate(el => ({ width: el.naturalWidth, height: el.naturalHeight, url: el.src }));
    assert.ok(reads.some(url => url.includes(encodeURIComponent(id))), "Decoded image must be fetched from the pinned source run");
    const visibility = await image.evaluate(el => {
      const rect = el.getBoundingClientRect();
      const window = el.closest('.node-preview-window');
      const bounds = window.getBoundingClientRect();
      return { scrollTop: window.scrollTop, imageHeight: rect.height,
        visibleHeight: Math.max(0, Math.min(rect.bottom, bounds.bottom, innerHeight) - Math.max(rect.top, bounds.top, 0)) };
    });
    assert.equal(visibility.scrollTop, 0, 'Preview must not need programmatic scrolling');
    assert.ok(visibility.visibleHeight >= 60, `Image must be visible in persistent preview: ${JSON.stringify(visibility)}`);
    assert.deepEqual(starts, [], 'Selecting/previewing history must not create a run');
    assert.deepEqual(errors, []);
    const after = await (await page.request.get(`${url}/api/runs/${id}`)).json();
    assert.deepEqual(after.run.dag.node_states, before.run.dag.node_states);
    await page.screenshot({ path: `${directory}/historical-input.png` });
    fs.writeFileSync(`${directory}/acceptance.json`, JSON.stringify({ sourceRun: id, sourceInput: before.run.dag.named_actual_inputs.image, decoded, visibility, reads, starts, errors }, null, 2));
    console.log('PASS: explicitly bound historical input decoded; no execution or history mutation');
  } catch (error) {
    await page.screenshot({ path: `${directory}/failure.png` });
    throw error;
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
