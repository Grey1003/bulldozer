"""按默认配置下载近五年日线的便捷入口。"""

import logging

from src.config.settings import Settings
from src.data.download import download_data

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    settings = Settings.load()
    data_directory = download_data(settings.data)
    print(f"日频数据已缓存：{data_directory.resolve()}")
