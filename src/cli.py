"""命令行仅编排下载、选股与导出，不实现形态逻辑。"""

import argparse
import logging
from pathlib import Path

import pandas as pd

from src.config.settings import DEFAULT_CONFIG_PATH, Settings
from src.data.download import download_data
from src.data.storage import DataStore
from src.reporting.report import export_report
from src.strategy.screening import screen_market

logger = logging.getLogger(__name__)


def run_screen(settings: Settings, requested: str) -> Path:
    """从本地缓存读取时点数据并生成单日复核报告。"""
    store = DataStore(Path(settings.data.root))
    date = store.resolve_date(requested)
    config = settings.strategy
    bars = store.read_window(date, settings.history_bars)
    factors = store.read_factors(date)
    instruments = pd.read_parquet(store.root / "instruments.parquet")
    logger.info("研究日 %s，读取 %s 行日线", date.date(), len(bars))
    result = screen_market(bars, factors, instruments, date, config)
    directory = export_report(result, settings, requested, date, store.read_metadata())
    logger.info("入选 %s 只，报告 %s", result.diagnostics["selected"].sum(), directory)
    return directory


def main() -> None:
    """处理 download 和 screen 两个独立命令。"""
    parser = argparse.ArgumentParser(description="推土机日线下载与单日形态选股")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    commands = parser.add_subparsers(dest="command", required=True)
    download = commands.add_parser("download", help="下载近五年全市场日线")
    download.add_argument("--end", help="下载终点，默认昨日")
    screen = commands.add_parser("screen", help="按指定日期收盘后的信息选股")
    screen.add_argument(
        "--date", required=True, help="YYYY-MM-DD；周末映射至此前交易日"
    )
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    settings = Settings.load(args.config)
    if args.command == "download":
        download_data(settings.data, args.end)
    else:
        run_screen(settings, args.date)
