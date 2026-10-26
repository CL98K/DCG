# PROGRESS — DCG 项目 Lead 持久状态

> 维护约定：长期入库维护。每个关键子任务完成、每轮交付提交后覆写本文件；
> 新会话或上下文压缩后**先读本文件恢复进度**，再读用户最新指令。
> 本文件不参与文档守护测试，允许写行号与临时事实。

## 当前任务

**第十四轮：用户 m03272「order_pipeline 把它实现进 bench_run.py 协议」+「判定改为连跑 3 次取中位数比值」**。
- 状态：**编辑与验证已完成，待提交**（基准协议 13 项、`perf_protocol.json` 刷新、验收/README/守护同步、全量回归与门禁全绿）。
- 裁决源：无独立裁决文件（协议变更以 `evidence/perf_protocol.json` 为唯一事实源；用户原话见 m03272）。

## 本轮实测（新的 13 项快照，`generated_at=2026-10-26T23:54:24+0800`）

协议：10 万行 × 每项 5 遍 trimmed_mean；整组连跑 3 次，取 3 个比值的中位数。

| 用例 | DCG µs/行 | 手写 µs/行 | 比值 | 各轮比值 | 判定 |
|---|---|---|---|---|---|
| shape_rows | 0.1680 | 0.3621 | 0.46× | 0.46/0.47/0.46 | 更快 |
| agg_direct | 0.0891 | 0.1109 | 0.80× | 0.80/0.81/0.80 | 更快 |
| shape_dict | 0.1314 | 0.1351 | 0.97× | 0.97/0.95/1.02 | 持平 |
| agg_container | 0.1553 | 0.1553 | 1.00× | 1.02/1.00/0.99 | 持平 |
| shape_index | 0.2479 | 0.2434 | 1.02× | 1.02/0.98/1.05 | 持平 |
| shape_list | 0.1433 | 0.1359 | 1.05× | 0.97/1.06/1.05 | 持平 |
| optwide_missing | 0.8447 | 0.7573 | 1.12× | 0.81/1.15/1.12 | 慢（噪声带内） |
| hires_filter | 0.3780 | 0.3097 | 1.22× | 1.22/1.23/1.13 | 慢 |
| hires_sort | 0.3724 | 0.3056 | 1.22× | 1.22/1.26/1.20 | 慢 |
| optwide_hit | 0.2014 | 0.1615 | 1.25× | 1.34/1.23/1.25 | 慢 |
| hires_map | 0.3818 | 0.2182 | 1.75× | 1.78/1.74/1.75 | 慢 |
| hires_reduce | 0.5250 | 0.2198 | 2.39× | 2.49/2.37/2.39 | 慢 |
| order_pipeline | 6.6721 | 2.4712 | 2.70× | 2.79/2.70/2.68 | 慢（第 13 项完整样例） |

- 判定汇总：12 项主基准 = **2 项更快 + 4 项持平 = 6 项不落后；6 项偏慢**；另第 13 项完整样例 2.70×。
- 与上一快照变化：`shape_index` 1.12→1.02（回持平）、`hires_sort` 1.28→1.22、`hires_map` 1.83→1.75、`hires_reduce` 2.62→2.39、`shape_rows` 0.49→0.46；`optwide_missing` 1.03→1.12（过线，但各轮噪声大）；判定分布仍 2/4/6。
- `order_pipeline` 的 DCG 产物与手写逐值相等（200 行探针 EQ=True），比值来自行容器设计边界（架构 §7.5）。

## 本轮已完成

