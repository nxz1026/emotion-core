//! T13 题材坐标（theme.py 的算法层）：概念标签 → 题材聚合。
//!
//! 语义逐字照搬 `emotion_core/algorithms/theme.py`（docs/07 §3.3、§4）：
//! STYLE_ONLY 三重判据（黑名单 / 前缀 / 包含）、题材展开口径（unnest 全部标签，
//! 一股可属多题材）、梯队聚合公式（完整度三项权重 + 状态四档）、FALSE_RELATION
//! 判例，一律照搬，未增删任何条件、未调任何阈值。
//!
//! 与 Python 侧的差异（全部是契约 / IO 适配，不涉判定规则）：
//! - SQL 取数留在 Python 侧：`_fetch_cont_days` / `load_cont_days` 不搬。
//!   `group_stats` 收注入的 cont_days 映射（= Python 侧 cont_days 注入分支，逐字）；
//!   `aggregate` 收注入的 `(date, code) -> cont_days` 映射（= `_fetch_cont_days`
//!   的返回形状；无行即丢，INNER JOIN 语义不变）。
//! - 日期一律以 ISO "YYYY-MM-DD" 字符串传递（`datetime.date` 的 str() 形式，
//!   与 indicators.rs 的 Bar::new 同先例）；注入映射的键用同形字符串。
//! - `group_stats_of` 空输入抛 ValueError（同 Python `max()` 空序列）。
//! - `aggregate` 返回 Python dict，保持题材首次出现序（同 Python 键序语义）。
//!
//! ★ 确定性：Python 的 `max(members, key=...)` 取 top_code，**并列时取行序首个**；
//!   本模块保持该取序语义（fold 严格大于才替换），不新造并列规则。

// pyo3 0.20 的 `#[pymethods]` 宏在 rustc ≥1.80 会触发 non_local_definitions 误报，抑制之。
#![allow(non_local_definitions)]

use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::PyDict;
use std::collections::{HashMap, HashSet};

// ── STYLE_ONLY 判据（lkl lkl/config.py 原值逐字；三重判据不用同一匹配方式）──
const THEME_STYLE_BLACKLIST: &[&str] = &[
    "全部A股", "新质生产力综合", "连板", "打板", "外资企业", "市场情绪",
    "TMT", "小市值", "微盘股", "融资融券", "深股通", "沪股通", "次新股",
    "举牌", "年报预增", "预盈预亏", "ST板块", "低价股", "破净股",
    "超涨", "首板", "领涨龙头", "贷款回购", "合资企业", "养老金",
    "昨日涨停", "昨日连板", "昨日触板", "涨停板", "昨日炸板",
    "龙虎榜", "成交主力", "高振幅", "破净", "预减", "预增", "预盈",
    "预亏", "股票质押", "连板反包", "反包", "趋势股",
];
const THEME_STYLE_PREFIXES: &[&str] = &[
    "全A", "标普", "富时", "纳入", "MSCI", "上证", "深证",
    "股权激励", "财报披露", "首板",
];
const THEME_STYLE_CONTAINS: &[&str] = &[
    "重仓", "标的", "综合", "点位贡献", "指数", "国资",
    "微盘", "小盘", "大盘", "中盘", "等权",
];

/// STYLE_ONLY 噪声：黑名单 / 全A* 前缀 / *重仓* 包含（三重判据，同 Python is_style）。
#[pyfunction]
pub fn is_style(tag: &str) -> bool {
    THEME_STYLE_BLACKLIST.contains(&tag)
        || THEME_STYLE_PREFIXES.iter().any(|p| tag.starts_with(p))
        || THEME_STYLE_CONTAINS.iter().any(|s| tag.contains(s))
}

/// 噪声剔除：保留非 STYLE_ONLY 标签，顺序不变（同 Python theme_filter）。
#[pyfunction]
pub fn theme_filter(tags: Vec<String>) -> Vec<String> {
    tags.into_iter().filter(|t| !is_style(t)).collect()
}

