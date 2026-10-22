"""近五年全市场日线下载，按股票批次断点续传。"""

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from src.config.settings import DataConfig
from src.data import ricequant
from src.data.storage import write_json

logger = logging.getLogger(__name__)


def download_data(config: DataConfig, end: str | None = None) -> Path:
    """下载原始价格、历史 ST 与除权因子，完成后提交数据集元数据。"""
    ricequant.connect(Path(config.credentials_file))
    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    latest_date = now.date() if now.hour >= 16 else now.date() - timedelta(days=1)
    end_date = pd.Timestamp(end or latest_date)
    if end_date.date() > latest_date:
        raise ValueError("当日日线需北京时间16:00后下载，不允许下载未来日期。")
    start_date = end_date - pd.DateOffset(years=config.years)
    start_text, end_text = start_date.strftime("%Y-%m-%d"), end_date.strftime(
        "%Y-%m-%d"
    )
    root = Path(config.root)
    for folder in [root, root / "daily", root / "factors"]:
        folder.mkdir(parents=True, exist_ok=True)
    plan = {
        "start_date": start_text,
        "end_date": end_text,
        "batch_size": config.batch_size,
    }
    plan_path = root / "download_plan.json"
    if plan_path.exists() and json.loads(plan_path.read_text()) != plan:
        raise ValueError("缓存区间已变化，请配置新的 data.root，避免混合数据集。")
    write_json(plan_path, plan)
    universe_path = root / "instruments.parquet"
    if universe_path.exists():
        universe = pd.read_parquet(universe_path)
    else:
        universe = ricequant.get_universe(start_text, end_text)
        universe.to_parquet(universe_path, index=False)
    calendar = ricequant.get_calendar(start_text, end_text)
    calendar.to_parquet(root / "calendar.parquet", index=False)
    symbols = universe["order_book_id"].tolist()
    row_count = 0
    observed_end = pd.Timestamp(start_text)
    for offset in range(0, len(symbols), config.batch_size):
        batch = symbols[offset : offset + config.batch_size]
        key = f"{offset // config.batch_size:04d}"
        prices_path = root / "daily" / f"{key}.parquet"
        factors_path = root / "factors" / f"{key}.parquet"
        if prices_path.exists() and factors_path.exists():
            prices = pd.read_parquet(prices_path, columns=["date"])
        else:
            prices = ricequant.get_daily_prices(batch, start_text, end_text)
            factors = ricequant.get_adjustment_factors(batch, end_text)
            factors.to_parquet(factors_path, index=False)
            prices.to_parquet(prices_path, index=False)
        row_count += len(prices)
        observed_end = max(observed_end, prices["date"].max())
        logger.info(
            "下载进度 %s/%s 只，累计 %s 行",
            min(offset + len(batch), len(symbols)),
            len(symbols),
            row_count,
        )
    expected_end = calendar["date"].iloc[-1]
    if observed_end < expected_end:
        raise ValueError(f"数据源行情仅到 {observed_end.date()}，请求终点尚无行情。")
    write_json(
        root / "metadata.json",
        {
            **plan,
            "end_date": str(expected_end.date()),
            "requested_end_date": end_text,
            "stock_count": len(symbols),
            "row_count": row_count,
            "price_adjustment": "none",
            "downloaded_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
        },
    )
    return root
