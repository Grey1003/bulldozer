"""典型错误形态与规则原因验证。"""

import pandas as pd
import pytest

from src.config.settings import StrategyConfig
from src.reporting.rule_details import rebound_details
from src.strategy.indicators import prepare_history, rebound_metrics
from src.strategy.rules import calculate_metrics, evaluate_rules
from src.strategy.screening import screen_market


@pytest.mark.parametrize(
    ("changes", "failed_rule"),
    [
        ({"last_return": 0.10}, "last_day"),
        ({"max_close_distance": 0.20}, "close_distance"),
        ({"mean_amplitude": 0.008}, "amplitude"),
        ({"max_limit_run": 3}, "no_limit_streak"),
        ({"acceleration_ratio": 4, "acceleration_return": 0.12}, "no_acceleration"),
        ({"ma20_touch_count": 1}, "no_touch_ma20"),
    ],
)
def test_rejects_distinct_bad_shapes(
    market_data: tuple,
    changes: dict,
    failed_rule: str,
) -> None:
    """大涨、脱线、低波动、连板、加速和MA20触线各自触发对应规则。"""
    bars, factors, _ = market_data
    config = StrategyConfig()
    history = prepare_history(bars, factors, bars["date"].max())
    metrics = calculate_metrics(history, config)
    assert all(evaluate_rules(metrics, config).values())
    metrics.update(changes)
    assert not evaluate_rules(metrics, config)[failed_rule]


def test_no_prior_peak_after_trough() -> None:
    """单边上升不能被倒序拼成旧高回撤。"""
    history = pd.DataFrame(
        {
            "high": [10, 11, 12, 13, 14, 15],
            "low": [9.8, 10.8, 11.8, 12.8, 13.8, 14.8],
            "close": [9.9, 10.9, 11.9, 12.9, 13.9, 14.9],
        }
    )
    drawdown, _ = rebound_metrics(history, trend_days=2)
    assert drawdown < 0.03


def test_no_limit_up_history_is_not_a_rejection(market_data: tuple) -> None:
    """无近期涨停的合格爬坡形态应通过，不再保留活跃性筛选。"""
    bars, factors, _ = market_data
    bars["limit_up"] = bars["close"] * 1.5
    history = prepare_history(bars, factors, bars["date"].max())
    config = StrategyConfig()
    metrics = calculate_metrics(history, config)
    rules = evaluate_rules(metrics, config)
    assert "activity" not in rules
    assert all(rules.values())


def test_intraday_touch_is_not_closing_limit_up(market_data: tuple) -> None:
    """盘中触板后回落不能计入收盘涨停活跃记录。"""
    bars, factors, _ = market_data
    bars.loc[bars.index[-1], "high"] = bars["limit_up"].iloc[-1]
    history = prepare_history(bars, factors, bars["date"].max())
    assert not history["limit_up_hit"].iloc[-1]


@pytest.mark.parametrize(
    ("value", "passed"), [(0.05999, False), (0.06, True), (0.4, True), (0.40001, False)]
)
def test_new_trend_return_boundaries(
    market_data: tuple, value: float, passed: bool
) -> None:
    """6%和40%端点应通过，越界应被爬坡涨幅规则拒绝。"""
    bars, factors, _ = market_data
    history = prepare_history(bars, factors, bars["date"].max())
    metrics = calculate_metrics(history, StrategyConfig())
    metrics["trend_return"] = value
    assert evaluate_rules(metrics, StrategyConfig())["trend_return"] == passed


@pytest.mark.parametrize(("offset", "count"), [(-0.00001, 1), (0, 1), (0.00001, 0)])
def test_ma20_touch_includes_equality(
    market_data: tuple, offset: float, count: int
) -> None:
    """最低价触及或跌破当日MA20即失败，高于MA20则通过。"""
    bars, factors, _ = market_data
    history = prepare_history(bars, factors, bars["date"].max())
    last = history.index[-1]
    history.loc[last, "low"] = history.loc[last, "ma20"] * (1 + offset)
    metrics = calculate_metrics(history, StrategyConfig())
    assert metrics["ma20_touch_count"] == count
    assert evaluate_rules(metrics, StrategyConfig())["no_touch_ma20"] == (count == 0)


@pytest.mark.parametrize(("position", "count"), [(-6, 0), (-5, 1), (-1, 1)])
def test_ma20_touch_window(market_data: tuple, position: int, count: int) -> None:
    """最近五日含研究日及T-4，T-5触线不触发新条件。"""
    bars, factors, _ = market_data
    history = prepare_history(bars, factors, bars["date"].max())
    index = history.index[position]
    history.loc[index, "low"] = history.loc[index, "ma20"] * 0.99
    metrics = calculate_metrics(history, StrategyConfig())
    assert metrics["ma20_touch_count"] == count
    expected = str(history.loc[index, "date"].date()) if count else ""
    assert metrics["ma20_touch_dates"] == expected


def test_rebound_is_annotation_only(market_data: tuple) -> None:
    """满足二波代理的股票仍可通过全部筛选，报告保留其指标。"""
    bars, factors, instruments = market_data
    bars.loc[bars.index[-50], "high"] = bars["close"].iloc[-1] / 1.08 * 1.01
    row = screen_market(
        bars, factors, instruments, bars["date"].max(), StrategyConfig()
    ).diagnostics.iloc[0]
    assert row["is_rebound"]
    assert row["selected"]
    assert "pass_no_rebound" not in row
    note = rebound_details(row, StrategyConfig())
    assert note["value"].startswith("是")
    assert "不参与筛选" in note["label"]
