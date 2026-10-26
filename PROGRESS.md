# PROGRESS — DCG 项目 Lead 持久状态

> 维护约定：长期入库维护。每个关键子任务完成、每轮交付提交后覆写本文件；
> 新会话或上下文压缩后**先读本文件恢复进度**，再读用户最新指令。
> 本文件不参与文档守护测试，允许写行号与临时事实。

## 当前任务

**第十二轮：用户 m03052 的 24 项（6 设计/语义 + 12 对不齐 + 6 写作）**。
- 状态：**执行中**——三 teammate 已交付（task-26/27/28 均 completed），Lead 已收口验收报告，剩全量验证结论落盘 + commit + 📊 汇总。
- 裁决源：`_tmp/fix_plan/DECISIONS_v8.md`（§0 探针 ground truth 5 条 / §1 C1–C12 canonical / §2 写域 / §3 收尾）。
- 写域：t2=README.md（task-26）、t3=接口+架构（task-27）、t4=JSONata+gen 脚本（task-28）、Lead=验收报告+PROGRESS。

## 本轮实测 ground truth（探针 `_tmp/fix_plan/probe_round12*.py`）

- optional 守卫两形态并存：`shape:"list"` 行首 `_t1=...` + `x=None if _t1 is _U else _t1`；`shape:"dict"`/`"rows"` 行内 `None if (_v:=...) is _U else _v`。
- 动态下标两层：①字段级 optional/default 决定下标访问（required → `base[idx]`；optional → `_ix_get(base, idx)`）；②基座读法（裸名 required 裸链 / optional+具体 type `.get` 快链 / optional+any `_ix_get`；`$.` 路径基座一律 `_ix_get`）。叠加实测：required 字段+路径基座 → `R['xs'][R['i']]`；optional 字段+路径基座 → `_t1=_ix_get(_ix_get(R,'xs'),_ix_get(R,'i'))`；optional 字段+required 裸名基座 → `xs=R['xs']` + `_t1=_ix_get(xs,_ix_get(R,'i'))`。
- `$uppercase($.sku)`（单次）→ `_up(R['sku'])`；同路径多次引用 → `_sku=R['sku']` 绑定后 `_up(_sku)`；裸名 `str` → `sku.upper()`；裸名 `any` → `_up(sku)`。
- `$sum($.xs)` → `_sm(R['xs'])`（不内联 `sum()`）；`$count($.xs)` → `_ct(R['xs'])`；仅 `$count` 在裸名 list+required 时内联 `len()`。
- `expr: "$.a[$.hour].v"` 合法 → `R['a'][R['hour']]['v']`；`expr: "$.a.$.hour.v"` 被编译成 `R['a']['hour']['v']`（`$.` 被吞、`hour` 当字面字段名）——已在文档加警示。
- `expr: "$.a + 1"` 运行 `[{'a':'a'}]` → `TypeError: $add: 操作数必须是数值`（非 undefined）。

## 本轮已完成

- Lead：验收 §6.1 `SOURCE_NEEDS_MATH` 行精简 + 表后注；「第十二轮增补」6 条；§7.2 改本轮 6 文件清单（上一提交 4b0bdba）；文档守护 73 passed。
- t2 README（1915→1917 行，+4/−2）：§3.1 `$函数名` 行按实测重写、§2.3 zz 前置说明、§3.7 动态下标两层判定；Lead 已删前置句里与块后加粗句重复的「不声明 optional → KeyError」从句。
- t3 接口 5 处 + 架构 1 处：§4.3 守卫两形态段、§3.4 两层判定段、§4.3 表第 6 行、§1.2 index 注释、§4.3 判据首行文字化、架构 §7.5 措辞。
- t4 JSONata 4 处（§13.6 第 3 条、§3 写法建议、§15.2 引用、§13.9 引用+删 89%），并同步 `tests/gen_jsonata_manual.py` 的 §3 行说明。
- 验证：全量 `pytest -q` → **1415 passed / 10 skipped / 0 failed（1425）** exit 0；`evidence/accept.py` → **15 PASS / 0 FAIL / 0 SKIP**。

## 下一步

1. `git status` 核对 → `git add -A && git commit`（建议信息：`docs: 第十二轮语义口径与跨文档引用修正`）。
2. 输出 📊 汇总（24 项逐项 + 用户建议与实测不符 2 处说明）。

## 坑（踩过的）

- PowerShell 内联 `python -c` 含中文/反引号/`$` 会转义失败 → 写 `_tmp/fix_plan/*.py` 脚本（或 `chr()` 拼字符串）。
- `Get-Content | Measure-Object -Line` 行数不可信 → 用 Python `splitlines()`。
- 文档产物块必须 `dcg.generate()` 实测重算，`# dcg:` 块被 `test_doc_products` 逐字节钉死。
- `generate(validate(schema))` 会二次校验报错（第九轮 B 方案：`validate()` 拒收规范形）。
- 删章节/改标题要同步编号连续性守护（`test_doc_structure`）。
- 沙箱下 pytest 跳过 10 项（正常机 4 项）；`COUNT_SUMMARY` 行是准数。
- 用户建议可能含事实错误（本轮：`$uppercase` 产物「`_sku.upper()`」不成立、守卫「统一成临时变量」实际两形态并存），一律先探针再落文档。
- edit 工具要求先用 read 工具读过文件；file changed 时报错就重读。

## 近期已交付（归档）

- 4b0bdba 第十一轮：optional/type 职责、`$count` 前提、`validate` 契约、示例修正（8 files）。
- d02e4bf 第十轮：去行号/去尺寸 + 尺寸自述反向守护（10 files）。
- 5eb7db0 第九轮收尾：`validate()` 规范形拒收（B 方案）+ 性能归因修正 + gen §6/`$indexOf` 对齐（8 files）。
- 4b8b89c 第八轮 22 项 + AGENTS v2.1 + PROGRESS.md；5b61492 第七轮 16 项；8e5762c 第六轮 18 项；0789c18 第五轮 3 项；a617cc8 第四轮 20 项 + D-BOOL-01；b33a12e 第三轮 22 项；10af3a1 第二轮 22 项 + 架构 §4.8。
