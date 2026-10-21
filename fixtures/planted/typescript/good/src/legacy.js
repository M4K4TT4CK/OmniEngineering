const { execFileSync } = require("node:child_process");
const axios = require("axios");

axios.defaults.timeout = 5000;

function tailLog(file) {
  return execFileSync("tail", ["-n", "50", file]).toString();
}

function scheduleRefresh(name) {
  setTimeout(() => refresh(name), 1000);
}

function renderBanner(el, message) {
  el.textContent = message;
}

function clearBanner(el) {
  el.innerHTML = "";
}

async function fetchStatus() {
  const res = await axios({ url: "https://upstream.example/status", method: "get" });
  return res.data;
}

function warmCache(loader) {
  loader().catch((err) => console.error("cache warm-up failed", err));
}

function readConfig(path) {
  try {
    return JSON.parse(require("node:fs").readFileSync(path, "utf8"));
  } catch (e) {
    if (e.code !== "ENOENT") throw e;
  }
  return {};
}

function refresh(name) {
  return name;
}

module.exports = { tailLog, scheduleRefresh, renderBanner, clearBanner, fetchStatus, warmCache, readConfig };
