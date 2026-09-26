"""契约层：全项目唯一的类型定义。

纪律（审核文档 S4）：
- 这些 dataclass 是全项目唯一的类型定义
- Rust 侧用同名同字段的 struct（serde 对齐字段名）
- 展示层只读 snapshot.py 的视图模型，不直接拼裸 DB 行
- 字段级标注口径：volume: int  # 手（非股）/ turnover_rate: float  # 百分数（非比值）
- 价格一律用分（int cents），消灭浮点
"""
