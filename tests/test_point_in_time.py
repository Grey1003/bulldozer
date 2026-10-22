"""未来行情、除权和交易日边界测试。"""

from pathlib import Path

import pandas as pd
import pytest
from pandas.testing import assert_frame_equal

from src.config.settings import StrategyConfig
from src.data.storage import DataStore, write_json
from src.strategy.indicators import prepare_history
from src.strategy.screening import screen_market


def test_future_bars_and_factors_cannot_change_result(market_data: tuple) -> None:
    """追加未来暴跌和拆股因子，不得影响历史选股和已截断图形。"""
    bars, factors, instruments = market_data
    date = bars["date"].max()
    baseline = screen_market(bars, factors, instruments, date, StrategyConfig())
    future_bar = bars.tail(1).assign(date=date + pd.Timedelta(days=7), close=0.01)
    future_factor = factors.tail(1).assign(
        ex_date=date + pd.Timedelta(days=7), ex_cum_factor=100.0
    )
    altered = screen_market(
        pd.concat([bars, future_bar]),
        pd.concat([factors, future_factor]),
        instruments,
        date,
        StrategyConfig(),
    )
    assert bool(baseline.diagnostics.iloc[0]["selected"])
    assert_frame_equal(baseline.diagnostics, altered.diagnostics)
    assert_frame_equal(
        baseline.histories["000001.XSHE"], altered.histories["000001.XSHE"]
    )


def test_adjustment_only_applies_on_effective_date(market_data: tuple) -> None:
    """除权当天价格跳变经复权后连续，除权前不使用新因子。"""
    bars, factors, _ = market_data
    split_date = bars["date"].iloc[-3]
    bars.loc[bars["date"] >= split_date, ["open", "high", "low", "close"]] /= 2
    factors = pd.concat(
        [factors, factors.tail(1).assign(ex_date=split_date, ex_cum_factor=2.0)]
    )
    before = prepare_history(bars, factors, bars["date"].iloc[-4])
    after = prepare_history(bars, factors, bars["date"].max())
    assert before["close"].iloc[-1] == pytest.approx(bars["close"].iloc[-4])
    assert after["close"].iloc[-3] / after["close"].iloc[-4] == pytest.approx(1.005)


def test_weekend_and_out_of_range(tmp_path: Path) -> None:
    """周末映射到前一交易日，缓存外日期必须明确拒绝。"""
    write_json(
        tmp_path / "metadata.json",
        {"start_date": "2025-01-02", "end_date": "2025-01-10"},
    )
    pd.DataFrame({"date": pd.bdate_range("2025-01-02", "2025-01-10")}).to_parquet(
        tmp_path / "calendar.parquet"
    )
    store = DataStore(tmp_path)
    assert store.resolve_date("2025-01-05") == pd.Timestamp("2025-01-03")
    with pytest.raises(ValueError, match="数据范围"):
        store.resolve_date("2025-01-11")


def test_st_filter_uses_historical_flag(market_data: tuple) -> None:
    """即使现用名称无ST字样，研究日为ST仍需淘汰。"""
    bars, factors, instruments = market_data
    bars.loc[bars.index[-1], "is_st"] = True
    result = screen_market(
        bars, factors, instruments, bars["date"].max(), StrategyConfig()
    )
    assert not result.diagnostics.iloc[0]["selected"]
    assert result.diagnostics.iloc[0]["failed_rules"] == "研究日为ST股票"


def test_no_corporate_action_uses_unit_factor(market_data: tuple) -> None:
    """没有除权事件的股票保持原价；首次除权前不得反向填入未来因子。"""
    bars, factors, _ = market_data
    date = bars["date"].max()
    no_events = prepare_history(bars, factors.iloc[:0], date)
    first_future = factors.assign(
        ex_date=date + pd.Timedelta(days=1), ex_cum_factor=1.2
    )
    future_events = prepare_history(bars, first_future, date)
    assert_frame_equal(no_events, future_events)
    assert no_events["close"].tolist() == bars["close"].tolist()


def test_future_delisting_does_not_remove_historical_candidate(
    market_data: tuple,
) -> None:
    """后来退市的股票在其存续历史日期仍可成为候选。"""
    bars, factors, instruments = market_data
    date = bars["date"].max()
    instruments["de_listed_date"] = (date + pd.Timedelta(days=30)).strftime("%Y-%m-%d")
    result = screen_market(bars, factors, instruments, date, StrategyConfig())
    assert result.diagnostics.iloc[0]["selected"]


def test_missing_stock_session_cannot_be_compressed(market_data: tuple) -> None:
    """个股漏掉最近交易日日线时，不能把更早日期拼入连续8日。"""
    bars, factors, instruments = market_data
    other_stock = bars.assign(order_book_id="600000.XSHG")
    incomplete = pd.concat([bars.drop(bars.index[-3]), other_stock])
    result = screen_market(
        incomplete, factors, instruments, bars["date"].max(), StrategyConfig()
    )
    assert result.diagnostics.iloc[0]["failed_rules"] == "近期缺少交易日日线"
