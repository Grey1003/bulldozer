"""形态指标与逐项规则；不包含下载、存储、绘图。"""

import numpy as np
import pandas as pd

from src.config.settings import StrategyConfig
from src.strategy.indicators import longest_limit_run, rebound_metrics

RULE_LABELS = {
    "recent_trading": "近期无停牌及缺失行情",
    "ma_rising": "五日均线持续向上",
    "ma_alignment": "均线多头且中期向上",
    "close_distance": "收盘未远离五日线",
    "touch_ma5": "K线下沿贴近五日线",
    "support": "收盘未明显跌破五日线",
    "trend_return": "爬坡涨幅适中",
    "amplitude": "日内振幅适中",
    "last_day": "末日未大涨或过度偏离",
    "no_limit_streak": "近期无连续涨停",
    "no_acceleration": "末段未突然加速",
    "no_touch_ma20": "近五日最低价未触及MA20",
}


def calculate_metrics(history: pd.DataFrame, config: StrategyConfig) -> dict:
    """计算截至末日的单股形态指标。"""
    recent = history.tail(config.trend_days)
    last = history.iloc[-1]
    close_distance = recent["close"] / recent["ma5"] - 1
    low_distance = (recent["low"] / recent["ma5"] - 1).abs()
    bullish = (recent["ma5"] > recent["ma10"]) & (recent["ma10"] > recent["ma20"])
    split = config.acceleration_days
    returns = recent["daily_return"].clip(lower=0)
    base_speed = max(float(returns.iloc[:-split].mean()), 0.003)
    acceleration = float(returns.tail(split).mean() / base_speed)
    tail_return = float((1 + recent["daily_return"].tail(split)).prod() - 1)
    drawdown, peak_distance = rebound_metrics(
        history.tail(config.rebound_days), config.trend_days
    )
    ma20_window = history.tail(config.ma20_touch_days)
    ma20_touch = ma20_window["low"] <= ma20_window["ma20"]
    return {
        "ma5_up_ratio": float(recent["ma5_up"].mean()),
        "bullish_ma_ratio": float(bullish.mean()),
        "ma20_rising": bool(last["ma20"] > history["ma20"].iloc[-6]),
        "max_close_distance": float(close_distance.max()),
        "min_close_distance": float(close_distance.min()),
        "touch_ratio": float((low_distance <= config.max_low_ma5_distance).mean()),
        "trend_return": float((1 + recent["daily_return"]).prod() - 1),
        "mean_amplitude": float(recent["amplitude"].mean()),
        "max_amplitude": float(recent["amplitude"].max()),
        "last_return": float(last["daily_return"]),
        "last_ma5_distance": float(last["close"] / last["ma5"] - 1),
        "max_limit_run": longest_limit_run(
            history["limit_up_hit"].tail(config.consecutive_limit_days)
        ),
        "acceleration_ratio": acceleration,
        "acceleration_return": tail_return,
        "prior_drawdown": drawdown,
        "prior_peak_distance": peak_distance,
        "is_rebound": bool(
            drawdown >= config.rebound_min_drawdown
            and peak_distance <= config.rebound_peak_tolerance
        ),
        "min_low_ma20_distance": float(
            (ma20_window["low"] / ma20_window["ma20"] - 1).min()
        ),
        "ma20_touch_count": int(ma20_touch.sum()),
        "ma20_touch_dates": "、".join(
            ma20_window.loc[ma20_touch, "date"].dt.strftime("%Y-%m-%d")
        ),
        "recent_trading": bool(
            (recent["volume"] > 0).all()
            and np.isfinite(recent[["open", "high", "low", "close"]]).all().all()
        ),
        "close": float(last["close"]),
        "ma5": float(last["ma5"]),
        # 仅用当前收盘假设下一交易日价格不变；不是次日真实均线。
        "next_ma5_estimate": float(
            (history["close"].tail(4).sum() + last["close"]) / 5
        ),
    }


def evaluate_rules(metrics: dict, config: StrategyConfig) -> dict[str, bool]:
    """将指标映射为独立规则，所有阈值由配置提供。"""
    return {
        "recent_trading": metrics["recent_trading"],
        "ma_rising": metrics["ma5_up_ratio"] >= config.min_ma5_up_ratio,
        "ma_alignment": (
            metrics["bullish_ma_ratio"] >= config.min_bullish_ma_ratio
            and metrics["ma20_rising"]
        ),
        "close_distance": metrics["max_close_distance"]
        <= config.max_close_ma5_distance,
        "touch_ma5": metrics["touch_ratio"] >= config.min_touch_ratio,
        "support": metrics["min_close_distance"] >= -config.max_below_ma5,
        "trend_return": (
            config.min_trend_return
            <= metrics["trend_return"]
            <= config.max_trend_return
        ),
        "amplitude": (
            config.min_mean_amplitude
            <= metrics["mean_amplitude"]
            <= config.max_mean_amplitude
            and metrics["max_amplitude"] <= config.max_daily_amplitude
        ),
        "last_day": (
            metrics["last_return"] <= config.max_last_return
            and metrics["last_ma5_distance"] <= config.max_last_ma5_distance
        ),
        "no_limit_streak": metrics["max_limit_run"] <= config.max_consecutive_limit_up,
        "no_acceleration": not (
            metrics["acceleration_ratio"] > config.max_acceleration_ratio
            and metrics["acceleration_return"] > config.acceleration_min_return
        ),
        "no_touch_ma20": metrics["ma20_touch_count"] == 0,
    }
