# PROGRESS — DCG

> 会话断点恢复用。每次关键阶段覆写。**最新任务在最上面。**
> 维护约定：本文件长期入库（用户 m02343 裁决），随每轮收尾更新；新会话/上下文压缩后先读本文件恢复状态。

---

## 当前任务：第九轮（用户 m02414 裁决 B 方案 + m02497 性能归因 + m02534 仅改措辞）

**状态：全部完成 —— 代码/测试/文档/规模/matrix/全量回归全绿，随本轮提交入库。**

### 本轮目标
1. **B 方案**：`dcg.validate()` 返回的规范化内部形态（段 tuple / 钩子二元组）**不得回喂** `generate()`；`validate()` 入口 fail-fast 报错并提示「把原始 schema 直接传给 generate()/build()」。原「validate 幂等」契约作废，测试与文档串同步改写。
2. **性能验收归因修正**：原「每元素要付 JSONata 语义检查（UNDEF 传播、命名报错）」不成立——`_ad` / `_ml` / `_sm` 已是纯 EAFP，成功路径零类型判断；真实成本是「每元素一次 Python 级 `lambda` 回调 + helper 调用 + Python `for` 循环」。**只改措辞，12 项数字与 `evidence/perf_protocol.json` 不动**（用户 m02534 裁决①）。
3. **`tests/gen_jsonata_manual.py`**：§12.2 `$indexOf` 归类注 prose + 渲染片段与文档 §6 / §12.2 对齐。

### 已完成
- `dcg/schema.py`：新增 `_normalized_value_path()` + `_reject_normalized()`（检测顺序：`out.fields[*].value` tuple → `output.before/after` 二元组 → `input.source` tuple），`validate()` 入口 fail-fast；模块 docstring 与 `parse_value_path` docstring 改写（不再承诺幂等）。
- `tests/test_schema.py`：`test_validate_is_idempotent` → `test_normalized_schema_is_rejected`；文档头第 4 条、两处 `generate(validate(...))` 调用改写；**112 passed**（用例数不变）。
- 接口文档 §2.4 新增「返回值是生成器内部形态，不得回喂」契约段。
- 验收报告：§6.3「第九轮增补」3 条、§7.2 本轮真实文件集重写；§0 / §5.3 / §7.1 R1·R3 / §8 共 9 处性能归因改写。
- `tests/gen_jsonata_manual.py`：§6 字符串函数表尾 +3 行 `$indexOf`、`ARRAY_ROWS` −3 行、`$sum([true,1])` note 与文档逐字一致。

### 验证结果（本轮终态）
1. 全量 `python -X utf8 -m pytest -q` → **1415 passed / 10 skipped / 0 failed（1425），exit 0**（日志 `_tmp/pytest_round9_final.log`）。
2. `python -X utf8 evidence/accept.py` → **15 PASS / 0 FAIL / 0 SKIP**。
3. 文档守护（doc_structure/doc_products/docs_consistency/doc_error_messages/sync_contract）→ **95 passed / 1 skipped（96）**。
4. `evidence/doc_vs_impl_matrix.md` 重生成 78 行，§0 与验收 §6.1 **逐值相等**（README 1907/82069、接口 899/86530、架构 932/88153、JSONata 658/66394、验收 659/63162）。
5. `tests/gen_jsonata_manual.py` render 与文档 §6（17 行）、§12.2（21 行）**DIFF=0**，`$sum([true,1])` 行逐字一致。

### 下一步
- 本轮已收口；下一轮从新需求开始。新会话/压缩后先读本文件。

### 坑 / 注意事项
- `generate(validate(sch))` 对含钩子 schema 现在会命中 **B 方案契约错误**（预期行为）；正确姿势是 `generate(schema)` / `build(schema, …)`。
- PowerShell 内联 `python -c` 写含中文/引号/反引号的代码会转义失败 → 一律写 `_tmp/*.py` 脚本再执行。
- 字节统计用磁盘值（`Get-Item Length`）或 matrix 打印值，勿用 Python `len(text.encode())`（CRLF 会少算「行数个字节」）。
- 文档守护：`test_doc_products` 按 `# dcg:` 块逐字节比对；`test_docs_consistency` 钉 86 / 96 / 55 函数 / 1425；`test_sync_contract` 钉报告计数与未跟踪清单（提交前先 `git add`）。
- 行数统计：用 Python `len(read_bytes().decode("utf-8").splitlines())` 或 matrix 口径（`count("\n")+1`）；`Get-Content | Measure-Object -Line` 会给出错误行数。
- 验收 §6.1 是三列表 `| 文档 | 行数 | 字节 |`；matrix §0 是合并列 `| \`x.md\` | N 行 / M 字节 |`——改规模时两处都要动，改完必须重跑 `gen_doc_matrix.py --write`。
- 本轮只改测试名、不新增用例，全仓 collected 应保持 **1425**。

---

## 归档（更早轮次）
- 第八轮（提交 `4b8b89c`）：22 项一致性修复（S1–S6/A1–A6/B1–B6）+ AGENTS v2.1 + PROGRESS.md 首次入库。
- 更早：`5b61492`（第七轮）、`8e5762c`（第六轮）、`0789c18`（第五轮）、`a617cc8`（第四轮 + D-BOOL-01）、`b33a12e`（第三轮）、`10af3a1`（第二轮）、`725749a`（N2 修复 + 证据清理）。
- SIMD 可行性调查（用户裁决「不做」；报告留档 `_tmp/simd_probe/SIMD-FEASIBILITY.md`，不入库）。
