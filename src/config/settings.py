"""以数据类加载独立的研究配置。"""

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "configs" / "default.json"


@dataclass(frozen=True)
class DataConfig:
    """米筐数据缓存参数。"""

    root: str = "data/raw"
    credentials_file: str = "米筐设置.txt"
    years: int = 5
    batch_size: int = 100


@dataclass(frozen=True)
class StrategyConfig:
    """形态阈值；比例均使用小数，窗口均为交易日。"""

    trend_days: int = 8
    min_listing_days: int = 120
    min_history_bars: int = 120
    min_ma5_up_ratio: float = 0.875
    min_bullish_ma_ratio: float = 0.75
    max_close_ma5_distance: float = 0.08
    max_low_ma5_distance: float = 0.035
    min_touch_ratio: float = 0.75
    max_below_ma5: float = 0.02
    min_trend_return: float = 0.06
    max_trend_return: float = 0.40
    min_mean_amplitude: float = 0.02
    max_mean_amplitude: float = 0.09
    max_daily_amplitude: float = 0.13
    max_last_return: float = 0.065
    max_last_ma5_distance: float = 0.05
    consecutive_limit_days: int = 15
    max_consecutive_limit_up: int = 1
    acceleration_days: int = 3
    max_acceleration_ratio: float = 2.5
    acceleration_min_return: float = 0.06
    rebound_days: int = 100
    rebound_min_drawdown: float = 0.18
    rebound_peak_tolerance: float = 0.08
    ma20_touch_days: int = 5
    main_board_only: bool = True
    exclude_st: bool = True

    def __post_init__(self) -> None:
        """检查决定窗口切片有效性的参数约束。"""
        if not 1 <= self.acceleration_days < self.trend_days:
            raise ValueError("acceleration_days 必须为正且小于 trend_days。")
        if self.ma20_touch_days < 1:
            raise ValueError("ma20_touch_days 必须为正。")
        required_bars = max(
            60,
            self.trend_days + 1,
            self.rebound_days,
            self.consecutive_limit_days,
            self.ma20_touch_days + 19,
        )
        if self.min_history_bars < required_bars:
            raise ValueError(f"min_history_bars 至少应为 {required_bars}。")


@dataclass(frozen=True)
class ReportConfig:
    """人工复核报告参数。"""

    root: str = "output"
    chart_bars: int = 120
    near_miss_count: int = 12


@dataclass(frozen=True)
class Settings:
    """组合数据、形态与报告配置。"""

    data: DataConfig = field(default_factory=DataConfig)
    strategy: StrategyConfig = field(default_factory=StrategyConfig)
    report: ReportConfig = field(default_factory=ReportConfig)

    @property
    def history_bars(self) -> int:
        """返回选股及单股复核共用的历史读取长度。"""
        return max(
            self.report.chart_bars + 60,
            self.strategy.min_history_bars,
            self.strategy.rebound_days,
            self.strategy.trend_days + 60,
            self.strategy.consecutive_limit_days,
        )

    @classmethod
    def load(cls, path: Path = DEFAULT_CONFIG_PATH) -> "Settings":
        """加载配置；业务相对路径统一以项目根目录为基准。"""
        content = json.loads(path.read_text(encoding="utf-8"))
        # 兼容旧配置；已删除的涨停活跃性参数不再参与任何选股条件。
        content["strategy"].pop("activity_days", None)
        content["strategy"].pop("min_limit_up_count", None)
        content["data"]["root"] = str(
            PROJECT_ROOT / content["data"].get("root", DataConfig.root)
        )
        content["data"]["credentials_file"] = str(
            PROJECT_ROOT
            / content["data"].get("credentials_file", DataConfig.credentials_file)
        )
        content["report"]["root"] = str(
            PROJECT_ROOT / content["report"].get("root", ReportConfig.root)
        )
        return cls(
            data=DataConfig(**content["data"]),
            strategy=StrategyConfig(**content["strategy"]),
            report=ReportConfig(**content["report"]),
        )

    def to_dict(self) -> dict:
        """导出可复现研究所需的完整配置。"""
        return asdict(self)
