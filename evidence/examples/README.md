# `evidence/examples/` 索引（task-5）

本目录是 `docs/examples_v1_to_v2.md`、`bench/bench_examples.md`、`tests/examples/` 的**原始证据**。
每条结论都应能在这里找到对应的原始输出；本文件说明"哪个文件是什么、哪条命令能重新生成它"。

Python 3.13.3（`MSC v.1943 64 bit`）；v1 引擎统一经 `bench/_v1bridge.py` 的 `load_v1_engine()`
加载（**唯一入口**，未复制 v1 源码）。

## 冻结的 v1 期望（写报告时引用的就是这些）

| 文件 | 内容 | 重新生成 |
|---|---|---|
| `v1_examples_frozen.json` | 六例的机器可读冻结记录：`describe`/`datas`/`stdout`/`pprint` 实参/异常原文/`modes_set_by_example`/`variants`（2 因子实验）/三种生成码 | `python -X utf8 evidence/examples/probe_v1_examples.py` |
| `v1_examples_raw.txt` | 同一批数据的**人读**全文记录（含逐例 stdout） | 同上 |
| `v1_examples_source.txt` | 六例**反注释后的源码**，带真实 `format.py` 行号 | 同上 |
| `stdout/ex{1..6}_stdout.txt` | 每例单进程的 stdout 原文 | 同上 |
| `v1_defects.json` | **16 个最小复现**：D-A（`filter` 键）/D-B 三组合/D-C（`chain`）/六例 verbatim，每例附生成码与异常原文 | `python -X utf8 evidence/examples/probe_v1_defects.py` |

## v2 侧探针

| 文件 | 内容 |
|---|---|
| `probe_v2_status.py` | `import dcg` 的公开面、`generate(CAP)` 能否编译/运行、`compile_expr` 的代表性输出与 `Unsupported` 原文 |
| `probe_v2_gaps.py` | v2 对 v1 各种写法的实测反应（`show:False`、无 `_field` 层、裸名、深路径、f-string 伪路径、逗号多值、三种 `shape` 的产物形态） |
| `probe_helper_cost.py` | 每个 runtime helper 相对内联的开销（ns/调用，min of 3 × 2,000,000）—— `bench_examples.md` §4.1 的来源 |
| `probe_determinism.py` | 3 进程 × 每 schema 生成 5 次，比对源码是否逐字相同（确定性证据） |
| `_dump_v2_examples.py` | 打印 ex1/ex2_docs/ex3/ex4 的 v2 生成码与字符数（`bench_examples.md` §4.3 的复测来源） |
| `_inspect_v1_codegen.py` | `summary` / `<n> [substr]` 两模式，查看 v1 生成码 |

> 曾经的一次性脚本 `_debug_golden.py`（抓 `harness.Golden.result` 笔误）与 `_mini_expr.py`
> （不可复现的瞬时生成码观察）**已按 Lead 裁定删除**；它们的结论分别记在
> `tests/examples/v1_parity/harness.py:103` 的 docstring（笔误与修法）与
> `docs/examples_v1_to_v2.md` §9（瞬时生成码观察 + 3 进程 × 5 次不可复现的证据），
> 删脚本不影响任何结论的可追溯性。

## 基准原始输出

| 文件 | 内容 |
|---|---|
| `bench_examples_raw.txt` | **默认（不带 tag）运行的人读全文**（含时间戳、生成器 sha1、当时的进程表）。⚠ 当前内容 = **2026-10-17T00:51:03 那次**：更早的 00:48:12 那次写过同一个文件名、**已被覆盖**（数字只留在 `bench_examples.md` §3.2）—— 与 Run B 被 Run C 覆盖是同一个缺陷，见 `bench_examples.md` §6.1b |
| `bench_examples.json` | 同一批数据的机器可读版本（`errors` 为空即无失败；**不要只看退出码**，见 `bench_examples.md` §6） |
| `bench_examples_postinline_raw.txt` / `.json` | **内联完成后**的第三次 provisional 运行（`--tag postinline`，2026-10-17T01:03:21，加权 v1/v2 = 1.011）。⚠ 这一轮的 `1.011` **已被静默窗口的三次 `--final` 推翻**（同向 0.783 / 0.746 / 0.803，v2 更慢），引用前先看 `bench_examples.md` §3.6 / §7 |
| `bench_examples_postinline_console.txt` | 上述运行的 stdout 原文（PowerShell 重定向，UTF-16） |
| `bench_examples_final_raw.txt` / `bench_examples_final.json` | **历史产物：窗口 W2 的第三次运行（Run C，2026-10-17T01:13:28，锚定 revision `A40457511277`）**，用的是**墙钟口径**。⚠ 该文件名**不含 tag**（旧版 `--final` 的固定名），W3 起 `--final` **必须**带 `--tag`、且同 tag 拒绝覆盖，所以这组文件**不会再被写**，保留作证据 |
| `bench_examples_final_W1_untagged_raw.txt` / `.json` | 窗口 W1 那次 `--final` 的**留证副本**（Run A，2026-10-17T01:10:49 启动）。它自算的 revision 是 `a40457511277ffda701eceb515ba37e4ba8cfda2`，而当时下达的锚点是 `80DCA0418503`（`codegen.py` 在锚点采样后 0.58 秒被改写）→ **按规则停手上报，不作为终值**；正文因此记 `label_actual=FINAL`、`measurement revision = 启动时自算` |
| `bench_examples_final_<tag>_raw.txt` / `.json` | **W3 起的终值产物**（CPU 口径，CONTRACT §7.1）：`--final --tag <tag> --n 200000`。除样例表外含 `report["hand"]`＝§5 CPU 口径块（A/A 噪声底、per-block `x/hand`、`verdict_v2{v,reason,margin,noise_floor,pass}`、三态 `sec5_label`）与 `revision_stable_during_run`。**新 tag 每次新文件**，绝不覆盖旧证据 |
| `bench_examples_final_W3-2026-10-17T01-41_raw.txt` / `.json` | ⭐ **终值**（2026-10-17T01:41:10，N=200000，`exit 0`，`revision_stable_during_run=True`，revision `A40457511277`）。§5 CPU 口径：`hand 0.762` / `v2 0.771` µs/行，`x/hand` = **1.000 / 1.000 / 1.013**（worst 1.013），A/A 噪声底 0.0127，margin 0.0372 → **`RESOLVED` / `MET (all 3 blocks <= 1.05)`**。逐例墙钟 v1÷v2：EX1 1.069 / EX2 0.657 / EX3 0.891 / EX4 0.835；加权 `0.795` |
| `bench_examples_final_W3-2026-10-17T01-36_raw.txt` / `.json` | 同一 revision 早 5 分钟的一次 CPU 口径 `--final`（`x/hand` worst 1.026，margin 0.0241 < 噪声底 0.0270 → `WITHIN_NOISE`）。⚠ **它的正文最后一行按当时的直译印 `NOT MET`，请读作 `WITHIN_NOISE`**（三个 block 其实全部 ≤1.05）—— 这正是后来加三态 `sec5_label` 的原因；数字本身有效 |

