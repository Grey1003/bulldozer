# 架构和命名约定

项目按功能拆为配置、数据、策略、报告四个模块；每个模块的内部文件职责单一。
参考《积优Python代码规范指南》及用户AGENTS要求，优先保持简洁、高内聚、低耦合。

| 层 | 输入 | 输出 | 边界 |
| --- | --- | --- | --- |
| data.ricequant | 代码与日期 | 原始DataFrame | 独占米筐SDK访问 |
| data.download | DataConfig | Parquet快照 | 处理批次和下载清单 |
| data.storage | 日期和窗口 | 截断数据 | 处理本地读取与研究日映射 |
| strategy.indicators | 单股历史及因子 | 指标历史 | 不读取路径、不访问网络 |
| strategy.rules | 指标与StrategyConfig | 布尔规则 | 不排序、不写文件 |
| strategy.screening | 全市场时点数据 | ScreenResult | 股票资格与规则组合 |
| reporting | ScreenResult与参数 | PNG/HTML/CSV | 不重新定义选股标准 |
| cli | 用户参数 | 完整流程 | 只做编排 |

文件、变量、函数统一 `snake_case`，类使用 `PascalCase`，常量使用
`UPPER_SNAKE_CASE`。日期列统一 `date`，米筐代码统一 `order_book_id`，
布尔规则列统一 `pass_规则名`，淘汰原因统一 `failed_rules`。

新增选股条件时，先在 `StrategyConfig` 和JSON中定义阈值，
在 `calculate_metrics` 中计算指标，再在 `evaluate_rules` 中实现条件，
在 `RULE_LABELS` 中写明可读说明。不要把条件复制到绘图层或下载层。

每个函数保留精简标准文档字符串与类型注解；仅在数据边界实施必要检查，
不捕获全部异常、不静默跳过下载失败、不用默认数据伪装成功。
没有业务需求时不添加数据库、ORM、继承体系或通用工具大类。

依赖方向为 `cli → data/strategy/reporting`，
`reporting → strategy结果和标签`，`strategy → config`。
配置层只依赖标准库，计算层与米筐解耦，测试不需要网络或账号。
