"""单只股票的时点复权与基础指标。"""

import numpy as np
import pandas as pd

PRICE_COLUMNS = ["open", "high", "low", "close"]


def prepare_history(
    bars: pd.DataFrame,
    factors: pd.DataFrame,
    date: pd.Timestamp,
) -> pd.DataFrame:
    """先截断未来记录，再用当时已生效因子构建研究日前复权价格。"""
    history = bars.loc[bars["date"] <= date].sort_values("date").copy()
    known_factors = factors.loc[factors["ex_date"] <= date].sort_values("ex_date")
    history = pd.merge_asof(
        history,
        known_factors[["ex_date", "ex_cum_factor"]],
        left_on="date",
        right_on="ex_date",
        direction="backward",
    )
    # 下载的是上市以来全部除权事件，首个事件前及从未除权的累计因子为1。
    # 只能填基准1，不能用下一条除权因子反向填充。
    history["ex_cum_factor"] = history["ex_cum_factor"].fillna(1.0)
    scale = history["ex_cum_factor"] / history["ex_cum_factor"].iloc[-1]
    history["daily_return"] = history["close"] / history["prev_close"] - 1
    history["amplitude"] = (history["high"] - history["low"]) / history["prev_close"]
    limit_ratio = history["limit_up"] / history["prev_close"]
    history["limit_up_hit"] = (
        np.isclose(history["close"], history["limit_up"], atol=0.005, rtol=0)
        & limit_ratio.between(1.01, 1.31)
        & (history["volume"] > 0)
    )
    history[PRICE_COLUMNS] = history[PRICE_COLUMNS].mul(scale, axis=0)
    # 停牌日不压缩时间轴，也不以前后日期填充；由形态规则排除近期停牌。
    for window in [5, 10, 20, 60]:
        history[f"ma{window}"] = history["close"].rolling(window).mean()
    history["ma5_up"] = history["ma5"].diff() > 0
    return history


def longest_limit_run(flags: pd.Series) -> int:
    """计算历史窗口内连续收盘涨停的最大天数。"""
    groups = flags.ne(flags.shift()).cumsum()
    return int(flags.groupby(groups).sum().max())


def rebound_metrics(history: pd.DataFrame, trend_days: int) -> tuple[float, float]:
    """按时间先后寻找旧高与其后低点，返回回撤和现价相对旧高位置。"""
    prior = history.iloc[:-trend_days]
    peak_position = int(prior["high"].to_numpy().argmax())
    peak = prior["high"].iloc[peak_position]
    trough = prior["low"].iloc[peak_position:].min()
    return float(1 - trough / peak), float(history["close"].iloc[-1] / peak - 1)
