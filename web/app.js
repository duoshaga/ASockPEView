"use strict";

(() => {
  const isHikvision = document.body?.dataset?.instrument === "hikvision";
  const profile = isHikvision ? {
    code: "002415", name: "海康威视", metric: "滚动市盈率（PE TTM）", range: "all",
    data: "/hikvision-data.json", status: "/api/hikvision/status", update: "/api/hikvision/update",
    sourceName: "公开数据源",
  } : {
    code: "000985", name: "中证全指", metric: "滚动市盈率", range: "10",
    data: "/data.json", status: "/api/status", update: "/api/update", sourceName: "中证指数官网",
  };
  const state = {
    payload: null,
    range: profile.range,
    updating: false,
    loading: false,
    pollTimer: null,
    serverStatus: null,
    updateNotice: null,
    connectionError: null,
    chartError: null,
  };
  const $ = (id) => document.getElementById(id);
  const numeric = (value) => typeof value === "number" && Number.isFinite(value) && value > 0;
  const formatPE = (value) => Number.isFinite(value) ? value.toFixed(2) : "—";
  const rangeLabel = () => state.range === "all" ? "全部历史" : `近 ${state.range} 年`;

  function localTime(value) {
    if (!value) return "—";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return String(value);
    return new Intl.DateTimeFormat("zh-CN", {
      timeZone: "Asia/Hong_Kong", year: "numeric", month: "2-digit", day: "2-digit",
      hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23",
    }).format(date).replaceAll("/", "-");
  }

  function quantile(sorted, fraction) {
    if (!sorted.length) return NaN;
    const position = (sorted.length - 1) * fraction;
    const low = Math.floor(position);
    const high = Math.ceil(position);
    return sorted[low] + (sorted[high] - sorted[low]) * (position - low);
  }

  function selectedRecords() {
    const records = state.payload.records;
    if (state.range === "all" || !records.length) return records;
    const end = records[records.length - 1].date;
    const cutoff = new Date(`${end}T00:00:00Z`);
    cutoff.setUTCFullYear(cutoff.getUTCFullYear() - Number(state.range));
    const from = cutoff.toISOString().slice(0, 10);
    return records.filter((record) => record.date >= from);
  }

  function setPlaceholder(message) {
    $("chart-placeholder").textContent = message || "";
    $("chart-placeholder").hidden = !message;
  }

  async function renderChart() {
    if (!state.payload) return;
    const records = selectedRecords();
    const values = records.filter((record) => numeric(record.pe)).map((record) => record.pe).sort((a, b) => a - b);
    const allValid = state.payload.records.filter((record) => numeric(record.pe));
    const latest = allValid[allValid.length - 1];
    const latestDate = state.payload.records.at(-1)?.date;
    const firstDate = records[0]?.date;
    const lastDate = records.at(-1)?.date;
    const missingCount = records.length - values.length;
    const q20 = quantile(values, 0.2);
    const q50 = quantile(values, 0.5);
    const q80 = quantile(values, 0.8);
    const percentile = latest && values.length ? 100 * values.filter((value) => value <= latest.pe).length / values.length : NaN;

    $("latest-pe").textContent = latest ? formatPE(latest.pe) : "—";
    $("latest-pe-detail").textContent = latest ? `PE 观测日期 ${latest.date}${latest.date !== latestDate ? " · 最新日期 PE 缺失" : ""}` : "暂无有效市盈率数据";
    $("percentile").textContent = Number.isFinite(percentile) ? percentile.toFixed(1) : "—";
    $("percentile-detail").textContent = `${rangeLabel()} · ${values.length.toLocaleString("zh-CN")} 个有效观测值`;
    $("latest-date").textContent = latestDate || "—";
    $("coverage-detail").textContent = allValid.length ? `有效历史起于 ${allValid[0].date}` : "暂无有效历史记录";
    $("range-description").textContent = firstDate ? `${firstDate} — ${lastDate} · ${missingCount ? `${missingCount} 个缺失值保留为空` : "所选区间无缺失值"}` : "所选区间暂无数据";
    $("q20").textContent = formatPE(q20);
    $("q50").textContent = formatPE(q50);
    $("q80").textContent = formatPE(q80);

    const sourceName = state.payload.index?.source_name || profile.sourceName;
    $("source-link").textContent = `${sourceName} ↗`;
    const sourceCaption = $("source-name");
    if (sourceCaption) sourceCaption.textContent = sourceName;
    const sourceURL = state.payload.index?.source_url;
    if (sourceURL) {
      try {
        const url = new URL(sourceURL);
        if (url.protocol === "https:" || url.protocol === "http:") $("source-link").href = url.href;
      } catch (_) { /* Retain the verified default source link. */ }
    }

    if (!window.Plotly) {
      state.chartError = "本地绘图库未能加载，请确认 plotly.min.js 文件存在后重新打开页面。";
      setPlaceholder(state.chartError);
      renderStatus();
      return;
    }
    if (!values.length) {
      setPlaceholder("所选区间暂无有效 PE 数据，请切换其他范围。");
      return;
    }

    const smallScreen = window.matchMedia("(max-width: 700px)").matches;
    const levelLines = [
      { value: q20, color: "#a2bca1" },
      { value: q50, color: "#b7b48b" },
      { value: q80, color: "#c7a482" },
    ].map(({ value, color }) => ({
      type: "line", xref: "paper", x0: 0, x1: 1, yref: "y", y0: value, y1: value,
      line: { color, width: 1, dash: "dot" }, layer: "below",
    }));
    const traces = [{
      x: records.map((record) => record.date),
      y: records.map((record) => numeric(record.pe) ? record.pe : null),
      type: "scatter", mode: "lines", name: profile.metric, connectgaps: false,
      line: { color: "#28654a", width: 1.7 },
      hovertemplate: `%{x|%Y-%m-%d}<br>${profile.metric} <b>%{y:.2f} 倍</b><extra></extra>`,
    }];
    const layout = {
      autosize: true,
      paper_bgcolor: "#ffffff", plot_bgcolor: "#ffffff",
      font: { family: '"Segoe UI", "Microsoft YaHei", sans-serif', size: 11, color: "#8a9586" },
      margin: { l: smallScreen ? 42 : 52, r: smallScreen ? 19 : 31, t: 25, b: 43 },
      showlegend: false, hovermode: "x", dragmode: "zoom",
      hoverlabel: { bgcolor: "#213f32", bordercolor: "#213f32", font: { color: "#ffffff", size: 12 } },
      xaxis: {
        type: "date", showgrid: false, zeroline: false, showline: true, linecolor: "#e8ece4",
        tickformat: "%Y-%m", nticks: smallScreen ? 5 : 9, tickfont: { size: 10 },
        ticks: "outside", tickcolor: "#e8ece4", ticklen: 5, automargin: true,
        showspikes: true, spikemode: "across", spikethickness: 1, spikecolor: "#c1ccba", spikedash: "dot",
        range: [firstDate, lastDate],
      },
      yaxis: {
        showgrid: true, gridcolor: "#eff1ec", gridwidth: 1, zeroline: false, showline: false,
        tickfont: { size: 10 }, nticks: 6, tickformat: ".1f", fixedrange: false, automargin: true,
      },
      shapes: levelLines,
      uirevision: `${state.range}:${lastDate}`,
    };
    try {
      await Plotly.react("pe-chart", traces, layout, {
        responsive: true, displaylogo: false, displayModeBar: false, scrollZoom: false,
        doubleClick: "reset", showTips: false,
      });
      state.chartError = null;
      setPlaceholder(null);
      $("pe-chart").setAttribute("aria-label", `${firstDate} 至 ${lastDate} ${profile.name}${profile.metric}，${values.length} 个有效值，${missingCount} 个缺失值。最新可用 PE ${formatPE(latest?.pe)} 倍。`);
    } catch (error) {
      state.chartError = `图表绘制失败：${error.message}`;
      setPlaceholder(state.chartError);
    }
    renderStatus();
  }

  function renderStatus() {
    const meta = state.payload?.meta || {};
    const serverAttempt = Date.parse(state.serverStatus?.last_attempt_at);
    const cachedAttempt = Date.parse(meta.last_attempt_at);
    const useServerAttempt = Number.isFinite(serverAttempt) && (!Number.isFinite(cachedAttempt) || serverAttempt >= cachedAttempt);
    const attemptedAt = useServerAttempt ? state.serverStatus.last_attempt_at : meta.last_attempt_at;
    const updateError = useServerAttempt
      ? state.serverStatus.last_error
      : meta.error || (meta.update_status === "error" ? "抓取失败，请稍后重试。" : null);
    $("fetched-at").textContent = localTime(meta.fetched_at);
    $("attempted-at").textContent = localTime(attemptedAt);
    $("refresh-button").disabled = state.updating;
    $("refresh-button").classList.toggle("is-updating", state.updating);
    $("refresh-label").textContent = state.updating ? "正在更新" : "手动更新数据";
    const indicator = $("status-message");
    indicator.classList.remove("is-error", "is-updating");
    let message;
    let detail = "";
    if (state.connectionError) {
      indicator.classList.add("is-error");
      message = state.payload ? "本机服务连接异常 · 已保留上次载入的图表" : "无法读取本地数据";
      detail = `${state.connectionError} 请确认本机数据服务正在运行，然后重新打开页面或点击“手动更新数据”。`;
    } else if (state.chartError) {
      indicator.classList.add("is-error");
      message = "图表暂时不可用";
      detail = state.chartError;
    } else if (state.updating) {
      indicator.classList.add("is-updating");
      message = `正在从${state.payload?.index?.source_name || profile.sourceName}更新 · 继续展示已有数据`;
    } else if (updateError) {
      indicator.classList.add("is-error");
      message = "最近一次抓取失败 · 已保留上次成功数据";
      detail = typeof updateError === "string" ? updateError : JSON.stringify(updateError);
      if (state.updateNotice) detail += ` ${state.updateNotice}`;
    } else if (meta.preview_error) {
      indicator.classList.add("is-error");
      message = "数据已更新 · 仓库首页预览生成失败";
      detail = meta.preview_error;
    } else if (state.updateNotice) {
      message = state.updateNotice;
    } else if (state.payload) {
      message = "本地静态缓存已载入 · 点击“手动更新数据”获取新数据";
    } else {
      message = "正在读取本地静态缓存";
    }
    $("status-text").textContent = message;
    $("error-detail").hidden = !detail;
    $("error-detail").textContent = detail;
  }

  async function requestJSON(path, options = {}) {
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), 20000);
    try {
      const response = await fetch(path, { cache: "no-store", ...options, signal: controller.signal });
      if (!response.ok) throw new Error(`请求 ${path} 失败（HTTP ${response.status}）。`);
      return await response.json();
    } catch (error) {
      if (error.name === "AbortError") throw new Error(`请求 ${path} 超时。`);
      throw error;
    } finally {
      window.clearTimeout(timeout);
    }
  }

  function validatePayload(payload) {
    if (!payload || !payload.meta || !Array.isArray(payload.records)) throw new Error("本地数据格式无效。");
    if (String(payload.index?.code) !== profile.code) throw new Error(`本地缓存的证券代码不是 ${profile.code}，已拒绝载入。`);
    if (payload.index?.name !== profile.name) throw new Error(`本地缓存的名称不是${profile.name}，已拒绝载入。`);
    if (isHikvision && !/TTM/i.test(String(payload.index?.metric || ""))) throw new Error("海康威视缓存不是 PE TTM 口径，已拒绝载入。");
    let previous = "";
    for (const record of payload.records) {
      if (!/^\d{4}-\d{2}-\d{2}$/.test(record.date) || record.date <= previous) throw new Error("本地数据日期无效或未按日期递增。");
      if (record.pe !== null && !numeric(record.pe)) throw new Error("本地数据含非法 PE 值。");
      previous = record.date;
    }
    return payload;
  }

  function scheduleUpdatePoll() {
    window.clearTimeout(state.pollTimer);
    state.pollTimer = window.setTimeout(pollUpdate, 2000);
  }

  async function pollUpdate() {
    state.pollTimer = null;
    try {
      const result = await requestJSON(profile.status);
      state.serverStatus = result;
      state.updating = Boolean(result.updating);
      state.connectionError = null;
      if (state.updating) {
        scheduleUpdatePoll();
      } else {
        state.pollTimer = null;
        await loadData();
      }
    } catch (error) {
      state.connectionError = error.message;
      state.updating = false;
    }
    renderStatus();
  }

  async function loadData() {
    if (state.loading) return;
    state.loading = true;
    state.updateNotice = null;
    try {
      state.payload = validatePayload(await requestJSON(profile.data));
      state.connectionError = null;
      await renderChart();
    } catch (error) {
      state.connectionError = error.message;
    } finally {
      state.loading = false;
    }
    if (!state.payload && state.connectionError) setPlaceholder("尚未载入有效数据，请查看下方状态，或点击“手动更新数据”重试。");
    renderStatus();
  }

  document.querySelectorAll("[data-range]").forEach((button) => {
    const initiallyActive = button.dataset.range === state.range;
    button.classList.toggle("is-active", initiallyActive);
    button.setAttribute("aria-pressed", String(initiallyActive));
    button.addEventListener("click", () => {
      state.range = button.dataset.range;
      document.querySelectorAll("[data-range]").forEach((item) => {
        const active = item === button;
        item.classList.toggle("is-active", active);
        item.setAttribute("aria-pressed", String(active));
      });
      void renderChart();
    });
  });

  $("refresh-button").addEventListener("click", async () => {
    if (state.updating) return;
    state.updating = true;
    state.updateNotice = null;
    state.connectionError = null;
    renderStatus();
    try {
      const result = await requestJSON(profile.update, { method: "POST" });
      if (result.accepted || result.reason === "busy") {
        scheduleUpdatePoll();
      } else if (result.reason === "throttled") {
        state.updating = false;
        state.updateNotice = "更新请求较频繁，请稍后再试 · 已保留当前图表";
        window.clearTimeout(state.pollTimer);
        state.pollTimer = null;
        renderStatus();
      } else {
        throw new Error("本机服务返回了无法识别的更新状态。");
      }
    } catch (error) {
      state.updating = false;
      state.connectionError = error.message;
      renderStatus();
    }
  });

  void loadData();
})();
