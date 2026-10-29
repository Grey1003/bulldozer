"""将共用规则诊断转换成可人工核对的数值与门槛。"""

from src.config.settings import StrategyConfig
from src.strategy.rules import RULE_LABELS


def rule_details(row: dict, config: StrategyConfig) -> list[dict]:
    """返回每条规则的实际值、门槛和Python引擎判定。"""
    if not row["eligible"]:
        return [
            {
                "key": "eligibility",
                "label": "股票资格",
                "value": row["failed_rules"],
                "threshold": "须先通过资格，再评估形态",
                "passed": False,
            }
        ]
    values = {
        "recent_trading": (
            "完整" if row["recent_trading"] else "存在异常",
            "成交量>0，OHLC有限",
        ),
        "ma_rising": (
            f"{row['ma5_up_ratio']:.1%}",
            f"上升比例≥{config.min_ma5_up_ratio:.1%}",
        ),
        "ma_alignment": (
            f"多头比例{row['bullish_ma_ratio']:.1%}；MA20向上：{row['ma20_rising']}",
            f"多头比例≥{config.min_bullish_ma_ratio:.1%}且MA20[T]>MA20[T-5]",
        ),
        "close_distance": (
            f"窗口最大偏离{row['max_close_distance']:.5%}",
            f"≤{config.max_close_ma5_distance:.1%}",
        ),
        "touch_ma5": (
            f"贴线{row['touch_ratio']:.1%}",
            f"低点绝对偏离≤{config.max_low_ma5_distance:.1%}"
            f"的天数占比≥{config.min_touch_ratio:.0%}",
        ),
        "support": (
            f"窗口最小偏离{row['min_close_distance']:.5%}",
            f"≥-{config.max_below_ma5:.1%}",
        ),
        "trend_return": (
            f"{row['trend_return']:.2%}",
            f"{config.min_trend_return:.0%}～{config.max_trend_return:.0%}",
        ),
        "amplitude": (
            f"均值{row['mean_amplitude']:.2%}；最大{row['max_amplitude']:.2%}",
            f"均值{config.min_mean_amplitude:.0%}～{config.max_mean_amplitude:.0%}；"
            f"最大≤{config.max_daily_amplitude:.0%}",
        ),
        "last_day": (
            f"日涨幅{row['last_return']:.2%}；距MA5{row['last_ma5_distance']:.2%}",
            f"涨幅≤{config.max_last_return:.1%}；偏离≤{config.max_last_ma5_distance:.1%}",
        ),
        "no_limit_streak": (
            f"最大{row['max_limit_run']}连板",
            f"近{config.consecutive_limit_days}日不超过{config.max_consecutive_limit_up}连板",
        ),
        "no_acceleration": (
            f"{row['acceleration_ratio']:.5f}倍；末段涨幅{row['acceleration_return']:.5%}",
            f"淘汰：同时>{config.max_acceleration_ratio}倍"
            f"且>{config.acceleration_min_return:.0%}",
        ),
        "no_touch_ma20": (
            f"最低价距MA20最小值{row['min_low_ma20_distance']:.5%}；"
            f"触线{row['ma20_touch_count']}日：{row['ma20_touch_dates'] or '无'}",
            f"近{config.ma20_touch_days}日每天最低价>当日MA20；触及或跌破即淘汰",
        ),
    }
    return [
        {
            "key": key,
            "label": label,
            "value": values[key][0],
            "threshold": values[key][1],
            "passed": bool(row[f"pass_{key}"]),
        }
        for key, label in RULE_LABELS.items()
    ]


def rebound_details(row: dict, config: StrategyConfig) -> dict:
    """返回二波反弹提示及指标，仅展示，不进入规则集合。"""
    return {
        "label": "明显二波反弹（仅提示，不参与筛选）",
        "value": ("是" if row["is_rebound"] else "否")
        + f"；旧高回撤{row['prior_drawdown']:.2%}；"
        f"现价距旧高{row['prior_peak_distance']:.2%}",
        "threshold": f"回看{config.rebound_days}日、排除末{config.trend_days}日；"
        f"回撤≥{config.rebound_min_drawdown:.0%}且距旧高≤"
        f"{config.rebound_peak_tolerance:.0%}时标为是",
    }
