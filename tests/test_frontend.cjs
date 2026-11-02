"use strict";

const assert = require("node:assert").strict;
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const tests = [];
const test = (name, run) => tests.push({ name, run });
const source = fs.readFileSync(path.join(__dirname, "..", "web", "app.js"), "utf8");

const basePayload = () => ({
  index: { code: "000985", name: "中证全指" },
  meta: {
    last_date: "2026-10-22",
    fetched_at: "2026-10-23T18:30:00+08:00",
    last_attempt_at: "2026-10-23T18:30:00+08:00",
    update_status: "success", error: null,
  },
  records: [
    { date: "2026-10-21", pe: 16.00, close: 100 },
    { date: "2026-10-22", pe: 16.15, close: 101 },
  ],
});

const hikvisionPayload = () => ({
  ...basePayload(),
  index: {
    code: "002415", name: "海康威视", metric: "滚动市盈率（PE TTM）",
    source_name: "公开估值数据源", source_url: "https://example.org/002415",
  },
  records: [
    { date: "2010-06-01", pe: 30.0, close: null },
    { date: "2015-06-01", pe: null, close: null },
    { date: "2026-10-22", pe: 18.5, close: null },
  ],
});

function element(dataset = {}) {
  const classes = new Set();
  return {
    dataset, textContent: "", hidden: false, disabled: false, attributes: {}, listeners: {},
    classList: {
      add: (...names) => names.forEach((name) => classes.add(name)),
      remove: (...names) => names.forEach((name) => classes.delete(name)),
      toggle(name, force) {
        const present = force === undefined ? !classes.has(name) : force;
        if (present) classes.add(name); else classes.delete(name);
      },
      contains: (name) => classes.has(name),
    },
    setAttribute(name, value) { this.attributes[name] = value; },
    addEventListener(name, callback) { this.listeners[name] = callback; },
  };
}

async function flush() {
  for (let i = 0; i < 5; i++) await new Promise((resolve) => setImmediate(resolve));
}

async function createApp({ instrument = "csi", payload = basePayload(), status = { updating: false, last_error: null, last_attempt_at: null }, update = { accepted: true, reason: "started" }, dataError = null } = {}) {
  const routes = instrument === "hikvision"
    ? { data: "/hikvision-data.json", status: "/api/hikvision/status", update: "/api/hikvision/update" }
    : { data: "/data.json", status: "/api/status", update: "/api/update" };
  const nodes = new Map();
  const ranges = ["1", "3", "5", "10", "all"].map((range) => element({ range }));
  const timeouts = new Map();
  const intervals = [];
  const plots = [];
  const requests = [];
  let timerId = 0;
  let browserClone;
  const app = {
    payload, status, update, dataError, statusError: null, updateError: null, nodes, plots, requests,
    node(id) { if (!nodes.has(id)) nodes.set(id, element()); return nodes.get(id); },
    async refresh() { await this.node("refresh-button").listeners.click(); await flush(); },
    async poll() {
      const entry = [...timeouts.entries()].find(([, timer]) => timer.delay === 2000);
      assert.ok(entry, "An active update should schedule status polling");
      timeouts.delete(entry[0]);
      await entry[1].callback();
      await flush();
    },
    async selectRange(range) { ranges.find((item) => item.dataset.range === range).listeners.click(); await flush(); },
    hasScheduledWork() { return timeouts.size > 0 || intervals.length > 0; },
    hasPoll() { return [...timeouts.values()].some((timer) => timer.delay === 2000); },
  };
  const Plotly = { async react(...args) { plots.push(args); } };
  const window = {
    Plotly,
    matchMedia: () => ({ matches: false }),
    setTimeout(callback, delay) { const id = ++timerId; timeouts.set(id, { callback, delay }); return id; },
    clearTimeout(id) { timeouts.delete(id); },
    setInterval(callback) { intervals.push(callback); return intervals.length; },
  };
  const context = vm.createContext({
    window, Plotly, URL, Intl,
    AbortController: class {
      constructor() { this.signal = { aborted: false }; }
      abort() { this.signal.aborted = true; }
    },
    document: { body: { dataset: { instrument } }, getElementById: (id) => app.node(id), querySelectorAll: () => ranges },
    async fetch(url, options) {
      requests.push({ url, method: options.method || "GET" });
      assert.ok(Object.values(routes).includes(url), `Unexpected route for ${instrument}: ${url}`);
      if (url === routes.data && app.dataError) throw app.dataError;
      if (url === routes.status && app.statusError) throw app.statusError;
      if (url === routes.update && app.updateError) throw app.updateError;
      const value = url === routes.data ? app.payload : url === routes.status ? app.status : app.update;
      return { ok: true, status: url === routes.update && value.accepted ? 202 : 200, json: async () => browserClone(value) };
    },
  });
  // The browser supports these methods; this project's system Node 14 does not.
  vm.runInContext(`
    if (!Array.prototype.at) Array.prototype.at = function (index) {
      return this[index < 0 ? this.length + index : index];
    };
    if (!String.prototype.replaceAll) String.prototype.replaceAll = function (from, to) {
      return this.split(from).join(to);
    };
  `, context);
  browserClone = vm.runInContext("(value) => JSON.parse(JSON.stringify(value))", context);
  vm.runInContext(source, context, { filename: "app.js" });
  await flush();
  return app;
}