/// theme_tag 行 → 展开后的题材名列表（lkl unnest 口径，同 Python _row_themes）。
///
/// 工单形状 `{code, theme_name, tag_date}` 是单标签，直接成表（优先于原生形状）；
/// lkl 原生形状 `{primary_theme, secondary_themes}`：主名已在次名列表内则原样，
/// 否则主名**追加在末位**；主名或次名列表任一为 NULL/缺失 → 空列表。
#[pyfunction]
pub fn row_themes(row: &PyAny) -> PyResult<Vec<String>> {
    // 工单形状：含 theme_name 键即单标签路径（键存在但值为 None/"" → 空列表）。
    if row.get_item("theme_name").is_ok() {
        let name: Option<String> = row.get_item("theme_name")?.extract()?;
        return Ok(match name {
            Some(s) if !s.is_empty() => vec![s],
            _ => Vec::new(),
        });
    }
    // lkl 原生形状：任一为 NULL/缺失 → 空列表（lkl NULL 传播）。
    let secondary: Option<Vec<String>> = match row.get_item("secondary_themes") {
        Ok(v) => v.extract()?,
        Err(_) => None,
    };
    let primary: Option<String> = match row.get_item("primary_theme") {
        Ok(v) => v.extract()?,
        Err(_) => None,
    };
    match (secondary, primary) {
        (Some(sec), Some(pri)) => {
            if sec.contains(&pri) {
                Ok(sec)
            } else {
                let mut v = sec;
                v.push(pri);
                Ok(v)
            }
        }
        _ => Ok(Vec::new()),
    }
}

/// 题材组梯队统计（对应 Python group_stats_of 返回的 dict，8 字段逐字）。
#[pyclass]
#[derive(Clone)]
pub struct GroupStats {
    #[pyo3(get, set)]
    pub highest_board: i64,
    #[pyo3(get, set)]
    pub top_code: String,
    #[pyo3(get, set)]
    pub mid_count: i64,
    #[pyo3(get, set)]
    pub low_count: i64,
    #[pyo3(get, set)]
    pub first_board_count: i64,
    #[pyo3(get, set)]
    pub completeness: f64,
    #[pyo3(get, set)]
    pub status: String,
    #[pyo3(get, set)]
    pub member_count: i64,
}

#[pymethods]
impl GroupStats {
    #[new]
    #[pyo3(signature = (highest_board, top_code, mid_count, low_count, first_board_count, completeness, status, member_count))]
    pub fn new(
        highest_board: i64,
        top_code: String,
        mid_count: i64,
        low_count: i64,
        first_board_count: i64,
        completeness: f64,
        status: String,
        member_count: i64,
    ) -> Self {
        GroupStats {
            highest_board,
            top_code,
            mid_count,
            low_count,
            first_board_count,
            completeness,
            status,
            member_count,
        }
    }
}

/// Python `round(x, 1)` 的等价实现：对精确二进制值正确舍入（half-to-even），
/// 返回最近 double。Rust 的 `format!("{:.1}")` 同样对精确值正确舍入
/// （half-to-even），parse 回最近 double——与 Python round 逐位一致。
fn round1(x: f64) -> f64 {
    format!("{:.1}", x).parse().unwrap_or(x)
}

/// `group_stats_of` 本体（要求非空；空输入由 pyfunction 包装抛 ValueError）。
fn group_stats_of_inner(members: &[(String, i64)]) -> GroupStats {
    let days: Vec<i64> = members.iter().map(|m| m.1).collect();
    let h = *days.iter().max().expect("non-empty members");
    let levels: HashSet<i64> = days.iter().copied().collect();
    let (gaps, gap_rate) = if h > 2 {
        let g = (2..h).filter(|x| !levels.contains(x)).count();
        (g, g as f64 / (h - 2) as f64)
    } else {
        (0, 0.0)
    };
    let mid = days.iter().filter(|&&x| (3..h).contains(&x)).count() as i64;
    let low = days.iter().filter(|&&x| x == 2).count() as i64;
    // top_code 取行序首个最高板（lkl 的 `max(members, ...)`，平局由输入顺序决定）。
    let top = members
        .iter()
        .fold(&members[0], |best, m| if m.1 > best.1 { m } else { best });
    // 完整度临时公式（权重待 P3 用晋级率定标）：
    // 10*min(h,8) + 30*(1-断层率) + 20*min(1,成员/5)，round 到 1 位。
    let comp = round1(
        (10 * h.min(8)) as f64 + 30.0 * (1.0 - gap_rate)
            + 20.0 * (1.0f64.min(days.len() as f64 / 5.0)),
    );
    let status = if h >= 4 && mid > 0 && low > 0 {
        "梯队完整"
    } else if h >= 4 && mid == 0 {
        "孤高"
    } else if days.len() >= 5 && h <= 2 {
        "低位扩散"
    } else {
        "一般"
    };
    GroupStats {
        highest_board: h,
        top_code: top.0.clone(),
        mid_count: mid,
        low_count: low,
        first_board_count: 0,
        completeness: comp,
        status: status.to_string(),
        member_count: days.len() as i64,
    }
}

