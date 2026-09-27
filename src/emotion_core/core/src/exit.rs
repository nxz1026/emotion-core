//! T6 卖出建议（PLAN §1.6）：退潮清仓(b) > 断板卖出(a)；高潮/分歧期无建议。
//!
//! 语义逐字照搬 emotion_core/algorithms/exit.py 的纯逻辑部分（_sell_signal / _sell /
//! suggestions 编排），SQL 取数（_stat / _derived / _open_positions）留在 Python 侧。
//!
//! 判定逻辑（零改动）：
//! - b 退潮（stat.force_liquidate=True）：全部持仓无条件清仓建议，优先级最高——
//!   此分支不读当日判据行（退潮当天仍封板的持仓也清）；建议日期统一改写为建议日
//!   （Python：replace(s, date=trade_date)）；
//! - a 断板：当日判据行存在且 is_limit_up=False → 卖出建议（今日未封住）；
//! - c 其余（涨停 / 一字 / 无判据行）→ 无建议。缺行 = 缺数，不当断板（F6）；
//! - stat=None → 不走退潮分支，退化为逐仓断板检查。
//!
//! 执行语义（契约 v2）：SELL 恒 exec_hint=CLOSE_ALL（清仓全部持仓）；buy_window 只是
//! 买入口径的市场标签（退潮期为 NONE），与卖出无关。

// pyo3 0.20 的 `#[pymethods]` 宏在 rustc ≥1.80 会触发 non_local_definitions 误报，抑制之。
#![allow(non_local_definitions)]

use pyo3::prelude::*;
use std::collections::HashMap;

/// 执行语义常量（契约 v2：SELL=CLOSE_ALL 清仓，BUY=OPEN_POS 开仓）。
const EXEC_CLOSE_ALL: &str = "CLOSE_ALL";

/// 持仓最小投影（对应 Python Holding）：exit 判定只读 code / entry_date。
#[pyclass(name = "Holding")]
#[derive(Clone)]
pub struct Holding {
    #[pyo3(get, set)]
    pub code: String,
    /// 开仓日，ISO "YYYY-MM-DD"（datetime.date 的 str() 形式，字典序即时间序）。
    #[pyo3(get, set)]
    pub date: String,
}

#[pymethods]
impl Holding {
    /// 接受 datetime.date（str() → "YYYY-MM-DD"）或 ISO 字符串。
    #[new]
    #[pyo3(signature = (code, date))]
    fn new(code: String, date: &PyAny) -> PyResult<Self> {
        let date: String = date.str()?.extract()?;
        Ok(Holding { code, date })
    }
}

/// 当日市场状态行（对应 Python _stat 返回的 dict）：phase / buy_window / force_liquidate。
#[pyclass(name = "MarketStat")]
#[derive(Clone)]
pub struct MarketStat {
    #[pyo3(get, set)]
    pub phase: String,
    #[pyo3(get, set)]
    pub buy_window: String,
    #[pyo3(get, set)]
    pub force_liquidate: bool,
}

#[pymethods]
impl MarketStat {
    #[new]
    #[pyo3(signature = (phase, buy_window, force_liquidate))]
    fn new(phase: String, buy_window: String, force_liquidate: bool) -> Self {
        MarketStat { phase, buy_window, force_liquidate }
    }
}

/// SELL 信号（对应 Python SellSignal）：Signal 契约 + 卖出三列（reason / buy_window /
/// exec_hint）。action 恒 SELL、source 恒 live、status 恒 SUGGESTED。
/// checklist 恒六项 UNKNOWN（不参与判定，见 exit.py 模块文档），故不映射。
#[pyclass(name = "SellSignal")]
#[derive(Clone)]
pub struct SellSignal {
    #[pyo3(get, set)]
    pub code: String,
    #[pyo3(get, set)]
    pub date: String,
    #[pyo3(get, set)]
    pub action: String,
    #[pyo3(get, set)]
    pub source: String,
    #[pyo3(get, set)]
    pub status: String,
    #[pyo3(get, set)]
    pub reason: String,
    #[pyo3(get, set)]
    pub buy_window: String,
    #[pyo3(get, set)]
    pub exec_hint: String,
}

