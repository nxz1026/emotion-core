"""实验闭环（Lab）：用事实结果迭代判据参数，但不碰主线。

铁律（与 docs/20 一致）
1. **只写 schema `lab`**：本包所有写操作必须经 `lab.db.execute()`，它会拒绝任何指向
   `public.*` 的写语句；主线 `public` 只允许 `SELECT`。
2. **主线不得 import 本包**：`tests/architecture/test_lab_isolation.py` 会拦。
3. **没有自动晋升**：本包最多写 `lab_promotion` 提案，改主线常量永远由人做。
4. 缺数一律 `None`（F6），不补 0；`n < 30` 不给分位数。
"""
