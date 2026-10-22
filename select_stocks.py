"""交互输入日期的便捷选股入口。"""

from src.cli import run_screen
from src.config.settings import Settings

if __name__ == "__main__":
    settings = Settings.load()
    selected_date = input("请输入选股日期（YYYY-MM-DD，按该日收盘后选股）：").strip()
    report_directory = run_screen(settings, selected_date)
    print(f"报告已生成：{report_directory.resolve() / 'index.html'}")