#[pymethods]
impl SellSignal {
    /// 接受 datetime.date（str() → "YYYY-MM-DD"）或 ISO 字符串。
    #[new]
    #[pyo3(signature = (code, date, reason, buy_window))]
    fn new(code: String, date: &PyAny, reason: String, buy_window: String) -> PyResult<Self> {
        let date: String = date.str()?.extract()?;
        Ok(SellSignal {
            code,
            date,
            action: "SELL".to_string(),
            source: "live".to_string(),
            status: "SUGGESTED".to_string(),
            reason,
            buy_window,
            exec_hint: EXEC_CLOSE_ALL.to_string(),
        })
    }
}

/// 构造 SELL 信号（对应 Python _sell_signal）：exec_hint 恒 CLOSE_ALL，source 恒 live。
#[pyfunction]
#[pyo3(signature = (code, when, reason, buy_window))]
pub fn sell_signal(code: String, when: String, reason: String, buy_window: String) -> SellSignal {
    SellSignal {
        code,
        date: when,
        action: "SELL".to_string(),
        source: "live".to_string(),
        status: "SUGGESTED".to_string(),
        reason,
        buy_window,
        exec_hint: EXEC_CLOSE_ALL.to_string(),
    }
}

/// 退潮(b) 或 断板(a) 卖出建议；c 规则=无建议（对应 Python _sell 纯逻辑）。
///
/// `day` 为当日判据行三元组 (date, is_limit_up, cont_days)——Python 侧由
/// domain.DerivedBar 投影（Rust 不持有该 pyclass 类型）；None = 缺行（缺数，
/// 不当断板，F6）。
#[pyfunction]
#[pyo3(signature = (pos, stat=None, day=None))]
pub fn sell(
    pos: &Holding,
    stat: Option<&MarketStat>,
    day: Option<(&str, bool, i64)>,
) -> Option<SellSignal> {
    if let Some(stat) = stat {
        if stat.force_liquidate {
            return Some(sell_signal(
                pos.code.clone(),
                pos.date.clone(),
                "退潮期清仓建议".to_string(),
                "NONE".to_string(),
            ));
        }
    }
    if let Some((date, is_limit_up, cont_days)) = day {
        if !is_limit_up {
            // Python: f"断板建议（原{day.cont_days or ''}连板今日未封）"——0 板不留数字。
            let cont = if cont_days == 0 { String::new() } else { cont_days.to_string() };
            return Some(sell_signal(
                pos.code.clone(),
                date.to_string(),
                format!("断板建议（原{cont}连板今日未封）"),
                String::new(),
            ));
        }
    }
    None
}

/// 当日全部卖出建议（对应 Python suggestions 的纯逻辑编排）：退潮优先，否则逐仓断板检查。
///
/// SQL 取数留在 Python 侧：stat（_stat 结果）/ positions（OPEN 持仓）/ bars
/// （code → 当日判据行三元组，缺行 = None）由调用方注入。
#[pyfunction]
#[pyo3(signature = (trade_date, stat=None, positions=vec![], bars=HashMap::new()))]
pub fn suggestions(
    trade_date: &PyAny,
    stat: Option<MarketStat>,
    positions: Vec<Holding>,
    bars: HashMap<String, Option<(String, bool, i64)>>,
) -> PyResult<Vec<SellSignal>> {
    let trade_date: String = trade_date.str()?.extract()?;
    if let Some(stat) = &stat {
        if stat.force_liquidate {
            // 退潮：全部持仓无条件清仓，不读当日判据行（退潮当天仍封板的持仓也清）；
            // 建议日期统一改写为建议日（Python：replace(s, date=trade_date)）。
            return Ok(positions
                .iter()
                .filter_map(|p| sell(p, Some(stat), None))
                .map(|s| SellSignal { date: trade_date.clone(), ..s })
                .collect());
        }
    }
    Ok(positions
        .iter()
        .filter_map(|p| {
            let day = bars
                .get(&p.code)
                .and_then(|o| o.as_ref())
                .map(|(d, up, cont)| (d.as_str(), *up, *cont));
            sell(p, None, day)
        })
        .collect())
}