test("opening the page reads only local cache and leaves no automatic refresh work", async () => {
  const app = await createApp({ status: { updating: true } });
  assert.deepEqual(app.requests, [{ url: "/data.json", method: "GET" }]);
  assert.equal(app.hasScheduledWork(), false);
  assert.equal(app.node("refresh-button").disabled, false);
  await app.selectRange("all");
  assert.equal(app.requests.length, 1, "Changing the display range must not refresh data");
  assert.equal(app.hasScheduledWork(), false);
});

test("an initial cache read failure waits for explicit user action", async () => {
  const app = await createApp({ dataError: new Error("缓存不可用") });
  assert.match(app.node("status-text").textContent, /无法读取本地数据/);
  assert.deepEqual(app.requests, [{ url: "/data.json", method: "GET" }]);
  assert.equal(app.hasScheduledWork(), false);
  assert.doesNotMatch(app.node("error-detail").textContent, /自动重试/);
});

test("only a manual click sends the update POST", async () => {
  const app = await createApp();
  assert.equal(app.requests.filter((request) => request.method === "POST").length, 0);
  await app.refresh();
  assert.deepEqual(app.requests.filter((request) => request.method === "POST"), [{ url: "/api/update", method: "POST" }]);
  assert.equal(app.hasPoll(), true);
});

test("latest source date remains visible when its PE is missing", async () => {
  const payload = basePayload();
  payload.records.push({ date: "2026-10-23", pe: null, close: 102 });
  const app = await createApp({ payload });
  assert.equal(app.node("latest-date").textContent, "2026-10-23");
  assert.equal(app.node("latest-pe").textContent, "16.15");
  assert.match(app.node("latest-pe-detail").textContent, /2026-10-22.*最新日期 PE 缺失/);
  const trace = app.plots[app.plots.length - 1][1][0];
  assert.equal(trace.y[trace.y.length - 1], null);
  assert.equal(trace.connectgaps, false);
});

test("a newer server failure is shown as a fetch failure and retains the chart", async () => {
  const app = await createApp({ status: { updating: false, last_error: "上游暂时不可用", last_attempt_at: "2026-10-23T18:35:00+08:00" } });
  await app.refresh();
  await app.poll();
  assert.match(app.node("status-text").textContent, /抓取失败/);
  assert.doesNotMatch(app.node("status-text").textContent, /连接异常/);
  assert.match(app.node("error-detail").textContent, /上游暂时不可用/);
  assert.match(app.node("attempted-at").textContent, /18:35:00/);
  assert.equal(app.node("latest-pe").textContent, "16.15");
  assert.equal(app.node("chart-placeholder").hidden, true);
});

test("a server failure with an equal attempt timestamp is shown", async () => {
  const app = await createApp({ status: { updating: false, last_error: "响应解析失败", last_attempt_at: "2026-10-23T18:30:00+08:00" } });
  await app.refresh();
  await app.poll();
  assert.match(app.node("status-text").textContent, /抓取失败/);
  assert.match(app.node("error-detail").textContent, /响应解析失败/);
});

