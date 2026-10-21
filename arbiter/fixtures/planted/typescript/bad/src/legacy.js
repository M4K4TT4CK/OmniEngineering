const { execSync } = require("node:child_process");
const axios = require("axios");

function tailLog(file) {
  return execSync("tail -n 50 " + file).toString(); // expect: arbiter/typescript.shell-command-built
}

function scheduleRefresh(name) {
  setTimeout("refresh('" + name + "')", 1000); // expect: arbiter/typescript.timer-string-code
}

function renderBanner(el, message) {
  el.innerHTML = "<b>" + message + "</b>"; // expect: arbiter/typescript.inner-html-assigned
}

async function fetchStatus() {
  const res = await axios({ url: "https://upstream.example/status", method: "get" }); // expect: arbiter/typescript.axios-no-timeout
  return res.data;
}

function warmCache(loader) {
  loader().catch(function () {}); // expect: arbiter/typescript.promise-catch-noop
}

function readConfig(path) {
  try {
    return JSON.parse(require("node:fs").readFileSync(path, "utf8"));
  } catch (e) {} // expect: arbiter/typescript.empty-catch
  return {};
}

module.exports = { tailLog, scheduleRefresh, renderBanner, fetchStatus, warmCache, readConfig };
