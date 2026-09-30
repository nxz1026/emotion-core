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

  function tableHtml(head, rows) {
    return `<thead><tr>${head.map((h) => `<th>${esc(h)}</th>`).join("")}</tr></thead><tbody>` +
      (rows.length ? rows.join("")
        : `<tr><td colspan="${head.length}" class="muted">暂无</td></tr>`) + "</tbody>";
  }

  function statusCell(status) {
    const cls = status === "PASS" ? "up"
      : (status === "FAIL" || status === "WARN") ? "down" : "muted";
    return `<td class="${cls}">${esc(status)}</td>`;
  }

  function retCell(v) {
    return `<td class="${v == null ? "muted" : (v >= 0 ? "up" : "down")}">${num(v, "%")}</td>`;
  }

  function boolCell(v) {
    return `<td>${v == null ? "—" : (v ? "是" : "否")}</td>`;
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

  function renderBuyPoint(bp) {
    if (!bp || (!bp.limit_price && !bp.odds)) {
      $("buy-point-card").style.display = "none";
      return;
    }
    $("buy-point-card").style.display = "block";
    const limit = bp.limit_price != null ? num(bp.limit_price) : "—";
    $("buy-point-limit").textContent = limit;
    const odds = bp.odds || {};
    const rows = [];
    if (odds.layer) {
      rows.push(`<div class="buy-point-row"><span>层级</span><strong>${esc(odds.layer)}（${odds.cont_days} 连板）</strong></div>`);
    }
    if (odds.promote_rate != null) {
      rows.push(`<div class="buy-point-row"><span>历史晋级率</span><strong>${num(odds.promote_rate, "%")}（N=${odds.promote_n || "—"}）</strong></div>`);
    }
    if (odds.fwd_median != null) {
      rows.push(`<div class="buy-point-row"><span>前瞻 5 日中位收益</span><strong>${num(odds.fwd_median, "%")}（胜率 ${num(odds.fwd_win_rate, "%")} · N=${odds.fwd_n}）</strong></div>`);
    }
    $("buy-point-odds").innerHTML = rows.join("") || "<p class='muted'>暂无同层级历史样本</p>";
    if (bp.disclaimer) {
      $("buy-point-disclaimer").textContent = bp.disclaimer;
    }
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

  function renderDiagnose(dg) {
    const A = dg && dg.A, B = dg && dg.B, C = dg && dg.C;

    if (dg && !A && dg.note) {          // 整段降级：只显示原因，不摆空卡
      $("diagnose-a-card").style.display = "";
      $("diagnose-note").textContent = dg.note;
      $("diagnose-a-sub").textContent = "";
      $("diagnose-a-stats").innerHTML = "";
      $("diagnose-a-note").textContent = "";
      $("diagnose-a-table").innerHTML = "";
      return;
    }

    if (A) {
      $("diagnose-a-card").style.display = "";
      $("diagnose-note").textContent = dg.note || "";
      $("diagnose-a-sub").textContent =
        `${TRADE_DATE} · 窗口 ${A.window || "—"}（${A.window_source || "—"}）`;
      $("diagnose-a-stats").innerHTML = [
        statHtml("连板", num(A.cont_days, " 板")),
        statHtml("层级", A.layer || "—"),
        statHtml("换手板", A.is_exchange ? "是" : "—"),
        statHtml("梯队身份", A.ladder_role || "—"),
        statHtml("唯一最高板", A.is_sole_top ? "是" : "否"),
        statHtml("同身位只数", A.peers && A.peers.same_cont != null ? A.peers.same_cont : "—"),
        statHtml("买入窗口", A.window || "—"),
        statHtml("五条件", A.applicable
          ? (A.passed === true ? "全过" : (A.passed === false ? "未全过" : "不可核验"))
          : "不适用"),
      ].join("");
      $("diagnose-a-note").textContent =
        [A.note, A.reason, A.window_text].filter(Boolean).join("；");
      const items = (A.conditions || []).concat(A.warnings || []);
      $("diagnose-a-table").innerHTML = tableHtml(["项", "状态", "说明"],
        items.map((c) => `<tr><td>${esc(c.label)}</td>${statusCell(c.status)}` +
          `<td>${esc(c.note)}</td></tr>`));
    }

    if (B) {
      $("diagnose-b-card").style.display = "";
      const id = B.identity || {}, rs = B.return_summary || {};
      $("diagnose-b-sub").textContent = id.name
        ? `${id.name}（${id.industry || "行业未接入"}）` : "";
      $("diagnose-b-stats").innerHTML = [
        statHtml("首个 bar", id.first_bar_date || "—"),
        statHtml("次新", id.is_new_issuer == null ? "—" : (id.is_new_issuer ? "是" : "否")),
        statHtml("历史唯一最高板", num(B.sole_top_n, " 次")),
        statHtml("其后 T+1 均值", num(rs.avg_t1, "%")),
        statHtml("T+1 胜率", num(rs.win_rate_t1, "%")),
        statHtml("T+3 / T+5 均值", `${num(rs.avg_t3, "%")} / ${num(rs.avg_t5, "%")}`),
        statHtml("历史信号", num(B.signal_n, " 条")),
      ].join("");
      $("diagnose-b-note").textContent = B.note || "";
      $("diagnose-b-table").innerHTML = tableHtml(["当日", "T+1", "T+3", "T+5"],
        (B.sole_top_history || []).map((h) => `<tr><td>${esc(h.date)}</td>` +
          retCell(h.t1_ret) + retCell(h.t3_ret) + retCell(h.t5_ret) + "</tr>"));
      $("diagnose-b-signals").innerHTML = tableHtml(
        ["确认日", "动作", "状态", "窗口", "来源", "T+1 高开", "T+1 晋级", "T+1 收益",
          "5 日最大上行", "5 日最大回撤", "T+5 收益", "规则 A", "规则 D", "完成"],
        (B.signals || []).map((s) => {
          const o = s.outcome || {};
          return `<tr><td>${esc(s.confirm_date)}</td><td>${esc(s.action)}</td>` +
            `<td>${esc(s.status)}</td><td>${esc(s.buy_window)}</td><td>${esc(s.source)}</td>` +
            `<td>${num(o.t1_gap, "%")}</td>${boolCell(o.t1_promote)}` + retCell(o.t1_close_ret) +
            `<td>${num(o.max_up5, "%")}</td><td>${num(o.max_dd5, "%")}</td>` +
            retCell(o.t5_close_ret) + retCell(o.rule_ret_a) + retCell(o.rule_ret_d) +
            boolCell(o.complete) + "</tr>";
        }));
    }

    if (C) {
      $("diagnose-c-card").style.display = "";
      const p = C.promotion || {}, f5 = C.forward5 || {}, de = C.dragon_env || {};
      $("diagnose-c-sub").textContent = C.layer ? `${C.layer} 层级` : "无连板层级";
      $("diagnose-c-stats").innerHTML = [
        statHtml("层级", C.layer || "—"),
        statHtml("晋级率（名义）", num(p.rate, "%")),
        statHtml("晋级率（换手）", num(p.rate_exchange, "%")),
        statHtml("背离 divergence", num(p.divergence)),
        statHtml("同层 5 日前瞻中位", num(f5.median, "%")),
        statHtml("5 日胜率", num(f5.win_rate, "%")),
        statHtml("生态评级", de.rating || "—"),
        statHtml("分歧窗", C.diverge == null ? "—" : (C.diverge ? "是" : "否")),
      ].join("");
      const notes = [];
      if (p.total != null) notes.push(`同层样本 ${p.total}（晋级 ${p.promoted}）`);
      if (p.fail_perf != null) notes.push(`未晋级次日均值 ${p.fail_perf}%`);
      if (f5.n != null) notes.push(`前瞻样本 N=${f5.n}`);
      if (C.odds && C.odds.limit_price != null) notes.push(`打板价 ${C.odds.limit_price}`);
      if (C.note) notes.push(C.note);
      const side = (list, label) => {
        if (!Array.isArray(list) || !list.length) return;
        notes.push(label + list.map((x) => `${x.cond || ""}${x.cond ? "：" : ""}` +
          `${x.note || x}`).join(" / "));
      };
      side(de.reasons, "评级理由：");
      side(de.risks, "评级风险：");
      $("diagnose-c-note").textContent = notes.join("；");
      $("diagnose-c-table").innerHTML = tableHtml(
        ["日期", "主题材", "角色", "完整度", "状态", "最高板", "成员数", "龙头"],
        (C.themes || []).map((t) => `<tr><td>${esc(t.date)}</td>` +
          `<td>${esc(t.primary_theme)}</td><td>${esc(t.role)}</td>` +
          `<td>${num(t.completeness)}</td><td>${esc(t.status)}</td>` +
          `<td>${num(t.highest_board)}</td><td>${num(t.member_count)}</td>` +
          `<td>${esc(t.top_code)}</td></tr>`));
    }
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
      renderBuyPoint(data.buy_point);
      renderTrend(data.trend, data.structure);
      renderSeries(data.series || []);
      renderEnv(data.market_env, data.market_top);
      renderPromotion(data.promotion_table, data.verdict.layer);
      renderDiagnose(data.diagnose);
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
