"""单日全市场筛选与逐股诊断。"""

from dataclasses import dataclass

import pandas as pd

from src.config.settings import StrategyConfig
from src.strategy.indicators import prepare_history
from src.strategy.rules import RULE_LABELS, calculate_metrics, evaluate_rules

MAIN_BOARD_PREFIXES = ("000", "001", "002", "003", "600", "601", "603", "605")


@dataclass
class ScreenResult:
    """单次筛选的诊断和已截断历史。"""

    diagnostics: pd.DataFrame
    histories: dict[str, pd.DataFrame]


def eligibility_reason(
    instrument: pd.Series,
    bars: pd.DataFrame,
    date: pd.Timestamp,
    config: StrategyConfig,
) -> str:
    """按研究日期的上市、交易和 ST 状态检查候选资格。"""
    if config.main_board_only and not instrument["order_book_id"].startswith(
        MAIN_BOARD_PREFIXES
    ):
        return "非沪深主板A股"
    listed = pd.Timestamp(instrument["listed_date"])
    delisted = pd.to_datetime(instrument["de_listed_date"], errors="coerce")
    if listed > date or (pd.notna(delisted) and delisted <= date):
        return "研究日未上市或已退市"
    if bars.empty or bars["date"].max() != date:
        return "研究日缺少行情"
    if (
        len(bars) < config.min_history_bars
        or (date - listed).days < config.min_listing_days
    ):
        return "上市时间或历史长度不足"
    last = bars.sort_values("date").iloc[-1]
    if last["volume"] <= 0:
        return "研究日停牌"
    if pd.isna(last["is_st"]):
        return "研究日缺少ST状态"
    if config.exclude_st and bool(last["is_st"]):
        return "研究日为ST股票"
    return ""


def screen_market(
    bars: pd.DataFrame,
    factors: pd.DataFrame,
    instruments: pd.DataFrame,
    date: pd.Timestamp,
    config: StrategyConfig,
) -> ScreenResult:
    """只基于 date 收盘前已产生的日线执行形态筛选。"""
    bars = bars.loc[bars["date"] <= date]
    grouped_bars = dict(tuple(bars.groupby("order_book_id", sort=False)))
    grouped_factors = dict(tuple(factors.groupby("order_book_id", sort=False)))
    recent_dates = bars["date"].drop_duplicates().sort_values().tail(config.trend_days)
    rows, histories = [], {}
    for _, instrument in instruments.iterrows():
        symbol = instrument["order_book_id"]
        stock_bars = grouped_bars.get(symbol, bars.iloc[:0])
        reason = eligibility_reason(instrument, stock_bars, date, config)
        if not reason and not stock_bars["date"].sort_values().tail(
            config.trend_days
        ).reset_index(drop=True).equals(recent_dates.reset_index(drop=True)):
            reason = "近期缺少交易日日线"
        row = {
            "order_book_id": symbol,
            "symbol": instrument["symbol"],
            "eligible": not bool(reason),
            "selected": False,
            "failed_count": len(RULE_LABELS) + 1,
            "failed_rules": reason,
        }
        if reason:
            rows.append(row)
            continue
        history = prepare_history(
            stock_bars, grouped_factors.get(symbol, factors.iloc[:0]), date
        )
        metrics = calculate_metrics(history, config)
        rules = evaluate_rules(metrics, config)
        failures = [RULE_LABELS[name] for name, passed in rules.items() if not passed]
        row.update(metrics)
        row.update({f"pass_{name}": passed for name, passed in rules.items()})
        row.update(
            selected=not failures,
            failed_count=len(failures),
            failed_rules="；".join(failures),
        )
        rows.append(row)
        histories[symbol] = history
    diagnostics = pd.DataFrame(rows).sort_values(
        ["failed_count", "order_book_id"], kind="stable"
    )
    return ScreenResult(diagnostics.reset_index(drop=True), histories)
