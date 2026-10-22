"""米筐接口适配；凭据不进入业务配置或日志。"""

import ast
import os
from pathlib import Path

import pandas as pd
import rqdatac as rq

PRICE_FIELDS = [
    "open",
    "high",
    "low",
    "close",
    "volume",
    "total_turnover",
    "prev_close",
    "limit_up",
    "limit_down",
]


def connect(credentials_file: Path) -> None:
    """优先读取环境变量，否则静态解析文件中的 rq.init 字面量参数。"""
    if os.environ.get("RQDATAC_URI"):
        rq.init(os.environ["RQDATAC_URI"])
        return
    if os.environ.get("RQDATAC_USERNAME"):
        rq.init(os.environ["RQDATAC_USERNAME"], os.environ["RQDATAC_PASSWORD"])
        return
    # 只解析 init 所在行，不执行凭据文件中的 Python 代码。
    content = credentials_file.read_text(encoding="utf-8-sig")
    line = next(line for line in content.splitlines() if "rq.init(" in line)
    call = ast.parse(line.strip()).body[0].value
    rq.init(*[ast.literal_eval(arg) for arg in call.args])


def get_universe(start: str, end: str) -> pd.DataFrame:
    """保留下载区间内存在过的股票，包含后来退市的股票。"""
    universe = rq.all_instruments(type="CS")
    listed = pd.to_datetime(universe["listed_date"], errors="coerce")
    delisted = pd.to_datetime(
        universe["de_listed_date"], format="%Y-%m-%d", errors="coerce"
    )
    mask = (listed <= pd.Timestamp(end)) & (
        delisted.isna() | (delisted >= pd.Timestamp(start))
    )
    columns = ["order_book_id", "symbol", "listed_date", "de_listed_date"]
    return universe.loc[mask, columns].sort_values("order_book_id")


def get_daily_prices(symbols: list[str], start: str, end: str) -> pd.DataFrame:
    """获取不复权日线及历史 ST 标记，保留零成交停牌记录。"""
    prices = rq.get_price(
        symbols,
        start,
        end,
        frequency="1d",
        fields=PRICE_FIELDS,
        adjust_type="none",
        skip_suspended=False,
        expect_df=True,
    ).reset_index()
    status = rq.is_st_stock(symbols, start, end)
    status.index.name = "date"
    status.columns.name = "order_book_id"
    status = status.stack(future_stack=True).rename("is_st").reset_index()
    merged = prices.merge(
        status, on=["order_book_id", "date"], how="left", validate="one_to_one"
    )
    if merged["is_st"].isna().any():
        raise ValueError("日线缺少对应日期的历史ST状态，下载中止。")
    return merged


def get_adjustment_factors(symbols: list[str], end: str) -> pd.DataFrame:
    """获取截至终点的累计复权因子，保留上市以来的基准因子。"""
    factors = rq.get_ex_factor(symbols, end_date=end)
    if factors is None:
        return pd.DataFrame(
            {
                "order_book_id": pd.Series(dtype="str"),
                "ex_date": pd.Series(dtype="datetime64[ns]"),
                "ex_cum_factor": pd.Series(dtype="float64"),
            }
        )
    factors = factors.reset_index()
    return factors[["order_book_id", "ex_date", "ex_cum_factor"]]


def get_calendar(start: str, end: str) -> pd.DataFrame:
    """读取沪深交易日历。"""
    return pd.DataFrame({"date": pd.to_datetime(rq.get_trading_dates(start, end))})
