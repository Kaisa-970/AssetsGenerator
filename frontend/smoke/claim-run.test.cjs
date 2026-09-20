const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { spawnSync } = require("node:child_process");
const { claimRun } = require("./claim-run.cjs");

for (const existing of ["browser-submitted.json", "embedded-result.json", "browser-smoke-started.json"]) {
  test(`existing ${existing} blocks before browser launch`, () => {
    const root = fs.mkdtempSync(path.join(os.tmpdir(), "smoke-claim-"));
    try {
      fs.writeFileSync(path.join(root, existing), "original evidence");
      // Invalid URL and absent image must never be reached.
      const config = path.join(root, "config.json");
      fs.writeFileSync(config, JSON.stringify({root, url: "invalid", image: "/missing"}));
      const result = spawnSync(process.execPath, [path.join(__dirname, "embedded-review.cjs"), config], {encoding: "utf8"});
      assert.equal(result.status, 1);
      assert.match(result.stderr, /Existing smoke evidence|EEXIST/);
      assert.equal(fs.readFileSync(path.join(root, existing), "utf8"), "original evidence");
    } finally { fs.rmSync(root, {recursive: true, force: true}); }
  });
}
test("claim survives a new process and rejects replay", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "smoke-claim-"));
  try {
    claimRun(root);
    const before = fs.readFileSync(path.join(root, "browser-smoke-started.json"));
    assert.throws(() => claimRun(root), /EEXIST/);
    assert.deepEqual(fs.readFileSync(path.join(root, "browser-smoke-started.json")), before);
  } finally { fs.rmSync(root, {recursive: true, force: true}); }
});
