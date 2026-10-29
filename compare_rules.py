"""导出四类选股门槛的真实K线对照。"""

import argparse
from pathlib import Path

from src.config.settings import DEFAULT_CONFIG_PATH, Settings
from src.research.rule_comparison import export_rule_comparison


def main() -> None:
    """处理对照日期和配置参数。"""
    parser = argparse.ArgumentParser(description="画出单条规则通过与未通过的真实K线")
    parser.add_argument("--date", help="默认缓存最后交易日")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    args = parser.parse_args()
    print("正在生成全市场诊断及24张规则对照K线……")
    path = export_rule_comparison(Settings.load(args.config), args.date)
    print(f"规则对照已生成：{path.resolve()}")


if __name__ == "__main__":
    main()
