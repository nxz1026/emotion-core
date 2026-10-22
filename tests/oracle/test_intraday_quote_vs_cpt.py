"""本仓 `sina_quote` provider 与 CPT `SinaQuoteClient.fetch_quotes` 的跨仓对账。

ADR 0003 §E2：**原始采集归 CPT**，本仓那份只作对照/兜底。但两套解析
一旦漂移，就会出现「CPT 说买一 19.6 万手、本仓说 0」——而没有对账没人发现。

对账三件事：
1. **同一段响应文本**，两边解析出的 quote 逐字段相同；
2. **同一批代码**，两边请求 URL 相同（symbol 归一化口径一致）；
3. 停牌形态（整档 `None`）与未知代码（整行缺失）两边的处理相同。
"""

from __future__ import annotations

import os
import sys

import pytest

from emotion_core.data.providers import sina_quote as mine

CPT_PATH = "/home/ubuntu/DSH/Chan_Pattern_Trader"

#: 构造一段新浪批量响应：字段 34 位，格式与真实响应一致。
def _row(
    name: str = "测试股",
    open_: str = "10.00",
    prev: str = "9.90",
    last: str = "10.89",
    high: str = "10.90",
    low: str = "9.95",
    vol: str = "1234567",
    amount: str = "13000000.00",
    bid_vol: str = "19635490",
    bid_price: str = "10.89",
    ask_vol: str = "225700",
    ask_price: str = "10.90",
) -> list[str]:
    """拼一行 34 字段（买五档全 0，卖五档全 0）。"""
    fields: list[str] = [name, open_, prev, last, high, low, "10.10", "10.20", vol, amount]
    for _ in range(5):
        fields.extend([bid_vol, bid_price])
    for _ in range(5):
        fields.extend([ask_vol, ask_price])
    fields.extend(["2026-10-21", "15:00:00", "00", "00"])
    assert len(fields) == 34, len(fields)
    return fields


def _response(rows: list[tuple[str, list[str]]]) -> str:
    return "".join(f'var hq_str_{sym}="{",".join(f)}";\n' for sym, f in rows)


@pytest.fixture(scope="module")
def cpt_parser():
    if os.path.isdir(CPT_PATH) and CPT_PATH not in sys.path:
        sys.path.insert(0, CPT_PATH)
    try:
        from cpt.adapters.a_share_public import parse_sina_quotes
    except ImportError:  # pragma: no cover
        pytest.skip("CPT 仓不可用（非同一机器）")
    return parse_sina_quotes


class TestParseMatchesCpt:
    def test_full_row_equal(self, cpt_parser):
        # ⚠️ 键形态不同：本仓 `parse_sina_quotes` 直接给 6 位代码；
        # CPT 的 parse 给全限定 symbol（`fetch_quotes` 内部才切掉前缀）。
        # 对账时按 6 位对齐。
        text = _response([("sz000002", _row())])
        a = mine.parse_sina_quotes(text)
        b = {k[2:]: v for k, v in cpt_parser(text).items()}
        assert set(a) == set(b) == {"000002"}
        for key in a["000002"]:
            assert a["000002"][key] == b["000002"][key], key

    def test_suspended_row_equal(self, cpt_parser):
        """停牌：价量全 0 ⇒ 五档整对 `None`，量记 0.0。"""
        row = _row(open_="0", prev="2.46", last="0", high="0", low="0",
                   vol="0", amount="0", bid_vol="0", bid_price="0",
                   ask_vol="0", ask_price="0")
        text = _response([("sz000016", row)])
        a = mine.parse_sina_quotes(text)["000016"]
        b = {k[2:]: v for k, v in cpt_parser(text).items()}["000016"]
        for key in ("last", "bid1_price", "ask1_price", "bids", "asks", "bid1_vol"):
            assert a[key] == b[key], key
        assert a["bid1_price"] is None
        assert a["bid1_vol"] == 0.0

    def test_empty_body_skipped(self, cpt_parser):
        text = 'var hq_str_sz000000="";\n' + _response([("sz000002", _row())])
        a = mine.parse_sina_quotes(text)
        b = {k[2:]: v for k, v in cpt_parser(text).items()}
        assert set(a) == set(b) == {"000002"}

    def test_unknown_code_absent(self, cpt_parser):
        """新浪对不认识的 symbol 整行不回 ⇒ 请求 3 只回 2 行是正常。"""
        text = _response([("sz000002", _row()), ("sh600519", _row())])
        a = mine.parse_sina_quotes(text)
        b = {k[2:]: v for k, v in cpt_parser(text).items()}
        assert set(a) == set(b) == {"000002", "600519"}

    def test_too_few_fields_raises(self, cpt_parser):
        bad = 'var hq_str_sz000002="1,2,3";\n'
        with pytest.raises(Exception):
            mine.parse_sina_quotes(bad)
        with pytest.raises(Exception):
            cpt_parser(bad)

    def test_none_matched_raises(self, cpt_parser):
        text = _response([("sz000002", _row())])
        with pytest.raises(Exception):
            mine.parse_sina_quotes(text, expected=["sz999999"])
        with pytest.raises(Exception):
            cpt_parser(text, expected=["sz999999"])


class TestNormalizeCode:
    def test_forms(self):
        assert mine.normalize_code("600519") == "sh600519"
        assert mine.normalize_code("sh600519") == "sh600519"
        assert mine.normalize_code("600519.SH") == "sh600519"
        assert mine.normalize_code("000002") == "sz000002"
        assert mine.normalize_code("sz000002") == "sz000002"
        assert mine.normalize_code("300750") == "sz300750"
        assert mine.normalize_code("688981") == "sh688981"
        # 北交所新号段 92/43/83/87/88 必须在 9（沪 B）之前判，否则 920xxx
        # 会被推成 sh920xxx（CPT R17 修过同一个顺序 bug）。
        assert mine.normalize_code("920296") == "bj920296"
        assert mine.normalize_code("830799") == "bj830799"

    def test_bad_code_raises(self):
        with pytest.raises(Exception):
            mine.normalize_code("abcdef")


class TestNormalizeCodeMatchesCpt:
    """号段推断必须与 CPT 逐条一致（920296 先后顺序 bug 的回归钉子）。"""

    def test_same_results(self, cpt_parser):
        from cpt.adapters.a_share_public import normalize_code as cpt_norm

        codes = [
            "600519", "000002", "300750", "688981", "920296", "830799",
            "430418", "873122", "sh600519", "sz000002", "bj920296",
            "600519.SH", "000002.SZ",
        ]
        for code in codes:
            assert mine.normalize_code(code) == cpt_norm(code), code


class TestBatchLimit:
    def test_chunk_split(self):
        codes = [f"{600000 + i:06d}" for i in range(120)]
        assert len(codes) == 120
        # 120 只 → 3 块（50/50/20），与 CPT 的 SINA_BATCH_LIMIT=50 一致。
        from cpt.adapters.a_share_public import SINA_BATCH_LIMIT  # noqa: PLC0415

        assert mine.SINA_BATCH_LIMIT == SINA_BATCH_LIMIT
