"""Parquet 存储及按研究日期读取。"""

import json
from pathlib import Path

import pandas as pd


def write_json(path: Path, content: dict) -> None:
    """以 UTF-8 保存元数据。"""
    path.write_text(json.dumps(content, ensure_ascii=False, indent=2), encoding="utf-8")


class DataStore:
    """管理数据缓存，不参与数据源请求或形态判断。"""

    def __init__(self, root: Path) -> None:
        """设置缓存目录。"""
        self.root = root

    def read_metadata(self) -> dict:
        """读取已完成数据集的边界。"""
        return json.loads((self.root / "metadata.json").read_text(encoding="utf-8"))

    def resolve_date(self, requested: str) -> pd.Timestamp:
        """将非交易日映射到此前交易日，禁止超出缓存范围。"""
        date = pd.Timestamp(requested).normalize()
        metadata = self.read_metadata()
        if (
            not pd.Timestamp(metadata["start_date"])
            <= date
            <= pd.Timestamp(metadata["end_date"])
        ):
            raise ValueError(
                f"输入日期 {requested} 超出本地数据范围："
                f"{metadata['start_date']} 至 {metadata['end_date']}。"
                "请先下载对应区间并切换数据配置。"
            )
        calendar = pd.read_parquet(self.root / "calendar.parquet")["date"]
        return calendar[calendar <= date].iloc[-1]

    def read_window(self, date: pd.Timestamp, bars: int) -> pd.DataFrame:
        """仅读取截至 date 的所需交易日；不读取未来行情。"""
        calendar = pd.read_parquet(self.root / "calendar.parquet")["date"]
        start = calendar[calendar <= date].tail(bars).iloc[0]
        filters = [("date", ">=", start), ("date", "<=", date)]
        frames = [
            pd.read_parquet(path, filters=filters)
            for path in sorted((self.root / "daily").glob("*.parquet"))
        ]
        result = pd.concat(frames, ignore_index=True)
        expected = pd.DatetimeIndex(calendar[calendar.between(start, date)])
        if not expected.difference(result["date"].unique()).empty:
            raise ValueError("本地行情缺少完整交易日，请检查下载缓存。")
        return result

    def read_factors(self, date: pd.Timestamp) -> pd.DataFrame:
        """只读取研究日已生效的除权因子。"""
        frames = [
            pd.read_parquet(path, filters=[("ex_date", "<=", date)])
            for path in sorted((self.root / "factors").glob("*.parquet"))
        ]
        return pd.concat(frames, ignore_index=True)
