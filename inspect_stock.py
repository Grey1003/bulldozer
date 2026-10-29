"""输入中文名或代码，生成可按日期切换的单股复核HTML。"""

import argparse
from pathlib import Path

from src.config.settings import DEFAULT_CONFIG_PATH, Settings
from src.research.stock_inspection import export_stock_inspection


def main() -> None:
    """处理股票查询与初始日期，支持命令行或交互输入。"""
    parser = argparse.ArgumentParser(description="单股推土机条件测试文档")
    parser.add_argument("--stock", help="股票中文名、6位代码或米筐代码")
    parser.add_argument("--date", help="HTML初始研究日；默认缓存最后交易日")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    args = parser.parse_args()
    query = args.stock or input("请输入股票中文名或代码：").strip()
    print("正在逐日生成规则诊断，完成后可在HTML中切换日期……")
    path = export_stock_inspection(Settings.load(args.config), query, args.date)
    print(f"测试文档已生成：{path.resolve()}")


if __name__ == "__main__":
    main()