/// lkl `group_stats` 本体（逐字）：[(code, cont_days)] → 梯队聚合。
/// 空输入按 lkl 行为抛 ValueError（`max()` 空序列）——不静默返回零值统计。
#[pyfunction]
pub fn group_stats_of(members: Vec<(String, i64)>) -> PyResult<GroupStats> {
    if members.is_empty() {
        return Err(PyErr::new::<PyValueError, _>("max() arg is an empty sequence"));
    }
    Ok(group_stats_of_inner(&members))
}

/// 题材组统计（纯逻辑部分）：成员代码 + 注入的 cont_days 映射 → 梯队聚合。
///
/// 对应 Python `group_stats(codes, trade_date, cont_days=...)` 的注入分支：
/// 无梯队行的代码被丢弃（lkl INNER JOIN 语义，不记 0）；全体成员被丢弃 → None
/// （Python 侧返回 {}）。trade_date / load_cont_days 的 SQL 读留在 Python 侧。
#[pyfunction]
pub fn group_stats(codes: Vec<String>, cont_days: HashMap<String, i64>) -> Option<GroupStats> {
    let members: Vec<(String, i64)> = codes
        .iter()
        .filter_map(|c| cont_days.get(c).map(|d| (c.clone(), *d)))
        .collect();
    if members.is_empty() {
        None
    } else {
        Some(group_stats_of_inner(&members))
    }
}