> 三次墙钟 `--final` 运行（Run A / B / C）的逐例数字、进程表与 sha1 见 `bench/bench_examples.md` §3.6 与 §6.1。
> **Run B（`01:12:47`）测量期间进程表含非本任务的 `python.exe`（pid 1412 / 12812）→ 该轮被并发污染，不算终值；
> Run C（`01:13:28`）全程只有本进程的额外 pid，可证干净。**
> **W3 起改用 CPU 口径（`time.process_time()`、k=8、interleaved、reps=7、3 block）→ 抢占免疫，
> §5 的 5% 判定不再靠墙钟**（见 `bench_examples.md` §6.2 / §6.2.1）。

## 纪律

- 六例的 v1 期望**全部来自真跑**，没有任何一条是手抄或推测。
- 无法对等的地方不写"看起来能跑"的伪 schema，也不用 `pytest.skip` 掩盖；
  `tests/examples/` = 13 个对等测试 + 4 个可追溯性测试，**全部真跑**（0 skipped、0 xfail）。
- 性能数字在静默窗口前一律是 `PROVISIONAL`，绝对值不可引用（v1 引擎代码十几分钟没改，
  单例却从 0.520 漂到 0.780 µs/行；波动率最高 149.5%）。主判定只看同一次运行内的 v1/v2 比值。
- **墙钟口径的 5% 阈值判定无判定力**：同一 revision 上三次 §5 `ratio_v2_over_ref` =
  1.117 / 0.901 / 0.937，两次"进程表干净"的测量（Run A 与 Run C）给出**相反判定**；Lead 的
  A/A 噪声底标定（同一个逐字相同的函数交替 15 轮）显示"比值"本身可在 0.97–1.168 之间漂。
  → 墙钟的 §5 判定记为 **`INCONCLUSIVE`，不得据此宣称达标或不达标**（`bench_examples.md` §6.2.1）。
- **§5 的正式判定改用 CPU 口径**（CONTRACT §7.1：`time.process_time()`、k=8、interleaved、reps=7、
  3 block、A/A 噪声底；计时器**只能** import `bench/bench_row.py::measure_interleaved_cpu`，不得另写一份）。
  在该口径下 Lead 测出 `v2/hand ≈ 0.96–0.99` → `RESOLVED PASS`；我这边终值 `W3-…T01-41` 测出
  `1.000 / 1.000 / 1.013`（worst 1.013）→ 也是 `RESOLVED` / `MET`；但同 revision 早 5 分钟的
  `T01-36` 落在 `WITHIN_NOISE`（worst 1.026）→ **余量只有 1–4%，与噪声底同量级**，
  报结论时必须同时给 `margin` 与 `noise_floor`，**`WITHIN_NOISE` 不等于"没达标"**（三态 `sec5_label`）。
  **绝对值跨会话可漂 56%**（我这边同 revision 两次 `hand` = 1.113 → 0.762 µs/行，漂 46%），只有**比值**可引用。
- **可以引用的是方向与归因**：样例负载上三次同向（v1÷v2 = 0.783 / 0.746 / 0.803，v2 慢
  1.25–1.34×，主因 EX2），helper 开销 ~1.4 µs/行超出噪声 ~0.02 µs/行 400 倍 → 归因结论成立；
  绝对 µs/行不成立。

## 再生成命令

```powershell
# 冻结的 v1 期望（每例独立 subprocess；改 format.py 前请先重跑）
python -X utf8 evidence/examples/probe_v1_examples.py
# v1 三处缺陷的最小复现（D-A/D-B/D-C + D-D 的 2 因子实验）
python -X utf8 evidence/examples/probe_v1_defects.py
# §5 的 helper 开销归因
python -X utf8 evidence/examples/probe_helper_cost.py
# v2 生成码 dump（EX1/EX2_DOCS/EX3/EX4）
python -X utf8 evidence/examples/_dump_v2_examples.py

# 样例基准：provisional（墙钟，只作方向）——tag 自定，同 tag 拒绝覆盖
python -X utf8 bench/bench_examples.py --n 200000 --tag <tag>
# 终值（墙钟样例表 + CPU 口径 §5 块）；--final 必须带 tag
python -X utf8 bench/bench_examples.py --final --tag <tag> --n 200000
```

`pytest tests/examples -q` 是这两份文档的守门人（13 对等测试 + 4 可追溯性测试，0 skip / 0 xfail）。
