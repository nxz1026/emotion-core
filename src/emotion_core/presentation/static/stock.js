/* 个股诊断页：输入代码 → 规则层结论（即时）+ AI 解读（异步）。 */
(function () {
  "use strict";

  const BASE = window.STOCK_BASE_PATH || "";
  const TRADE_DATE = window.STOCK_TRADE_DATE || "";
  const $ = (id) => document.getElementById(id);

  const STANCE_CLASS = {
    BUY: "badge-success",
    LOW: "badge-warning",
    WATCH: "badge-watch",
    AVOID: "badge-danger",
  };
  const TAG_CLASS = { high: "chip-high", mid: "chip-mid", low: "chip-low" };

  function esc(v) {
    return String(v === null || v === undefined ? "—" : v)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }
  function num(v, suffix) {
    if (v === null || v === undefined || v === "" || Number.isNaN(v)) return "—";
    return esc(v) + (suffix || "");
  }
  function bold(s) {
    // 依据里的 **换手板** 标记
    return esc(s).replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
  }

  function statHtml(label, value, cls) {
    return `<div class="stat"><div class="stat-label">${esc(label)}</div>` +
      `<div class="stat-value ${cls || ""}">${value}</div></div>`;
  }

  function showError(msg) {
    $("stock-error").style.display = "block";
    $("stock-error").textContent = msg;
    $("stock-result").style.display = "none";
  }

  function renderVerdict(v, basic, tradeDate) {
    $("verdict-title").textContent = `${basic.code} ${basic.name}`;
    $("verdict-sub").textContent =
      `${tradeDate} · ${v.layer ? v.layer + " 层级" : "无连板"} · ` +
      `行业 ${basic.industry || "未接入"}`;
    const badge = $("verdict-badge");
    badge.textContent = v.stance_text;
    badge.className = "badge " + (STANCE_CLASS[v.stance] || "badge-neutral");
    $("verdict-oneliner").textContent = v.one_liner;

    $("verdict-reasons").innerHTML =
      (v.reasons || []).map((r) => `<li>${bold(r)}</li>`).join("") || "<li class='muted'>无</li>";
    $("verdict-risks").innerHTML =
      (v.risks || []).map((r) => `<li>${bold(r)}</li>`).join("") ||
      "<li class='muted'>未触发风险项</li>";
    $("verdict-tags").innerHTML = (v.tags || []).map((t) =>
      `<span class="chip ${TAG_CLASS[t.level] || ""}" title="${esc(t.why)}">${esc(t.name)}</span>`
    ).join("");
  }

  function renderTrend(t, structure) {
    $("trend-stats").innerHTML = [
      statHtml("收盘", num(t.price), ""),
      statHtml("涨跌幅", num(t.pct_chg, "%"), t.pct_chg >= 0 ? "up" : "down"),
      statHtml("连板", num(t.cont_days, " 板")),
      statHtml("MA5 / MA20", `${num(t.ma5)} / ${num(t.ma20)}`),
      statHtml("距 20 日高点", num(t.off_high_pct, "%")),
      statHtml("量比", num(t.vol_ratio)),
      statHtml("换手率", num(t.turnover_rate, "%")),
      statHtml("近 60 日涨停 / 炸板", `${num(structure.limit_up_n)} / ${num(structure.bomb_n)}`),
    ].join("");
  }

  function renderSeries(series) {
    const head = ["日期", "收盘", "涨跌幅", "连板", "一字", "换手", "炸板", "换手率", "量比(5日)"];
    const rows = series.slice().reverse().map((r) => {
      const tags = [];
      if (r.is_limit_up) tags.push("涨停");
      if (r.is_one_word) tags.push("一字");
      if (r.is_exchange) tags.push("换手");
      if (r.is_bomb) tags.push("炸板");
      if (r.is_limit_down) tags.push("跌停");
      return `<tr>
        <td>${esc(r.date)}</td>
        <td>${num(r.close)}</td>
        <td class="${r.pct_chg >= 0 ? "up" : "down"}">${num(r.pct_chg, "%")}</td>
        <td>${num(r.cont_days)}</td>
        <td>${r.is_one_word ? "是" : "—"}</td>
        <td>${r.is_exchange ? "是" : "—"}</td>
        <td>${r.is_bomb ? "是" : "—"}</td>
        <td>${num(r.turnover_rate, "%")}</td>
        <td>${num(r.vol_ratio5)}</td>
      </tr>`;
    }).join("");
    $("series-table").innerHTML =
      `<thead><tr>${head.map((h) => `<th>${h}</th>`).join("")}</tr></thead><tbody>${rows}</tbody>`;
  }

  function renderEnv(env, top) {
    if (!env || !env.phase) {
      $("env-stats").innerHTML = "<p class='muted'>该交易日 market_stat 无数据</p>";
    } else {
      $("env-date").textContent = env.date || "";
      $("env-stats").innerHTML = [
        statHtml("阶段", esc(env.phase)),
        statHtml("买入窗口", esc(env.buy_window)),
        statHtml("涨停家数", num(env.limit_up_count)),
        statHtml("跌停家数", num(env.limit_down_count)),
        statHtml("最高板", num(env.max_limit_days, " 板")),
        statHtml("炸板率", num(env.bomb_rate !== null && env.bomb_rate !== undefined
          ? Math.round(env.bomb_rate * 1000) / 10 : null, "%")),
        statHtml("一字占比", num(env.oneword_ratio !== null && env.oneword_ratio !== undefined
          ? Math.round(env.oneword_ratio * 1000) / 10 : null, "%")),
        statHtml("强制离场", env.force_liquidate ? "是" : "否",
          env.force_liquidate ? "down" : ""),
      ].join("");
    }
    $("top-table").innerHTML =
      "<thead><tr><th>代码</th><th>名称</th><th>连板</th><th>一字</th><th>换手板</th></tr></thead><tbody>" +
      (top || []).map((r) => `<tr><td>${esc(r.code)}</td><td>${esc(r.name)}</td>
        <td>${num(r.cont_days)}</td><td>${r.is_one_word ? "是" : "—"}</td>
        <td>${r.is_exchange ? "是" : "—"}</td></tr>`).join("") + "</tbody>";
  }

  function renderPromotion(rows, mine) {
    const head = ["层级", "样本次日", "晋级率", "换手口径", "背离",
      "次日中位数", "次日胜率", "未晋级次日均值"];
    const body = (rows || []).map((r) => {
      const hit = mine && r.layer === mine ? ' class="row-hit"' : "";
      return `<tr${hit}><td>${esc(r.layer)}</td>
        <td>${num(r.total)}</td>
        <td>${num(r.rate, "%")}</td>
        <td>${num(r.rate_exchange, "%")}</td>
        <td>${num(r.divergence)}</td>
        <td>${num(r.perf_median, "%")}</td>
        <td>${num(r.win_rate, "%")}</td>
        <td>${num(r.fail_perf, "%")}</td></tr>`;
    }).join("");
    $("promo-table").innerHTML =
      `<thead><tr>${head.map((h) => `<th>${h}</th>`).join("")}</tr></thead><tbody>${body}</tbody>`;
    $("promo-note").textContent = mine
      ? `高亮行 = 当前标的所处层级（${mine}）；分母不足 ${3} 只时不给比率（显示 —）。`
      : "当前标的不在连板层级内（未涨停）。";
  }

  function renderLlm(llm) {
    if (!llm) return;
    $("llm-card").style.display = "";
    $("llm-model").textContent = llm.ok && llm.model ? `（${llm.model}）` : "";
    if (llm.ok) {
      $("llm-body").className = "";
      $("llm-body").innerHTML = esc(llm.text).replace(/\n/g, "<br>")
        .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
    } else {
      $("llm-body").className = "muted";
      $("llm-body").textContent = llm.enabled === false
        ? "LLM 未启用（EC_STOCK_LLM_PROFILE=off）：以上规则层结论不依赖 LLM。"
        : `AI 解读不可用：${llm.error || "未知错误"}`;
    }
  }

  async function analyze(code) {
    if (!code) return;
    $("stock-hint").textContent = "分析中…（首次需实时聚合全市场历史样本，约 1-2 秒）";
    try {
      const res = await fetch(`${BASE}/api/stock?code=${encodeURIComponent(code)}`
        + (TRADE_DATE ? `&date=${encodeURIComponent(TRADE_DATE)}` : ""));
      const data = await res.json();
      if (!data.ok) {
        showError(data.error || "分析失败");
        $("stock-hint").textContent = "";
        return;
      }
      $("stock-error").style.display = "none";
      $("stock-result").style.display = "block";
      $("stock-hint").textContent =
        `口径：只用 ${data.trade_date} 及之前的样本；标的数据源 daily_bar/derived_bar/market_stat/stock_basic。`;
      renderVerdict(data.verdict, data.basic, data.trade_date);
      renderTrend(data.trend, data.structure);
      renderSeries(data.series || []);
      renderEnv(data.market_env, data.market_top);
      renderPromotion(data.promotion_table, data.verdict.layer);
      $("llm-card").style.display = "none";
      window.location.hash = "code=" + encodeURIComponent(data.basic.code);
      history.replaceState(null, "", `${BASE}/stock?code=${encodeURIComponent(data.basic.code)}`
        + (TRADE_DATE ? `&date=${encodeURIComponent(TRADE_DATE)}` : ""));
      // AI 解读按需异步取（4~6 秒，不阻塞上面全部数据）
      const llmRes = await fetch(`${BASE}/api/stock/llm?code=${encodeURIComponent(data.basic.code)}`
        + (TRADE_DATE ? `&date=${encodeURIComponent(TRADE_DATE)}` : ""));
      renderLlm(await llmRes.json());
    } catch (err) {
      showError("请求失败：" + err);
      $("stock-hint").textContent = "";
    }
  }

  $("stock-form").addEventListener("submit", (e) => {
    e.preventDefault();
    analyze($("stock-input").value.trim());
  });

  const prefill = $("stock-input").value.trim();
  if (prefill) analyze(prefill);
})();
