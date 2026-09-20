// A failed smoke may already have submitted work. Never implicitly replay it.
const fs = require("node:fs");
const path = require("node:path");

function claimRun(root) {
  for (const name of ["browser-submitted.json", "embedded-result.json"]) {
    if (fs.existsSync(path.join(root, name))) {
      throw Error("Existing smoke evidence; inspect the original run before starting another.");
    }
  }
  const marker = path.join(root, "browser-smoke-started.json");
  const fd = fs.openSync(marker, "wx");
  try {
    fs.writeFileSync(fd, JSON.stringify({ started: new Date().toISOString() }));
    fs.fsyncSync(fd);
  } finally {
    fs.closeSync(fd);
  }
  const directory = fs.openSync(root, "r");
  try { fs.fsyncSync(directory); } finally { fs.closeSync(directory); }
}
module.exports = { claimRun };
