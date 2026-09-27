// TODO(审核文档 §9 第 9 条): 本模块目前只是占位——`pub struct Ecosystem;`，
// 既无评级逻辑，也未在 lib.rs 里注册 pyfunction，故生态评级实际只有
// algorithms/dragon_env.py 一份 Python 实现。
// 移植范围（按其它 8 个模块同规格）：dragon_env 的纯判定 g1~g4 / b1~b5 与 _verdict，
// 落成 pyfunction 后加 tests/oracle/test_ecosystem_rust_vs_python.py 同输入同输出对账。
// 未完成前不要在任何文档/注释里声称"已有 Rust 实现"。
pub struct Ecosystem;
