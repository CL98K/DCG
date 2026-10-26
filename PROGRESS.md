# PROGRESS — DCG 项目 Lead 持久状态

> 维护约定：长期入库维护。每个关键子任务完成、每轮交付提交后覆写本文件；
> 新会话或上下文压缩后**先读本文件恢复进度**，再读用户最新指令。
> 本文件不参与文档守护测试，允许写行号与临时事实。

## 当前任务

**第十轮：文档去「文件:行号」/ 去「文档与产物尺寸数值」**（用户 m02729、m02761）。
- 状态：**已完成并提交 77dbdea，工作区 clean**。
- 裁决源：`_tmp/fix_plan/DECISIONS_v6.md`（临时，不进库）。
- 范围：README.md、DCG 接口实现细节文档.md、DCG 整体设计方案与架构文档.md、JSONata 语法.md、验收报告.md、evidence/doc_vs_impl_matrix.md；evidence/ 历史报告不动。
- 保留项（白名单）：manifest 的 `lines`/`chars` 字段、数据规模（100000 行、语料 242 条、55 函数、86 helper）、指本文表格行的「第 N 行」、字符区间「1–3 字符」。

## 下一步

1. 等用户下一步指令（本轮已提交 77dbdea，📊 汇总已交付）。
2. 后续如再改文档：先读 `_tmp/fix_plan/DECISIONS_v6.md` 的白名单口径，避免把保留项当残留删掉。

## 本轮已完成（第十轮）

- 5 份文档 + matrix：删除全部 `文件:行号` 引用（改为「文件 + 符号/结构名」）、删除文档规模表（验收 §6.1 整节含说明块）与产物尺寸注记；验收 `6.2..6.6` → `6.1..6.5` 重编号并同步内部引用。
- `evidence/gen_doc_matrix.py`：删 `doc_size()`、§0 文档规模五行、§4 规模断言行；§0 标题改「符号与计数」；matrix 重生成 **72 行**（原 78）。
- `tests/test_doc_products.py`：原「README 尺寸自述为真」用例改为反向守护 `test_readme_has_no_size_self_claims`（禁止出现尺寸自述，用例数不变）。
- `tests/gen_jsonata_manual.py`：`wrote …` 输出不再打印行数；验收报告样例行同步。
- 验收报告 §7.2 改动清单按本轮 10 文件重写；§7.1 R1 标题改「高阶函数逐元素 Python 回调开销」。

## 验证结果（本轮）

- 全量 `python -X utf8 -m pytest -q -p no:cacheprovider` → **1415 passed / 10 skipped / 0 failed（total 1425）** exit 0（日志 `_tmp/pytest_round10_final.log`）。
- 文档守护 4 文件 → **78 passed**；`evidence/accept.py` → **15 PASS / 0 FAIL / 0 SKIP**。
- 残留扫描（5 文档 + matrix）：无 `文件:\d+`、无「N 行 / M 字节/字符」、无单独「N 字符 / N 字节」；仅白名单命中。
- `git status`：10 个 M、无未跟踪、diffstat +81/−181。

## 近期已交付（归档）

- 5eb7db0 第九轮收尾：`validate()` 规范形拒收（B 方案）+ 性能归因修正 + gen §6/`$indexOf` 对齐 + 规模矩阵（8 files）。
- 4b8b89c 第八轮 22 项一致性 + AGENTS v2.1 + PROGRESS.md。
- 5b61492 第七轮 16 项；8e5762c 第六轮 18 项；0789c18 第五轮 3 项；a617cc8 第四轮 20 项 + D-BOOL-01；10af3a1 第三轮 22 项；b33a12e 第二轮 22 项 + 架构 §4.8；8e5762c / 725749a 历史报告清理与 N2 修复。

## 坑（踩过的）

- PowerShell 内联 `python -c` 含中文/反引号会转义失败 → 一律写 `_tmp/fix_plan/*.py` 脚本执行。
- `Get-Content | Measure-Object -Line` 行数不可信（会给 1509）→ 用 Python `splitlines()` 或 matrix 口径。
- 读 UTF-8 中文文件时 PowerShell 需 `-Encoding utf8`，否则乱码。
- 文档改动必须由生成器复算（产物块 `# dcg:` 标记被 `test_doc_products` 逐字节钉死）。
- `generate(validate(schema))` 会二次校验报错（`validate()` 把钩子规范成 tuple）→ 现在 `validate()` 直接拒收规范形并提示传原始 schema（第九轮 B 方案）。
- 删除文档章节时，`tests/test_doc_structure.py::test_numbered_heading_sequences_are_contiguous` 要求同级标题 1..N 连续，必须同步重编号。
- 沙箱下 pytest 跳过 10 项（正常机 4 项）；`COUNT_SUMMARY` 行是准数。
