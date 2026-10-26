# PROGRESS — DCG 项目 Lead 持久状态

> 维护约定：长期入库维护。每个关键子任务完成、每轮交付提交后覆写本文件；
> 新会话或上下文压缩后**先读本文件恢复进度**，再读用户最新指令。
> 本文件不参与文档守护测试，允许写行号与临时事实。

## 当前任务

**第十一轮：用户 m02921 的 26 项（5 设计级 + 1 示例 + 20 对不齐/写作）**。
- 状态：**已完成并提交（本轮 commit：第十一轮对齐），工作区 clean**（26 项全部落盘，全量 1415/10/1425 + accept 15 PASS）。
- 裁决源：`_tmp/fix_plan/DECISIONS_v7.md`（唯一 canonical，含 §0 实测 ground truth）。
- 写域：t2=README.md（task-23）、t3=接口+架构（task-24）、t4=JSONata+dcg/jx/policy.py（task-25）、Lead=验收报告+PROGRESS。

## 实测 ground truth（本轮已用探针固定）

- 无 optional → 裸链 `R['zz']`，缺键 `KeyError`；加 optional 才 `_ix_get` + `None` 守卫。
- `$count(xs)`（xs=type list 且 required）→ `len(xs)`；`$count($.xs)` 即使 required list 也 → `_ct(_xs)`（路径读值不参与类型证明）。
- `$uppercase($.sku)` → `_sku.upper()`；裸名 `sku` → `sku.upper()`；无 `_up(_sku)`。
- `$.a[i]`：required → `R['a'][i]`；optional+具体类型 → `.get` 链 + 守卫后 `_a[i]`；optional+any → `_ix_get` 链。
- `_hb('abc')` → `TypeError: output.before: 必须返回数组（行集合），得到 string`。
- `before:"@json.dumps"` 产物带 `_hb(...)`，运行期必被拒。

## 已完成（本轮 Lead）

- 验收报告 6 处：§0 真缺陷分类改为「静默脏结果/运行期崩溃/性能设计/语义分裂」；§0 ② 旧归因改「每元素 Python 回调」；§5.5 旧句替换；§2.1 表断裂修好；§6.2「缺缺失」→「缺失时」；§6.3「这三类守护」→「上述守护（+语料行为断言）」。
- 验证：`test_doc_structure` + `test_doc_error_messages` 30 passed。

## 下一步

1. 等用户下一步指令（本轮已提交，📊 汇总已交付）。
2. 本轮与用户建议的偏差已在汇总中说明：`$count($.xs)` 注法（路径读值不参与类型证明）、`$uppercase` 实际产物 `_sku.upper()`、动态下标按 optional+类型分档。

## 验证结果（本轮）

- 全量 `pytest -q` → **1415 passed / 10 skipped / 0 failed（1425）**；`evidence/accept.py` → **15 PASS / 0 FAIL / 0 SKIP**；四守护 78 passed。
- `git status`：clean；提交 8 files、+102/−85。

## 坑（踩过的）

- PowerShell 内联 `python -c` 含中文/反引号会转义失败 → 写 `_tmp/fix_plan/*.py` 脚本。
- `Get-Content | Measure-Object -Line` 行数不可信 → 用 Python `splitlines()`。
- 文档产物块必须 `dcg.generate()` 实测重算，`# dcg:` 块被 `test_doc_products` 逐字节钉死。
- `generate(validate(schema))` 会二次校验报错（第九轮 B 方案已改为 validate 拒收规范形）。
- 删章节/改标题要同步编号连续性守护（`test_doc_structure`）。
- 沙箱下 pytest 跳过 10 项（正常机 4 项）；`COUNT_SUMMARY` 行是准数。
- 用户建议可能含事实错误（本轮：`$count($.xs)` 归因、`$uppercase` 产物、动态下标分档），一律先探针再落文档。

## 近期已交付（归档）

- d02e4bf 第十轮：去行号/去尺寸 + 尺寸自述反向守护（10 files）。
- 5eb7db0 第九轮收尾：`validate()` 规范形拒收（B 方案）+ 性能归因修正 + gen §6/`$indexOf` 对齐 + 规模矩阵（8 files）。
- 4b8b89c 第八轮 22 项一致性 + AGENTS v2.1 + PROGRESS.md；5b61492 第七轮 16 项；8e5762c 第六轮 18 项；0789c18 第五轮 3 项；a617cc8 第四轮 20 项 + D-BOOL-01；10af3a1 第三轮 22 项；b33a12e 第二轮 22 项 + 架构 §4.8。
