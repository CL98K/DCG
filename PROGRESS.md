# PROGRESS — DCG 文档一致性项目

> 长期维护文件（用户 m02343 裁决：入库、每完成关键子任务覆写）。
> 新会话或上下文被压缩后，**先读本文件恢复状态**，再动手。

## 当前状态

- **最新一轮：第十五轮（评审 7 项 + 用户复核追加 2 项）——已完成、已验证、已提交 `2249970` + 本轮追加提交**
- 引擎代码本轮未改（纯文档改动）。

## 第十五轮任务与状态

| # | 项 | 落点 | 状态 |
|---|---|---|---|
| 1 | 段间 `$.` 双口径（路径拒绝 / expr 吞段） | 接口 §2.3 表后注 | ✅ 用户确认保留现状写法 |
| 2 | `function(){...}` 展开为推导式或回调 | README §3.5 正文 | ✅ |
| 3 | 「契约 §3.5」是历史引用 | 接口 §3.9 表前 + JSONata §12.5 表前 | ✅ |
| 4 | 动态下标产物四形态 + 只有 expr 合法 | 验收 §2.2 第 2 行 | ✅ 用户裁决不补产物表 |
| 5 | 动态下标 `i` 的声明前提 + **交叉引用验收 §2.2** | 接口 §3.4 两层判定后 | ✅ 用户复核追加 |
| 6 | `list`/`object` 不参与内联判据 | 接口 §4.3 表后 | ✅ |
| 7 | 「四种组合」→「四档读值口径」 | README §2.3 | ✅ |

### 与评审建议的 2 处偏差（已写入验收报告「第十五轮增补」）

1. 评审建议接口 §2.3 **加表行** `$.a.$.b → ("a","b")`；实测 `value` 形态**直接拒绝**（`value 路径非法: '$.a.$.b'（剩余 '$.b'）`），只有 `expr` 形态才吞段（→ `R['a']['b']`）→ 改为注记，不加表行。
2. 评审点名「JSONata §3.4」；该文档无 §3.4（`## 3. 路径与取值` 下无编号子节），拒绝消息列表在 §12.5 → 加在 §12.5。

## 验证记录（第十五轮）

- 文档守护：`test_doc_structure + test_doc_products + test_docs_consistency + test_doc_error_messages + test_sync_contract + test_rule_consistency` = **115 passed / 1 skipped**
- 全量：`python -X utf8 -m pytest -q` = **1415 passed / 10 skipped / 0 failed（1425）** exit 0
- 验收：`python -X utf8 evidence/accept.py` = **15 PASS / 0 FAIL / 0 SKIP**

## 最近已完成的轮次（归档）

- 第十五轮 `2249970`：评审 7 项路径/下标口径与引用纠偏（+ 用户复核追加接口 §3.4 → 验收 §2.2 交叉引用）。
- 第十四轮 `e814346`：`order_pipeline` 纳入 `bench_run.py` 协议（第 13 项）+ 判定改「连跑 3 次取比值中位数」，13 项快照重跑并同步 §5/§7.1/§7.2/§8 与 README。
- 第十三轮 `84c8964`：重跑性能快照并同步验收报告（12 项）。
- 第十二轮 `ef517e9`：语义口径与跨文档引用修正（24 项）。
- 第十一轮 `4b0bdba`：26 项（4 档读值、动态下标、`$count` 前提、`$uppercase` 产物、`validate()` 契约等）。
- 第十轮 `d02e4bf`：去「文件:行号」/去文档尺寸数值 + 尺寸自述反向守护。
- 第九轮 `5eb7db0`：`validate()` 规范形拒收（B 方案）+ 性能归因修正 + gen §6 `$indexOf` 对齐。

## 下一步（待用户指令）

- 当前无未完成项。若继续：按用户新评审或新需求开新一轮（先分解 → 实测取证 → 落盘 → 守护+全量+accept → commit → 📊 汇总）。

## 坑（踩过，别再踩）

- **文案必须以生成器产物为准**：评审建议可能含事实错误（已多次遇到），先写探针实测再落盘。
- **`generate(validate(schema))` 会二次校验失败**：`validate()` 输出是内部规范形（路径段 tuple、钩子二元组）；`dcg/schema.py` 已加 `_reject_normalized()` fail-fast。生成时直接传**原始 schema**。
- **`value` 与 `expr` 的路径语法能力不同**：`value` 不接受动态下标（`$.a[$.i]`、`$.a[i]` 都报 `value 路径下标非法`）与段间 `$.`；动态下标只能写在 `expr`。
- PowerShell 读/写 UTF-8 中文文件的 JSON/正则脚本要落成 `_tmp/fix_plan/*.py` 再跑（内联 `python -c` 的反引号与引号易被 PowerShell 吞）。
- 行数统计用 Python `splitlines()`（`Get-Content | Measure-Object -Line` 会给出错误值）。
- 文档守护要点：产物块用 `# dcg: <schema名>` 标记 + `tests/workload.py` 同名 schema 逐字节比对；报错文案须逐字符出现在文档里；`test_docs_consistency` 双向钉死 HELPERS / `__all__` / 函数分类 / 门禁名。
- 不要给 `test_sync_contract` 的 parametrize 加用例（会改变 collected 数 1425）。
- 交叉引用写法：接口讲规则、验收放实测产物表，两侧用「见《验收报告》 §2.2」互指，不复制表格（避免两处漂移）。