- `evidence/bench_protocol.py`：新增 `REPEATS=3`，`METHOD_NOTE` 写入「整组连跑 3 次取比值中位数」。
- `evidence/bench_fast.py`：新增 `SEED=20261024` / `make_orders()` / `order_pipeline_hand()`（与 `bench.py` 等价）；`optwide_missing` docstring 改「逐行探测上限、噪声带」。
- `evidence/bench_run.py`：新增第 13 项 `order_pipeline`（schema 从 `evidence/bench.py` 搬入）；`measure_case()` 改为 `_run_pass()` × `REPEATS` 取比值中位数；`perf_protocol.json` 增加 `repeats`/`ratios` 字段。
- `evidence/perf_protocol.json`：13 项新快照（`generated_at=2026-10-26T23:54:24+0800`）。
- `验收报告.md`：§0 ②/口径、§5 数据源+测量协议、§5.1 12 项表与 §5.2 汇总、§5.3 表（6 项 + `order_pipeline` 行）、§5.5 对比段、§6.2「第十四轮增补」、§7.1 R1/R2/R3/R6、§7.2 清单、§8 ② 与结论段。
- `README.md`：协议描述改为「每项 5 遍 trimmed mean；整组连跑 3 次取比值中位数」。
- `tests/test_sync_contract.py`：12 项参数化保持不变（收集数仍 1425）；名字集合按「协议 − {order_pipeline}」比对，并在同一用例里核对 `order_pipeline` 的 §5.3 比值。

## 下一步

1. `git add -A && git commit`（信息：`docs: 第十四轮 order_pipeline 纳入基准协议 + 判定改中位数降噪`）。
2. 输出 📊 汇总（含噪声风险与协议语义变化说明）。

## 坑（踩过的）

- PowerShell 内联 `python -c` 含中文/反引号/`$` 会转义失败 → 写 `_tmp/fix_plan/*.py` 脚本（或 `chr()` 拼字符串）。
- `Get-Content | Measure-Object -Line` 行数不可信 → 用 Python `splitlines()`。
- 文档产物块必须 `dcg.generate()` 实测重算，`# dcg:` 块被 `test_doc_products` 逐字节钉死。
- `generate(validate(schema))` 会二次校验报错（第九轮 B 方案：`validate()` 拒收规范形）。
- 删章节/改标题要同步编号连续性守护（`test_doc_structure`）。
- 沙箱下 pytest 跳过 10 项（正常机 4 项）；`COUNT_SUMMARY` 行是准数。
- 用户建议可能含事实错误，一律先探针再落文档。
- 给 `pytest.mark.parametrize` 加一个 case 会改变 collected 数（1425 是见诸多份文档的契约数字）→ 新校验尽量塞进已有用例。
- `evidence/bench.py`（探索性 65 项）已不可运行：依赖的 `bench_optimallib` 只剩 `__pycache__` 里的 `.pyc`、源文件不在仓库 → 但 `make_orders`/`order_pipeline_hand`/schema 可直接搬运复用。
- 10 万行基准单轮 40~70 ms，跨调度会抖 20%+ → 现在靠「3 次中位数」压噪，单项比值仍有 ~10% 边界波动（如 `optwide_missing` 各轮 0.81/1.15/1.12）。
- `edit` 工具要求先用 `read` 工具读过文件；file changed 时报错就重读。

## 近期已交付（归档）

- 84c8964 第十三轮：重跑性能快照并同步验收报告（3 files）。
- ef517e9 第十二轮：语义口径与跨文档引用修正（7 files）。
- 4b0bdba 第十一轮：optional/type 职责、`$count` 前提、`validate` 契约、示例修正（8 files）。
- d02e4bf 第十轮：去行号/去尺寸 + 尺寸自述反向守护（10 files）。
- 5eb7db0 第九轮收尾：`validate()` 规范形拒收（B 方案）+ 性能归因修正 + gen §6/`$indexOf` 对齐（8 files）。
- 4b8b89c 第八轮 22 项 + AGENTS v2.1 + PROGRESS.md；5b61492 第七轮 16 项；8e5762c 第六轮 18 项；0789c18 第五轮 3 项；a617cc8 第四轮 20 项 + D-BOOL-01；b33a12e 第三轮 22 项；10af3a1 第二轮 22 项 + 架构 §4.8。
