"""单股历史文档与共用选股引擎的一致性和未来数据隔离。"""

import pandas as pd
import pytest

from src.config.settings import Settings
from src.reporting.rule_details import rule_details
from src.research.stock_inspection import build_stock_payload, resolve_stock
from src.strategy.screening import screen_market


@pytest.mark.parametrize("query", ["000001", "000001.XSHE", "000001.SZ", "测试股票"])
def test_name_and_code_resolution(market_data: tuple, query: str) -> None:
    """中文名、短代码和两种交易所后缀应找到同一股票。"""
    _, _, instruments = market_data
    assert resolve_stock(query, instruments)["order_book_id"] == "000001.XSHE"


def test_inspector_matches_market_and_truncates_future(market_data: tuple) -> None:
    """追加未来价格变化和除权因子，不能改变过去日期的诊断和图形前缀。"""
    bars, factors, instruments = market_data
    # 真实缓存的日历为毫秒，行情为微秒，日期相同不能误报缺失。
    calendar = bars["date"].reset_index(drop=True).astype("datetime64[ms]")
    date = calendar.iloc[-6]
    settings = Settings()
    baseline = build_stock_payload(
        bars, factors, instruments.iloc[0], calendar, settings
    )
    market = screen_market(bars, factors, instruments, date, settings.strategy)
    row = market.diagnostics.iloc[0].to_dict()
    day = str(date.date())
    assert baseline["diagnoses"][day]["selected"] == bool(row["selected"])
    assert baseline["diagnoses"][day]["details"] == rule_details(row, settings.strategy)
    altered = bars.copy()
    altered.loc[altered["date"] > date, ["open", "high", "low", "close"]] *= 5
    future_factor = factors.assign(ex_date=calendar.iloc[-5], ex_cum_factor=20.0)
    mutated = build_stock_payload(
        altered,
        pd.concat([factors, future_factor]),
        instruments.iloc[0],
        calendar,
        settings,
    )
    assert baseline["diagnoses"][day] == mutated["diagnoses"][day]
    assert [p for p in baseline["prices"] if p["date"] <= day] == [
        p for p in mutated["prices"] if p["date"] <= day
    ]


def test_ambiguous_name_requires_specific_code(market_data: tuple) -> None:
    """模糊股票名匹配多只时不能静默选第一只。"""
    _, _, instruments = market_data
    other = instruments.assign(order_book_id="600000.XSHG", symbol="测试股份")
    with pytest.raises(ValueError, match="唯一"):
        resolve_stock("测试", pd.concat([instruments, other]))