test("a manual update loads a newer successful cache and supersedes an older server error", async () => {
  const app = await createApp({ status: { updating: false, last_error: "旧的失败", last_attempt_at: "2026-10-23T18:35:00+08:00" } });
  app.payload = basePayload();
  app.payload.meta.last_attempt_at = "2026-10-23T18:40:00+08:00";
  app.payload.meta.fetched_at = "2026-10-23T18:40:00+08:00";
  await app.refresh();
  await app.poll();
  assert.doesNotMatch(app.node("status-text").textContent, /失败/);
  assert.equal(app.node("error-detail").hidden, true);
  assert.match(app.node("attempted-at").textContent, /18:40:00/);
});

test("a newer successful server attempt clears an older cache failure", async () => {
  const payload = basePayload();
  payload.meta.update_status = "error";
  payload.meta.error = "旧的缓存错误";
  const app = await createApp({ payload, status: { updating: false, last_error: null, last_attempt_at: "2026-10-23T18:35:00+08:00" } });
  await app.refresh();
  await app.poll();
  assert.doesNotMatch(app.node("status-text").textContent, /失败/);
  assert.equal(app.node("error-detail").hidden, true);
});

test("an older successful server attempt cannot hide a newer cache failure", async () => {
  const payload = basePayload();
  payload.meta.update_status = "error";
  payload.meta.error = "另一次手动抓取失败";
  const app = await createApp({ payload, status: { updating: false, last_error: null, last_attempt_at: "2026-10-23T18:25:00+08:00" } });
  await app.refresh();
  await app.poll();
  assert.match(app.node("status-text").textContent, /抓取失败/);
  assert.match(app.node("error-detail").textContent, /另一次手动抓取失败/);
  assert.match(app.node("attempted-at").textContent, /18:30:00/);
});

test("throttled manual refresh gives a friendly notice without polling or losing the chart", async () => {
  const app = await createApp({ update: { accepted: false, reason: "throttled" } });
  await app.refresh();
  assert.match(app.node("status-text").textContent, /频繁|稍后/);
  assert.doesNotMatch(app.node("status-text").textContent, /连接异常|抓取失败/);
  assert.equal(app.node("refresh-button").disabled, false);
  assert.equal(app.hasPoll(), false);
  assert.equal(app.hasScheduledWork(), false);
  assert.equal(app.node("latest-pe").textContent, "16.15");
  assert.equal(app.node("chart-placeholder").hidden, true);
});

test("busy manual refresh follows the running update until its failure is visible", async () => {
  const app = await createApp({ update: { accepted: false, reason: "busy" } });
  await app.refresh();
  assert.equal(app.node("refresh-button").disabled, true);
  app.status = { updating: false, last_error: "本次上游更新失败", last_attempt_at: "2026-10-23T18:45:00+08:00" };
  await app.poll();
  assert.equal(app.node("refresh-button").disabled, false);
  assert.match(app.node("status-text").textContent, /抓取失败/);
  assert.match(app.node("error-detail").textContent, /本次上游更新失败/);
});

test("an accepted update polls through completion and loads the new observation", async () => {
  const app = await createApp();
  await app.refresh();
  app.status = { updating: true, last_error: null, last_attempt_at: "2026-10-23T18:45:00+08:00" };
  await app.poll();
  assert.equal(app.node("refresh-button").disabled, true);
  app.payload = basePayload();
  app.payload.records.push({ date: "2026-10-23", pe: 16.2, close: 102 });
  app.payload.meta.last_attempt_at = "2026-10-23T18:45:00+08:00";
  app.status.updating = false;
  await app.poll();
  assert.equal(app.node("refresh-button").disabled, false);
  assert.equal(app.node("latest-date").textContent, "2026-10-23");
  assert.equal(app.node("latest-pe").textContent, "16.20");
  assert.equal(app.hasPoll(), false);
  assert.equal(app.hasScheduledWork(), false);
  assert.equal(app.requests.filter((request) => request.method === "POST").length, 1);
});

