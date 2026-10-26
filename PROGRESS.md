# PROGRESS — DCG 项目 Lead 持久状态

> 维护约定：长期入库维护。每个关键子任务完成、每轮交付提交后覆写本文件；
> 新会话或上下文压缩后**先读本文件恢复进度**，再读用户最新指令。
> 本文件不参与文档守护测试，允许写行号与临时事实。

## 当前任务

**第十三轮：用户 m03196「重跑 `evidence/bench_run.py` 刷新性能快照并同步验收报告」**。
- 状态：**编辑与验证已完成，待提交**（12 项主基准重跑、`perf_protocol.json` 刷新、验收报告 §0/§5.1/§5.2/§5.3/§5.5/§7.1/§7.2/§8 同步、全量回归与门禁全绿）。
- 裁决源：无独立裁决文件（纯数据刷新，以新 `evidence/perf_protocol.json` 为唯一事实源）。

## 本轮实测（新的 12 项快照，`generated_at=2026-10-26T23:44:09+0800`）

| 用例 | DCG µs/行 | 手写 µs/行 | 比值 | 判定 |
|---|---|---|---|---|
| shape_rows | 0.2002 | 0.4123 | 0.49× | 更快 |
| agg_direct | 0.1020 | 0.1266 | 0.81× | 更快 |
| shape_dict | 0.1451 | 0.1462 | 0.99× | 持平 |
| agg_container | 0.1740 | 0.1763 | 0.99× | 持平 |
| optwide_missing | 0.6232 | 0.6078 | 1.03× | 持平 |
| shape_list | 0.1968 | 0.1843 | 1.07× | 持平 |
| shape_index | 0.3033 | 0.2699 | 1.12× | 慢 |
| hires_filter | 0.3964 | 0.3230 | 1.23× | 慢 |
| optwide_hit | 0.2036 | 0.1597 | 1.27× | 慢 |
| hires_sort | 0.4595 | 0.3598 | 1.28× | 慢 |
| hires_map | 0.4266 | 0.2332 | 1.83× | 慢 |
| hires_reduce | 0.5055 | 0.1928 | 2.62× | 慢 |

- 判定汇总：**2 项更快 + 4 项持平 = 6 项不落后；6 项偏慢**（1.12~2.62×）。
- 与上一快照变化：`agg_direct` 0.94→0.81（进更快）、`shape_list` 1.21→1.07（回持平）、`shape_index` 1.00→1.12（首次过 10% 线）、`hires_filter` 1.12→1.23；`hires_reduce` 2.63→2.62、`hires_map` 1.77→1.83 属同量级抖动。12 项产物源码与协议记录逐字节相同。

## 本轮已完成

- `python -X utf8 evidence/bench_run.py`（rows=100000、runs=5、BATCH=5）重跑成功，写入 `evidence/perf_protocol.json`。
- 验收报告同步 16 处（§0 口径区间、§5 数据源时间戳、§5.1 表 + §5.2 汇总 + 段落、§5.3 偏慢项表 + order_pipeline 来源标注、§5.5 与上一快照对比段、§7.1 R1/R2/R3/R6、§7.2 清单、§8 ② 行 + 结论段）。
- `tests/test_sync_contract.py` 17 passed / 1 skipped（比值与被测数逐项吻合）；全量 1415 passed / 10 skipped / 0 failed（1425）；`evidence/accept.py` 15 PASS / 0 FAIL / 0 SKIP。

## 下一步

1. `git add -A && git commit`（信息：`docs: 第十三轮重跑性能快照并同步验收报告`）。
2. 输出 📊 汇总（含 `order_pipeline` 无法复跑的说明与待决策项）。

## 坑（踩过的）

- PowerShell 内联 `python -c` 含中文/反引号/`$` 会转义失败 → 写 `_tmp/fix_plan/*.py` 脚本（或 `chr()` 拼字符串）。
- `Get-Content | Measure-Object -Line` 行数不可信 → 用 Python `splitlines()`。
- 文档产物块必须 `dcg.generate()` 实测重算，`# dcg:` 块被 `test_doc_products` 逐字节钉死。
- `generate(validate(schema))` 会二次校验报错（第九轮 B 方案：`validate()` 拒收规范形）。
- 删章节/改标题要同步编号连续性守护（`test_doc_structure`）。
- 沙箱下 pytest 跳过 10 项（正常机 4 项）；`COUNT_SUMMARY` 行是准数。
- 用户建议可能含事实错误，一律先探针再落文档。
- `evidence/bench.py`（探索性 65 项）已不可运行：依赖的 `bench_optimallib` 只剩 `__pycache__` 里的 `.pyc`、源文件不在仓库 → `order_pipeline` 数字只能保留历史实测并标注不可复跑。
- `edit` 工具要求先用 `read` 工具读过文件；file changed 时报错就重读。

## 近期已交付（归档）

- ef517e9 第十二轮：语义口径与跨文档引用修正（7 files）。
- 4b0bdba 第十一轮：optional/type 职责、`$count` 前提、`validate` 契约、示例修正（8 files）。
- d02e4bf 第十轮：去行号/去尺寸 + 尺寸自述反向守护（10 files）。
- 5eb7db0 第九轮收尾：`validate()` 规范形拒收（B 方案）+ 性能归因修正 + gen §6/`$indexOf` 对齐（8 files）。
- 4b8b89c 第八轮 22 项 + AGENTS v2.1 + PROGRESS.md；5b61492 第七轮 16 项；8e5762c 第六轮 18 项；0789c18 第五轮 3 项；a617cc8 第四轮 20 项 + D-BOOL-01；b33a12e 第三轮 22 项；10af3a1 第二轮 22 项 + 架构 §4.8。
