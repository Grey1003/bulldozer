"""构造可控的历史形态，避免测试依赖外部数据。"""

import numpy as np
import pandas as pd
import pytest


@pytest.fixture
def market_data() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """构造平缓爬坡、近期有单次涨停且无连板的股票。"""
    dates = pd.bdate_range("2025-01-02", periods=160)
    close = 10 * 1.012 ** np.arange(len(dates))
    close[-12:] *= 1.1 / 1.012
    previous = np.r_[10 / 1.012, close[:-1]]
    bars = pd.DataFrame(
        {
            "order_book_id": "000001.XSHE",
            "date": dates,
            "open": close * 0.995,
            "high": close * 1.01,
            "low": close * 0.97,
            "close": close,
            "volume": 1e7,
            "prev_close": previous,
            "limit_up": previous * 1.1,
            "is_st": False,
        }
    )
    factors = pd.DataFrame(
        {
            "order_book_id": ["000001.XSHE"],
            "ex_date": [pd.Timestamp("1991-04-03")],
            "ex_cum_factor": [1.0],
        }
    )
    instruments = pd.DataFrame(
        {
            "order_book_id": ["000001.XSHE"],
            "symbol": ["测试股票"],
            "listed_date": ["1991-04-03"],
            "de_listed_date": ["0000-00-00"],
        }
    )
    return bars, factors, instruments