test("a status connection failure stops polling and preserves the last rendered values", async () => {
  const app = await createApp();
  await app.refresh();
  app.statusError = new Error("服务未响应");
  await app.poll();
  assert.match(app.node("status-text").textContent, /连接异常/);
  assert.doesNotMatch(app.node("status-text").textContent, /抓取失败/);
  assert.equal(app.node("latest-pe").textContent, "16.15");
  assert.equal(app.node("chart-placeholder").hidden, true);
  assert.equal(app.node("refresh-button").disabled, false);
  assert.equal(app.hasScheduledWork(), false);
  assert.equal(app.requests.filter((request) => request.method === "POST").length, 1);
});

test("a failed manual POST does not retry automatically", async () => {
  const app = await createApp();
  app.updateError = new Error("服务未响应");
  await app.refresh();
  assert.match(app.node("status-text").textContent, /连接异常/);
  assert.equal(app.node("refresh-button").disabled, false);
  assert.equal(app.hasScheduledWork(), false);
  assert.equal(app.requests.filter((request) => request.method === "POST").length, 1);
  assert.equal(app.node("latest-pe").textContent, "16.15");
});

test("Hikvision starts with all cached history and its own source, without fetching upstream", async () => {
  const app = await createApp({ instrument: "hikvision", payload: hikvisionPayload() });
  assert.deepEqual(app.requests, [{ url: "/hikvision-data.json", method: "GET" }]);
  assert.equal(app.hasScheduledWork(), false);
  assert.equal(app.node("latest-pe").textContent, "18.50");
  assert.match(app.node("percentile-detail").textContent, /全部历史.*2 个有效/);
  const trace = app.plots[app.plots.length - 1][1][0];
  assert.deepEqual(Array.from(trace.x), ["2010-06-01", "2015-06-01", "2026-10-22"]);
  assert.deepEqual(Array.from(trace.y), [30, null, 18.5]);
  assert.equal(trace.connectgaps, false);
  assert.match(app.node("source-link").textContent, /公开估值数据源/);
  assert.equal(app.node("source-link").href, "https://example.org/002415");
  assert.match(app.node("pe-chart").attributes["aria-label"], /海康威视.*TTM/);
});

test("Hikvision display range changes do not trigger data requests", async () => {
  const app = await createApp({ instrument: "hikvision", payload: hikvisionPayload() });
  await app.selectRange("1");
  assert.match(app.node("percentile-detail").textContent, /近 1 年.*1 个有效/);
  assert.equal(app.requests.length, 1);
  assert.equal(app.hasScheduledWork(), false);
});

test("Hikvision updates and polls only its own manual endpoints", async () => {
  const app = await createApp({ instrument: "hikvision", payload: hikvisionPayload() });
  await app.refresh();
  assert.deepEqual(app.requests[1], { url: "/api/hikvision/update", method: "POST" });
  assert.doesNotMatch(app.node("status-text").textContent, /中证/);
  await app.poll();
  assert.deepEqual(app.requests.map((r) => r.url), [
    "/hikvision-data.json", "/api/hikvision/update", "/api/hikvision/status", "/hikvision-data.json",
  ]);
  assert.equal(app.hasScheduledWork(), false);
});

test("each instrument rejects the other instrument's cache", async () => {
  for (const options of [
    { instrument: "hikvision", payload: basePayload() },
    { instrument: "csi", payload: hikvisionPayload() },
  ]) {
    const app = await createApp(options);
    assert.equal(app.plots.length, 0);
    assert.match(app.node("error-detail").textContent, /代码.*拒绝载入/);
    assert.equal(app.hasScheduledWork(), false);
  }
});

test("Hikvision refuses a static PE cache instead of labelling it TTM", async () => {
  const payload = hikvisionPayload();
  payload.index.metric = "静态市盈率";
  const app = await createApp({ instrument: "hikvision", payload });
  assert.equal(app.plots.length, 0);
  assert.match(app.node("error-detail").textContent, /TTM|口径/);
});

(async () => {
  let failed = 0;
  for (const { name, run } of tests) {
    try {
      await run();
      console.log(`PASS ${name}`);
    } catch (error) {
      failed++;
      console.error(`FAIL ${name}\n${error.stack}`);
    }
  }
  console.log(`${tests.length - failed}/${tests.length} frontend checks passed`);
  process.exitCode = failed ? 1 : 0;
})();