/// 题材展开聚合（lkl `_aggregate_rows` 的纯函数化）：展开全部标签 → 按题材分组 →
/// 梯队聚合。返回 Python dict，键序 = 题材在展开口径下的首次出现序（同 Python）。
///
/// 输入行必备 code / tag_date；题材来源二选一（见 row_themes）——工单形状
/// `theme_name`，或 lkl 原生形状 `primary_theme` + `secondary_themes`（一股可属多题材）。
/// 可选键 `cont_days`：直接带连板数；缺失者按 (tag_date, code) 查 `cont_days_map`
/// （= Python 侧 `_fetch_cont_days` 的注入返回；查不到即丢，INNER JOIN 语义）。
/// 无 code / 无 tag_date 的行被丢弃（不是记 0）。
///
/// STYLE_ONLY 题材在展开后剔除（is_style，与 lkl 同位置同判据）。
/// 空输入 / 题材全被剔除 / 成员全无梯队行 → 空 dict（A6 守卫：返回 {} 不落库）。
#[pyfunction]
#[pyo3(signature = (theme_tags, cont_days_map=None))]
pub fn aggregate(
    py: Python,
    theme_tags: Vec<&PyAny>,
    cont_days_map: Option<HashMap<(String, String), i64>>,
) -> PyResult<Py<PyDict>> {
    let empty = || -> Py<PyDict> { PyDict::new(py).into_py(py) };
    if theme_tags.is_empty() {
        return Ok(empty());
    }
    // 展开：无 code / 无 tag_date 的行丢行；STYLE_ONLY 题材剔除。
    let mut expanded: Vec<(String, String, String, Option<i64>)> = Vec::new();
    for row in theme_tags {
        let code = match row.get_item("code") {
            Ok(v) => v.extract::<Option<String>>()?,
            Err(_) => None,
        };
        let td = match row.get_item("tag_date") {
            Ok(v) => v.str()?.extract::<String>().ok(),
            Err(_) => None,
        };
        // Python: `if not code or td is None: continue`（空代码串同丢）。
        let code = match code {
            Some(c) if !c.is_empty() => c,
            _ => continue,
        };
        let td = match td {
            Some(t) => t,
            None => continue,
        };
        let cd = match row.get_item("cont_days") {
            Ok(v) => v.extract::<Option<i64>>()?,
            Err(_) => None,
        };
        for theme in row_themes(row)? {
            if !is_style(&theme) {
                expanded.push((td.clone(), code.clone(), theme, cd));
            }
        }
    }
    if expanded.is_empty() {
        return Ok(empty());
    }
    // 缺 cont_days 的成员按 (tag_date, code) 补读（注入映射；无行即不在梯队）。
    let mut missing: HashSet<(String, String)> = HashSet::new();
    for (td, code, _, cd) in &expanded {
        if cd.is_none() {
            missing.insert((td.clone(), code.clone()));
        }
    }
    let mut joined: HashMap<(String, String), i64> = HashMap::new();
    if !missing.is_empty() {
        if let Some(m) = &cont_days_map {
            for p in &missing {
                if let Some(v) = m.get(p) {
                    joined.insert(p.clone(), *v);
                }
            }
        }
    }
    // 分组（保持题材首次出现序，同 Python dict 插入序）。
    let mut order: Vec<String> = Vec::new();
    let mut groups: HashMap<String, Vec<(String, i64)>> = HashMap::new();
    for (td, code, theme, cd) in expanded {
        let days = match cd {
            Some(d) => d,
            None => match joined.get(&(td, code.clone())) {
                Some(d) => *d,
                None => continue, // 无梯队行 → 丢（INNER JOIN 语义，不记 0）
            },
        };
        if !groups.contains_key(&theme) {
            order.push(theme.clone());
        }
        groups.entry(theme).or_default().push((code, days));
    }
    let out = PyDict::new(py);
    for theme in order {
        let members = groups.remove(&theme).unwrap_or_default();
        // members 非空（setdefault 只在 append 时建键），不会触发 ValueError。
        let stats = group_stats_of_inner(&members);
        out.set_item(theme, stats.into_py(py))?;
    }
    Ok(out.into_py(py))
}

/// 伪关联检测（单票）：有效题材为空 → 该票题材关联为伪（无板块支撑）。
/// 同 Python false_relation：theme_filter(themes) 为空；code 仅作调用方对齐
/// 与日志定位（判据不含代码本身，lkl 同）。
#[pyfunction]
pub fn false_relation(_code: &str, themes: Vec<String>) -> bool {
    theme_filter(themes).is_empty()
}

/// 名称前 2 **字符**（Python `n[:2]` 是字符口径，非字节）。
fn prefix2(s: &str) -> String {
    s.chars().take(2).collect()
}

/// 纯函数：名称前2字相同但有效题材交集为空 → FALSE_RELATION 判例对
/// （例：海鸥住工 vs 海鸥股份，防名称相似误归同组）。
/// 同 Python false_relation_pairs：输入 [(code, name, tags)]，输出 [(a, b)]
/// 字符串对（a/b 含代码，供报告直出）。
#[pyfunction]
pub fn false_relation_pairs(
    rows: Vec<(String, String, Vec<String>)>,
) -> Vec<(String, String)> {
    let mut out: Vec<(String, String)> = Vec::new();
    for i in 0..rows.len() {
        for j in (i + 1)..rows.len() {
            let (c1, n1, t1) = &rows[i];
            let (c2, n2, t2) = &rows[j];
            if prefix2(n1) == prefix2(n2) {
                let f1: HashSet<String> = theme_filter(t1.clone()).into_iter().collect();
                let f2: HashSet<String> = theme_filter(t2.clone()).into_iter().collect();
                if f1.is_disjoint(&f2) {
                    out.push((format!("{}({})", n1, c1), format!("{}({})", n2, c2)));
                }
            }
        }
    }
    out
}
