"""典型错误形态与规则原因验证。"""

from dataclasses import replace

import pandas as pd
import pytest

from src.config.settings import StrategyConfig
from src.strategy.indicators import prepare_history, rebound_metrics
from src.strategy.rules import calculate_metrics, evaluate_rules


@pytest.mark.parametrize(
    ("changes", "failed_rule"),
    [
        ({"last_return": 0.10}, "last_day"),
        ({"max_close_distance": 0.20}, "close_distance"),
        ({"mean_amplitude": 0.008}, "amplitude"),
        ({"limit_up_count": 0}, "activity"),
        ({"max_limit_run": 3}, "no_limit_streak"),
        ({"acceleration_ratio": 4, "acceleration_return": 0.12}, "no_acceleration"),
        ({"prior_drawdown": 0.30, "prior_peak_distance": -0.02}, "no_rebound"),
    ],
)
def test_rejects_distinct_bad_shapes(
    market_data: tuple,
    changes: dict,
    failed_rule: str,
) -> None:
    """大涨、脱线、低波动、闷股、连板、加速和反弹各自触发对应规则。"""
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


def test_activity_threshold_is_configurable(market_data: tuple) -> None:
    """形态放宽需由用户修改参数，不由程序自动修改。"""
    bars, factors, _ = market_data
    history = prepare_history(bars, factors, bars["date"].max())
    config = StrategyConfig()
    metrics = calculate_metrics(history, config)
    metrics["limit_up_count"] = 0
    assert not evaluate_rules(metrics, config)["activity"]
    assert evaluate_rules(metrics, replace(config, min_limit_up_count=0))["activity"]


def test_intraday_touch_is_not_closing_limit_up(market_data: tuple) -> None:
    """盘中触板后回落不能计入收盘涨停活跃记录。"""
    bars, factors, _ = market_data
    bars.loc[bars.index[-1], "high"] = bars["limit_up"].iloc[-1]
    history = prepare_history(bars, factors, bars["date"].max())
    assert not history["limit_up_hit"].iloc[-1]
