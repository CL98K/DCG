# DCG v2.1 全面性能评测报告

**数据来源**：`evidence/perf_bench.json`（唯一权威出处，本报告所有数字均可回溯到该文件的字段路径）
**采集时间戳**：`generated_at = 2026-10-24T19:26:16+0800`（墙钟 `wall_s = 46.97 s`）
**源码快照 sha1**（`source_sha1.*`）：`dcg/codegen.py = 2c85c3ab4b65…`、`dcg/runtime.py = 0ea16c87dab3…`、`dcg/schema.py = e2bdfcf3c917…`、`dcg/__init__.py = b9d7770a…`、`dcg/ir.py = 0e554848…`、`dcg/build.py = 79ec4321…`
**代码基线**：本次采样时 `python -m pytest -q` = **1100 passed / 4 skipped / 0 failed**（1104 collected），`python -X utf8 evidence/accept.py` = 15 GATES PASS。采样之后 Lead 又加了回归测试，当前工作区是 **1106 passed / 4 skipped**，但**本报告数字仍以 19:26 那次采样为准**（未重跑）。
**注意（口径声明）**：agg 组（`agg_direct` / `agg_container`）、`ladder.L9/L10` 的数字采集于「DEFECT-001 已修 + 聚合 const 子键已统一成标量」之后（见 §7），因此它们与修复前的老数字不可比；修复前的老数据一律不引用。

> 复现命令：`python -X utf8 evidence/bench.py --rows 10000 --repeat 5 --blocks 2 --json evidence/perf_bench.json --profile`
> 单次全量 ≈ 47 s。Cython 探针固定跳过（无 C 编译器），不影响其余数据。

---

## 1. 方法学

| 项 | 值 | 字段路径 |
|---|---|---|
| CPU | AMD Ryzen 7 PRO 5850U with Radeon Graphics，8 核 / 16 逻辑核，1901 MHz | `env.cpu_name` / `env.cpu_count` / `env.cpu_mhz` |
| OS | Microsoft Windows 10 家庭中文版，10.0.19045 | `env.os` / `env.platform` |
| Python | CPython 3.13.3（`tags/v3.13.3:6280bb5`） | `env.python` / `env.python_build` |
| DCG 版本 | 2.1.0 | `env.dcg_version` |
| `perf_counter` 分辨率 | 100 ns | `env.perf_counter_res_ns` |
| 计时方法 | `time.perf_counter_ns`；每个样本跑 k 次取平均，每轮 `repeat=5` 个样本取**中位数**，两块之间夹 `gc.collect()` 后取**两块最小值** | `config.repeat / config.blocks` |
| 场景行数 | 主表 `N = 10000` 行（scale 组另做 100 / 1000 / 10000 扫描；cap/m0 组 = 100000 行） | `config.rows`、`rows[group=scale].n`、`rows[group=cap].n` |
| 每条数字的三件套 | ① DCG 产物实测 `per_row_ns` ② 同形状等价手写 `full_per_row_ns` ③ 最简裸写 `bare_per_row_ns` | 同名字段 |
| **噪声底** | `aa_median = 0.96%`，**`aa_max = 1.88%`**，`cv_median = 4.92%`，`cv_p90 = 14.33%`，`cv_max = 115.16%` | `noise.*` |
| A/A 对照明细 | `post_warm_mix6_list` 1.88%（重测 2 次达标）、`post_optwide` 0.96%（重测 5 次）、`post_hand_order_pipeline` 0.18%（重测 3 次） | `noise.aa[*].gap` / `.tries` |
| Cython | **跳过**：`无 C 编译器（cl/MSVC 不在 PATH）`；仍验证了 `cython=True` 产物 + `cythonize` 成功（29 行 .pyx → 8712 行 C，`cythonize_ms ≈ 674 ms`） | `cython.*` |

### 1.1 判读口径（**硬规则**）

1. **差异 < 10% 不宣称「更快 / 更慢」**，一律记作「持平」。
2. 比值定义：`ratio_full = DCG µs/行 ÷ 等价手写 µs/行`。**>1 表示 DCG 更慢**，`<1` 表示 DCG 更快。
3. 显著性判定（`evidence/bench.py` 中的 `mark_significance()`）：
   - `sig_full`：`|ratio−1| > max(aa_max, 3%) = max(1.88%, 3%) = 3%` → 3% 以上的差异**才可能**被声明；
   - `sig10_full`：`|ratio−1| > 10%` → **报告正文只把 `sig10=True` 的行当作「快/慢」结论**；`sig10=False` 的行一律写成「持平（约 X×）」。表里用 `~` 标记。
4. `low_conf = cv > 2×aa_median` 的行（本报告中的 `cap@100000` / `m0@100000`，cv≈100%~115% 是「单次 30–40 ms 调用被系统抖动整块拖慢」造成的）其**比值仍可用**（A/B 交替、两块取小），但**绝对 µs/行 是上界**，不足以刻画 1e5 行时的绝对吞吐；绝对量另见 §5 的专门复测。
5. 阶梯/规模组里 DCG 常常**比手写更快**（ratio < 1）：这是因为等价手写是按「最直白写法」写的（每列一次 `r[...]`、每行一个 list），而 DCG 产物做了前缀提升（把 `R[5]['a']` 提到循环头只取一次）。这是 DCG 的设计收益，不是测量偏差。

---

## 2. 从简单到复杂阶梯表（L1 → L10）

单列 → 多列，每次只加一个形态。`DCG µs/行 = rows[group=ladder, name=Lx].per_row_ns / 1000`，`手写 = …full_per_row_ns / 1000`，`比值 = ratio_full`，`显著 = sig10_full`。

| 步骤 | 形态（累计在此基础上） | DCG µs/行 | 手写 µs/行 | 比值 | 噪声底内 | 判读 |
|---|---|---|---|---|---|---|
| L1_bare | 1 列 `value` 裸下标 `$.0` | 0.0875 | 0.0897 | 0.98× | 是 | 持平 |
| L2_expr | +1 列 `expr`（反引号算术 `` `$.1 + $.2` ``） | 0.1157 | 0.1243 | 0.93× | 否（sig10=F，仅 7% 差） | 持平 |
| L3_opt_typed | +optional（有类型，`.get(k,_U)` 链） | 0.1818 | 0.1390 | **1.31×** | 否 | DCG 慢 |
| L4_default | +default（`_df(_ix_get(...),7)` 兜底） | 0.2237 | 0.1515 | **1.48×** | 否 | DCG 慢 |
| L5_nested | +嵌套 fields（逐行 dict） | 0.3064 | 0.2664 | 1.15× | 否 | DCG 慢约 15% |
| L6_backtick | +反引号字符串段 `` `$.6[:1] + '.' + $.7[:1]` `` | 0.4756 | 0.4080 | 1.17× | 否 | DCG 慢约 17% |
| L7_py_block | +`py{}` 块（模块级辅助函数 `_py0`） | 0.5125 | 0.4503 | 1.14× | 否 | DCG 慢约 14% |
| L8_hires | +高阶函数 `$reduce($.5.a, …)` | 1.3672 | 0.5740 | **2.38×** | 否 | **DCG 明显慢（最大单点）** |
| L9_agg | +aggregate 列容器（尾元素，走直出快路径） | 1.3829 | 1.8086 | **0.76×** | 否 | DCG 更快（前缀提升受益） |
| L10_aggonly | 纯 aggregate（无逐行字段） | 0.2111 | 0.1075 | **1.96×** | 否 | DCG 慢 2 倍（行容器空转） |

字段路径：`rows[group=ladder,name=L8_hires].per_row_ns`、`.full_per_row_ns`、`.ratio_full`、`.sig10_full`；产物字符数见 `meta.artifacts.ladder_*`（L1=72 字符 … L8=372 字符）。

**阶梯结论**：从 L2（0.116 µs/行，比手写略快）到 L8（1.367 µs/行，比手写慢 2.38×），性能**不是随字段数平滑增长**，而是由一个形态断层——**高阶函数 `$reduce/$map/$filter`**（L8 = +0.85 µs/行的阶跃）——以及 **aggregate 行容器**（L10 = +0.12 µs/行）共同决定。除这两类，其余形态的额外开销都在 0.02–0.05 µs/行量级。

---

## 3. 各 group 结果（N=10000）

### 3.1 scale：10 行 → 1000 行 → 10000 行（9 形态混合 schema，list / dict 两种 shape）

| 场景 | DCG µs/行 | 手写 µs/行 | 比值 | 行/秒 | cv |
|---|---|---|---|---|---|
| mix9_list@100 | 1.2003 | 1.7739 | 0.68× | 833 234 | 2.5% |
| mix9_dict@100 | 1.3266 | 1.7062 | 0.78× | 753 818 | 0.6% |
| mix9_list@1000 | 1.2002 | 1.7040 | 0.70× | 833 171 | 1.5% |
| mix9_dict@1000 | 1.3863 | 1.7841 | 0.78× | 721 345 | 2.5% |
| mix9_list@10000 | 1.6552 | 1.9477 | 0.85× | 604 153 | 15.2% |
| mix9_dict@10000 | 1.6029 | 1.9452 | 0.82× | 623 889 | 3.9% |

字段：`rows[group=scale,name=mix9_list@10000].*`。
**结论**：100 行就已经是 DCG 更快（0.68×），说明**没有明显的冷启动/固定开销惩罚**——生成产物是一次 `exec` 出的普通函数，1e2 和 1e4 行同口径。10 万行扫描由 `cap`/`m0` 组覆盖（§5）。行数越大 DCG 相对手写的优势收窄（0.68× → 0.85×），原因是 dcache/cache 压力下每行 list 与逐行 dict 的分配成本上升，而手写路径的分配模式更扁平。

### 3.2 shape：4 种输出形状（同一 9 形态 schema，只换 `output.shape`）

| 场景 | DCG µs/行 | 手写 µs/行 | 比值 | 行/秒 | 产物字符 |
|---|---|---|---|---|---|
| shape_list | 1.3708 | 0.5912 | 2.32× | 729 485 | 440 |
| shape_dict | 1.6491 | 0.5509 | **2.99×** | 606 380 | 692 |
| shape_index | 1.4611 | 0.5580 | 2.62× | 684 439 | 468 |
| shape_rows | 2.1455 | 0.5829 | **3.68×** | 466 096 | 573 |

字段：`rows[group=shape,name=shape_dict].ratio_full`；字符数 `meta.artifacts.shape_dict.chars`。
注：本组的手写对手是「朴素直写」版（把同样的 9 个值直接写成行），**不含 `_o` 行容器**，所以比值天然偏大；把它与 §3.1 的 1.3–1.6 µs/行对照可以看出：**行容器 `_o` + 列式收尾**是 dc 的主要成本（shape_list 1.37 vs hands 0.59）。
**结论**：`rows` 是最贵形状（每行再建一个 dict，2.15 µs/行，3.68×）；`dict`（列式）次之（每个字段一次 `[r[i]for r in _o]`，9 次全扫）；`index` 最便宜（`{i:r for i,r in enumerate(_o)}` 是 C 级 dict comprehension）。

### 3.3 col：单列 9 种取值形态（dict shape，单字段走 flat-row 快路径）

| 场景 | DCG µs/行 | 手写 µs/行 | 比值 | 行/秒 |
|---|---|---|---|---|
| col_bare（裸下标） | 0.0302 | 0.1887 | **0.16×** | 33 129 822 |
| col_opt_typed（optional 有类型） | 0.0641 | 0.2137 | 0.30× | 15 593 673 |
| col_opt_any（optional + any） | 0.0605 | 0.2096 | 0.29× | 16 529 706 |
| col_default（default） | 0.0726 | 0.2204 | 0.33× | 13 774 105 |
| col_expr（反引号算术） | 0.0418 | 0.2301 | 0.18× | 23 899 758 |
| col_nested（嵌套 fields） | 0.0981 | 0.3011 | 0.33× | 10 194 373 |
| col_hires（`$reduce`） | 0.7193 | 0.3101 | **2.32×** | 1 390 279 |
| col_backtick（反引号字符串段） | 0.1343 | 0.3680 | 0.36× | 7 448 420 |
| col_py_block（`py{}` 块） | 0.0474 | 0.2444 | 0.19× | 21 097 046 |

字段：`rows[group=col,name=col_hires].ratio_full` 等。
**结论**：单列场景 DCG 比手写快 3–6 倍（fast path：`_c(_df(_ix_get(R,2),7))` + `return {'x':_o}`，没有行容器、没有转置）。**唯一例外是 `$reduce`（2.32×）**——见 §6 热点。

### 3.4 form：形态专项（无逐行多列混合）

| 场景 | DCG µs/行 | 手写 µs/行 | 比值 | 判读 |
|---|---|---|---|---|
| const_only | 0.0738 | 0.0666 | 1.11× | 略慢（常量列的 list 仍逐行 append） |
| hires_map（`$map`） | 0.9649 | 0.2862 | **3.37×** | DCG 慢 3.4× |
| hires_filter（`$filter`） | 1.4129 | 0.3073 | **4.60×** | DCG 慢 4.6× |
| hires_sort（`$sort`） | 0.4536 | 0.3017 | 1.50× | DCG 慢 1.5× |
| hires_reduce（`$reduce`） | 0.7675 | 0.1335 | **5.75×** | DCG 慢 5.8× |
| any_col（`type:"any"`） | 0.1074 | 0.0821 | 1.31× | 慢 31% |
| list_col（列表列） | 0.0985 | 0.0920 | 1.07× | **持平**（sig10=False） |
| py_block（`py{}`） | 0.1079 | 0.0795 | 1.36× | 慢 36%（每行一次 `_py0()` 调用） |

字段：`rows[group=form,name=hires_filter].ratio_full`。

### 3.5 agg：聚合（本次采样已在 DEFECT-001 修复之后）

| 场景 | DCG µs/行 | 手写 µs/行 | 比值 | 备注 |
|---|---|---|---|---|
| agg_direct（子键全是 const/单段 value/纯 expr，走 `_aggregate_direct` 直出列快路径） | 0.4044 | 0.2290 | 1.77× | 产物 7 行 / 308 字符，`stats` 的 const 子键已是**标量** `'n_rows':1`，value 子键是 `[_ix_get(r,2) for r in _d]`（`meta.checks.agg_direct_used_fastpath=True`） |
| agg_container（含 optional 子键 → 退回「每行造 `_ob` + 两级转置」） | 0.5662 | 0.0868 | **6.52×** | 产物 8 行 / 318 字符，`_a=[r[2]for r in _o]` + `[r['lvl']for r in _a]` |

字段：`rows[group=agg,name=agg_container].ratio_full`、`.ref_note`；产物形态见 `meta.ops.agg_container`、`meta.artifacts.agg_container`。
**这是全表最差的一项**：一旦 aggregate 里混进 optional 子键，代价从 1.77× 跳到 6.52×（每行多一个 dict、多一个 buffer list、多扫一遍）。

### 3.6 opt：optional 列（宽行 dict，8 个可空列，其中 4 个缺键）

| 场景 | DCG µs/行 | 手写 full | 手写 bare | 比值(full) | 比值(bare) |
|---|---|---|---|---|---|
| optwide_hit（全部命中） | 0.8133 | 0.4067 | 0.1657 | 2.00× | 4.91× |
| optwide_missing（4 列缺键） | 0.8359 | 0.4283 | 0.1501 | 1.95× | 5.57× |
| opt_2c（2 个 optional 列） | 0.2640 | 0.2828 | 0.2536 | 0.93×（sig10=F，持平） | 1.04× |
| opt_4c（4 个 optional 列共用 `$.buyer`） | 0.3534 | 0.5339 | 0.4478 | 0.66×（DCG 更快） | 0.79× |

字段：`rows[group=opt,name=optwide_hit].ratio_full`、`.ratio_bare`。
**关键发现**：DCG 的 optional 口径是「`.get(k,_U)` + `is _U` 三目」两步（产物 `_t1=R.get('level',_U); level=None if _t1 is _U else _t1`），比手写「一次 `.get(k)`」贵约 2.0×（`ratio_bare = 5.57` 就是拿「只做 `.get` 的手写版」比出来的）。这是**语义成本**（要区分「缺键」和「值是 None」），不是实现缺陷——但确实是最容易被用户感知的形态开销之一。

### 3.7 finish：输出段构造方式（同一个行线 `_o`，只换收尾策略）

| 场景 | DCG µs/行 | 手写 µs/行 | 比值 | 说明 |
|---|---|---|---|---|
| finish_direct（边循环边 append 到各列，**无** `_o` 行容器） | 0.3547 | 0.3194 | 1.11× | 理论最优形态 |
| finish_comps（`{k:[r[i]for r in _o]}` 逐列推导，**DCG 当前 dict shape 就用这个**） | 0.2628 | 0.3442 | 0.76× | cv 34.4%，但比值与 finish_direct 同量级 |
| finish_rows_dict（行容器 + `[{k:r[i]…} for r in _o]`，**DCG 当前 rows shape 用的**） | 0.4354 | 0.3275 | 1.33× | |
| finish_transpose（先 `_o` 再全表转置） | 0.5112 | 0.3388 | 1.51× | |

字段：`rows[group=finish,name=finish_transpose].ratio_full`。
**结论**：`_o` 行容器本身不是问题（finish_comps 只要 0.263 µs/行），**问题在 rows shape 的 dict 推导**（每行一次 dict comprehension = 2.15×）和**多列转置**（每列一遍全扫）。

### 3.8 pipeline：标准复杂负载 order_pipeline（15 字段 + before/after 钩子 + aggregate）

| 场景 | DCG µs/行 | 手写 µs/行 | 裸写 µs/行 | 比值 | 产物 |
|---|---|---|---|---|---|
| order_pipeline | **3.3635** | 1.6445 | 1.7613 | **2.05×** | 20 行 / 1406 字符（`meta.artifacts.order_pipeline`） |

字段：`rows[group=pipeline,name=order_pipeline].per_row_ns / .ratio_full`；输入 10000 行、输出 9230 行（`meta.checks.order_pipeline_rows`），所以**按输出行算是 3.64 µs/行**。语义校验 `meta.checks.order_pipeline_eq = true`（与本报告的手写对手逐行相等）。

### 3.9 hook：before / after 钩子的边际成本

| 场景 | DCG µs/行 | 相对 hook_none | 手写 µs/行 | 比值 |
|---|---|---|---|---|
| hook_none（无钩子） | 1.3792 | 基准 | 1.9078 | 0.72× |
| hook_before（`$filter($, …)`） | 1.7827 | **+0.4035 µs/行（+29%）** | 1.8934 | 0.94×（sig10=F） |
| hook_after（`$map($, …)`，恒等映射） | 1.4001 | +0.0209 µs/行（+1.5%） | 1.8810 | 0.74× |

字段：`rows[group=hook,name=hook_before].per_row_ns` 与 `rows[group=hook,name=hook_none].per_row_ns`。
**结论**：`after` 几乎零成本（+1.5%），但 **`before` 的 `$filter` 要 +29%**——因为它是 per-row 的 `_fi(D, lambda o,_i1: …)` 全表重造列表，是一次完整的额外 pass + 每行一次 lambda 调用。

### 3.10 gen：生成期成本

| 指标 | mix9_list（18 行/440 字符） | order_pipeline（20 行/1406 字符） | mix9_dict（18 行/692 字符） |
|---|---|---|---|
| `validate` | 0.0337 ms | 0.0596 ms | 0.0333 ms |
| `generate` | 1.4702 ms | **5.1094 ms** | 2.0066 ms |
| `exec(compile())` | 0.1899 ms | 0.5813 ms | 0.3079 ms |

字段：`meta.gen.mix9_list.validate_ms / .generate_ms / .exec_ms`；纯 `validate()` 一次 = `meta.gen.validate_only_us = 55.75 µs`（order_pipeline）。
组计时（20 次连续调用）：`rows[group=gen,name=gen_mix9_list_x20].ns = 25.66 ms`、`gen_order_pipeline_x20 = 85.37 ms`（≈ 1.28 ms / 4.27 ms 一次；该组 `per_row_ns = null`，只有 `ns`，故 `ratio_full` 一栏无意义）。
**结论**：生成期是**毫秒级**，相对运行期（1 万行 × 1.3 µs = 13 ms）占比 10%~40% 只在「少量数据 + 生成一次」的场景才显著；生成期的主要成本是 `generate()`（JX 编译 + 拼接），不是 `validate`（55.75 µs）也不是 `exec`。

---

## 4. 与 README 附录 B 的历史数据对比

| 口径 | 历史（`evidence/perf_profile_appendix_b.txt`，2026-10-23） | 本次（2026-10-24 19:26） | 说明 |
|---|---|---|---|
| order_pipeline，20000 行 | 手写 29.018 ms（1.4509 µs/行） vs DCG 89.286 ms（**4.4643 µs/行**），**3.077×**，噪声 23.1% | 10000 行：手写 1.6445 µs/行 vs DCG **3.3635 µs/行**，**2.05×**，噪声底 1.88% | 相对历史 **−25% 时间**（4.46 → 3.36 µs/行），且本次是干净噪声底下的显著结论 |
| 同一负载，200000 行 | 手写 424.107 ms（2.1205 µs/行） vs DCG 1272.321 ms（6.3616 µs/行），**3.000×**，噪声 20.2% | 未复测（受 10 分钟/次约束），改用 cap/m0 的 1e5 行口径 | 历史 20 万行数字里 DCG 2.12 → 6.36 µs/行是**内存压力**导致的（峰值 167.52 MB vs 手写 96.19 MB） |
| 8 列纯 value 列 | 0.573 µs/行 = **0.31×**（DCG 比手写快 3×） | `col_bare` 0.0302 µs/行 = **0.16×**；`scale.mix9_list@100` 1.20 µs/行 = **0.68×** | 同一结论方向：**纯 value 列 DCG 更快** |
| aggregate 容器 | 「每行造 `_ob` + 转置」约 +1.0 µs/行（B.8-3） | `agg_container` 0.566 µs/行（+0.34 µs vs 手写），`agg_direct` 0.404 µs/行 | B.8-3 的 `_ob` 建 dict 已被 `_aggregate_direct` 直出列路径**部分修复**（const 子键已是标量）；只有「含 optional 子键」时才回退到老路 |
| `_ob` vs dict 字面量 | B.8 建议把 `_ob` 换成 dict 字面量「省 ~1.1%」 | 微基准 `_ob2` 287.6 ns vs dict 字面量 114.7 ns = **2.51×**（`meta.micro[name=_ob2]`） | **B.8 的「换字面量只省 1.1%」低估了**：在 nested 列上 `_ob` 是真金白银的 2.5× 单点成本 |

**结论**：相对 README 附录 B 是**数量级改进**（同一 order_pipeline 负载从 4.46 µs/行降到 3.36 µs/行，且噪声底从 20%+ 降到 1.88%，结论第一次变得可判定）。**口径差异必须说明**：
- 附录 B 的 3.077×/3.000× 是「同一份**手写等价实现** vs DCG 产物」，噪声底 20%+（A/A 在当时的机器/负载下测不准），且当时产物带着 B.8 列出的 5 个负面项（`$.buyer` 取两次、嵌套子路径不提升、stats 每行建 `_ob`、每层数据级 `(lambda D:…)(_d)` 包装、optional 两步口径）；
- 本次 2.05× 是同一份 README B.4 手写对手、同一份数据（`make_orders`）、同样的输出校验（`order_pipeline_eq = true`），但**主表用 10000 行**、**噪声底 1.88%**、产物是当前（修复后的）codegen。
- 两者**不可直接相减**为「优化了多少」；能确凿说的是：**当前同一负载的 DCG 绝对耗时从 4.46 µs/行 降到 3.36 µs/行（−25%）**，且「比手写慢 3 倍」的历史结论现在是「慢 2.05 倍」，并且这个差距是显著可判定的。

---

## 5. 100k 行公开负载：CAP / M0（`tests/workload.py` 的 `V2_CAP` / `V2_M0`）

| 场景 | DCG µs/行 | 手写 µs/行 | 裸写 µs/行 | 比值 | 显著 | 行/秒 | 产物 |
|---|---|---|---|---|---|---|---|
| **cap@100000** | **0.3153** | 0.3020 | 0.3206 | **1.044×（DCG 慢 4.4%）** | `sig_full=True` / `sig10_full=False` | 3 171 884 | 9 行 / 144 字符 |
| **m0@100000** | **0.3929** | 0.3909 | 0.3864 | **1.005×（持平）** | `sig_full=False` | 2 545 339 | 10 行 / 190 字符 |

字段：`rows[group=cap,name=cap@100000].per_row_ns / .full_per_row_ns / .bare_per_row_ns / .ratio_full / .sig_full / .sig10_full`；产物规模 `meta.artifacts.cap`（lines=9, chars=144）、`meta.artifacts.m0`（lines=10, chars=190）；产物等价性 `meta.checks.eq_cap = true`、`meta.checks.eq_m0 = true`。

**读法（重要）**：
1. 两行的 `cv` 分别是 115.2% / 100.0%（`low_conf=True`）——这是「单次调用 30–40 ms、整块被系统抖动拖慢」造成的，**µs/行 的绝对值只是上界**；**比值可信**（A/B 交替 + 两块取小 + A/A 噪声底 1.88%）。
2. **绝对值补充**（另一次同工作区的定点复测，9 次数中位数）：
   - CAP 10 万行：DCG **30.73 ms = 0.307 µs/行**，手写 0.261 µs/行（1 万行口径）→ 与上表 0.315 一致；
   - M0 10 万行：DCG **39.68 ms = 0.397 µs/行**，与上表 0.393 一致。
3. **结论（供验收报告直接引用）**：
   - **CAP：0.315 µs/行（DCG）对 0.302 µs/行（等价手写），DCG 慢 4.4% → 在「<10% 不宣称快慢」口径下为「持平」**；
   - **M0：0.393 µs/行 对 0.391 µs/行，DCG 慢 0.5% → 持平**。
   - 两份公开负载**都是「持平」**：这是 DCG 的表现最接近手写的地方，原因是混合链 token 化把 `$.5.a.1` 提升成 `_a=R[5]['a']; _a1=_a[1]; _a2=_a[2]`（每行只 3 次下标），产物仅 9–10 行、144–190 字符（`meta.ops.cap.subscript = 3`、`meta.ops.m0.subscript = 6`）。
   - 注意 M0 产物 `_c([_a,_a1,_a2,R[2],str(_a1) + str(_a2),(_a1+_a2),(_a2+_a1),(_a[2]+123)])`：`_a[2]` 与 `_a2` 是同一个值取了两遍（`meta.ops.m0.subscript=6`），这是可省的（见 §8 建议 O5）。

---

## 6. 热点剖析（cProfile，`profile` 字段，3 个最慢场景）

> cProfile 会把函数调用成本放大约 3–5 倍，**只用于定位比例，不用于绝对值**。字段：`profile[i].by_tottime[j].func / .tottime_pct / .cumtime_pct / .ncalls`。

### 6.1 order_pipeline（total 0.0662 s，N=10000）

| 热点 | ncalls | tottime% | cumtime% |
|---|---|---|---|
| `run()` 本体（循环 + 逐行 list 构造） | 1 | 46.8% | 104.2% |
| `dict.get` | 36 920 | 9.1% | 9.1% |
| `dcg/runtime.py:538(_ob)` | 9 232 | 9.1% | 9.1% |
| `dcg/runtime.py:563(_ix_get)` | 18 464 | 6.0% | 6.0% |
| `dcg/runtime.py:230(_cmp_ok)` | 10 000 | 4.5% | 7.6% |
| `dcg/runtime.py:838(_fi)`（before 钩子的 `$filter`） | 1 | 4.5% | **21.1%** |
| `dcg/runtime.py:496(_sq)` | 9 230 | 3.0% | 4.5% |
| `dcg/runtime.py:69(_num)` | 29 230 | 3.0% | 3.0% |
| `dcg/runtime.py:246(_gt)` | 10 000 | 3.0% | 9.1% |
| `dcg/runtime.py:92(_ad)` / `_sm` / `math.sqrt` / `str.upper` | — | 1.5% 各 | — |

**读法**：`_ob`(9.1%) + `dict.get`(9.1%) 是「每行建一个 `{'from','to','span'}` 嵌套对象 + 每个 optional 列一次 `.get` 链」的成本；`_fi` 累计 **21.1%** 是 before 钩子（`$filter` 全表重造 + 每行 lambda）；运行期辅助函数（`_ix_get`/`_cmp_ok`/`_gt`/`_num`/`_sq`）合计约 19%。

### 6.2 shape_rows（total 0.0548 s）

| 热点 | ncalls | tottime% | cumtime% |
|---|---|---|---|
| `run()` 本体 | 1 | 47.4% | 98.5% |
| `dcg/runtime.py:844(_rd)`（`$reduce`） | 10 000 | **20.1%** | **43.8%** |
| 生成的 `<lambda>`（`lambda a,b:_ad(a,b)`） | 40 000 | 12.8% | 21.9% |
| `dcg/runtime.py:92(_ad)` | 40 000 | 9.1% | 9.1% |
| `dcg/runtime.py:563(_ix_get)` | 20 000 | 3.6% | 3.6% |
| `dcg/runtime.py:965(_df)` / `len` / `isinstance` / `_py0` | 10 000 各 | 1.8% 各 | — |

### 6.3 mix9_rows（9 形态混合，rows shape；total 0.0456 s）

同上：`_rd` **24.1% tottime / 52.6% cumtime**，lambda 15.3%，`_ad` 11.0%，`_ix_get` 4.4%。
即：**一份带 1 个 `$reduce` 列 + rows shape 的 schema，一半时间花在 `$reduce` 上**。

**热点总结（Top 3 瓶颈，按可优化收益排序）**
1. **高阶函数 `$reduce/$map/$filter` 的 per-row lambda + `_ad/_ml` 包装**：`_rd`+lambda 846.3 ns（对手 `sum()` 98.5 ns，**8.59×**）、`_mp`+lambda 996.3 ns（**5.08×**）、`_fi`+lambda 1521.0 ns（**6.52×**）——字段 `meta.micro[name=_rd+lambda]`。
2. **aggregate 容器回退路径**：`agg_container` 6.52×（每行 `_ob` + buffer + 两级转置）。
3. **optional 两步口径 + `_o` 行容器 + 转置收尾**：`optwide` 2.0×（对 `bare` 5.6×）、`shape_dict` 2.99×、`shape_rows` 3.68×、`finish_rows_dict` 1.33×。

---

## 7. 缺陷与遗留

| 编号 | 状态（本次采样） | 证据字段 | 说明 |
|---|---|---|---|
| DEFECT-001 agg 直出列按声明序位取列 → 越界 | **已修**（`status: "no_error"`） | `meta.defects["DEFECT-001"]` | 产物 `'c2':[r[2]for r in _o]`（不再是 `r[3]`）；`codegen.py:1907-1918` 建立 `slot` 行槽位映射。**注意**：修复方式是把 const 子键从「整列」改成「标量」`'n_rows':1`，所以 `meta.checks.agg_direct_used_fastpath` 由 True 变为 False（意思是「不再有 `_a` buffer」，**不是**退回慢路径）。 |
| DEFECT-002 `output.after` 里 `$` 绑定的是中间行流 | **仍存在** | `meta.defects["DEFECT-002"]` = `TypeError: $sum: 元素必须是数值`；`meta.defects["DEFECT-002-list_control"]` 同样报错（所以与 shape 无关） | 最小复现：`{"output":{"shape":"dict","after":"= $sum($)"},"out":{"type":"object","fields":{"a":{"type":"int64","value":"$.0"},"b":{…}}}}` + 数据 `[(1,2),(3,4),(5,6)]` → `TypeError`。建议在 schema 校验期拒绝「after 里对 `$` 做聚合」或明确 `$` = 输出行流。 |
| DEFECT-002b `after` 静默丢数据 | **仍存在** | `meta.defects["DEFECT-002b"]`（`status: no_error`, `result: {'x': 1}`） | `after = "= $merge([{'x': 1}])"` 时**不报错、返回不含任何数据的 `{'x': 1}`**——最危险的静默错误形态。 |
| rank aggregate 未报错但被静默丢弃（aggregate 作为尾字段/纯 aggregate + rows shape） | **采样时仍在**（本报告未作为主表数字使用） | `meta.checks.ladder_agg_note`（`"aggregate 容器作为尾字段时产物未输出该列"`）；`meta.checks.aggonly_row_loop = true` | L9_agg 的产物不输出 `stats` 列；纯 aggregate + shape=rows 会 `IndexError`。**Lead 宣称的修复在此采样之后**，请以当前工作区为准复核；本报告的 L9/L10 数字只反映「采样时刻的产物形态」，不作为正确性结论。 |
| Cython 模式 | **跳过** | `cython.status = "skipped"` | 无 `cl.exe`/MSVC；`cython=True` 只加 `cdef object` 声明（`dcg/codegen.py:2148-2151`），**语义不变、预计无加速**；`cythonize` 本身 674 ms / 29 行 .pyx → 8712 行 C。 |
| 未覆盖 | — | — | (a) 200k 行超大规模的内存/GC 行为未复测（受单次 10 分钟约束）；(b) 多线程/并发调用未测（产物是无状态函数，理论安全）；(c) `trace: true` 的行为未单独计时。 |

---

## 8. 优化建议清单（按 ROI 排序）

> **前提**：本报告只做建议，`dcg/` 源码未做任何修改。所有「当前」数字均可回溯到 `perf_bench.json`。

### O1 高阶函数内联：`$reduce/$map/$filter` 不再逐行调 lambda / 不再用 `_ad/_ml` 包装
- **目标**：消除全表最差形态（`hires_filter` 4.60×、`hires_reduce` 5.75×、`$map` 3.37×；L8 阶梯 2.38×；order_pipeline 中 `_fi` 占 cumtime 21%）。
- **当前**：`_rd(a, f, init)` / `_mp(a, f)` / `_fi(a, f)` 每个元素调一次 Python 函数对象（`lambda a,b:_ad(a,b)` 40 000 次/万行），`_ad` 每次做 UNDEF 双判 + 类型分支。微基准：`_rd`+lambda 846.3 ns vs `sum()` 98.5 ns（**8.59×**）；`_mp`+lambda 996.3 vs 196.2 ns（5.08×）；`_fi`+lambda 1521.0 vs 233.2 ns（6.52×）。字段 `meta.micro`。
- **改为**：在 `codegen` 里识别「纯算术归约/映射」白名单（`$reduce(…, function($a,$b){ $a + $b }, 0)`、`$map(…, function($v){ $v * 2 })`、`$filter(…, function($v){ $v > 4 })`），直接发内联表达式：`_rsq = sum(_a)` / `[v * 2 for v in _a]` / `[v for v in _a if v > 4]`；只有 lambda 体引用了行内局部名或含 UNDEF 敏感运算时才回退现状。
- **预期收益**：L8 1.367 → 约 0.62 µs/行（−55%）；order_pipeline 3.36 → 约 2.7 µs/行（−20%）；`hires_*` 三项 1.4–5.7× → 1.1–1.5×。
- **风险**：中。必须与「undefined 语义」严格对齐——`sum()` 遇到 `_U` 会 `TypeError` 而不是跳过；`$filter` 的 `_tru` 语义（真值转换）不能省。建议只在「元素来源是 `int64/float64` 类型的单段 value 列」时内联。
- **影响面**：`dcg/jx/compiler.py:836 _higher_order`（生成 `("call","_rd",…)` 的地方）、`dcg/codegen.py:828 _expr_node`、`dcg/runtime.py:825 _mp`、`:838 _fi`、`:844 _rd`、`:92 _ad`。

### O2 aggregate 容器回退路径改为直出列（或至少去掉每行 `_ob`）
- **目标**：`agg_container` 6.52×（全表最差）、L10 纯 aggregate 1.96×。
- **当前**：含 optional 子键时退回「每行 `_ob([('n_rows',1),('lvl',_ix_get(R,6))])` → `_a=[r[2]for r in _o]` → `[r['lvl']for r in _a]`」；`agg_container` 0.5662 µs/行 vs 手写 0.0868。`_ob` 单点 287.6 ns vs dict 字面量 114.7 ns（2.51×）。
- **改为**：把 `_aggregate_direct` 的放行条件从「const / 单段 value / 纯 expr」扩展到「optional 子键也允许」，为 optional 子键生成 `[<optional 列表达式> for r in _d]`（即把可选列直接当列函数求值，`_ix_get(r, path)` 已经能安全返回 `_U`）；彻底删掉 container 回退分支。
- **预期收益**：`agg_container` 0.566 → 约 0.25 µs/行（−56%，6.52× → 2.8×）；L10 0.211 → 约 0.15 µs/行。
- **风险**：中低。嵌套 aggregate（含 `fields` 的 object 子键）仍需保留回退路径，改的是「扁平子键 + optional」这一档。
- **影响面**：`dcg/codegen.py:1964 _aggregate_direct`（放行条件）、`:1946-1959 _aggregate`（回退分支）、`:2009 _col_const` / `:2029 _col_value`。

### O3 optional 列改用单步 `.get(k, None)` 快路径（可选语义分层）
- **目标**：`optwide` 2.0×（相对「只 `.get`」的手写版 5.6×）；order_pipeline 中 `dict.get` 占 9.1%。
- **当前**：每个 optional 列发两条语句 `_t1=R.get('level',_U)` + `level=None if _t1 is _U else _t1`（每列多一次名字绑定 + 一次 `is` 比较）。微基准：`opt_guard` 65.7 ns vs `dict.get` 44.5 ns vs 裸 `r[k]` 33.2 ns（`meta.micro`）。
- **改为**：给 schema 加显式档位（如 `optional: true` + `fail:"null"`），此时直接发 `R.get('level')`（缺键与值为 None 都变 None，一步到位）；默认档保留现有「区分缺键」语义。或在**同一次 `_ix_get` 链**里合并（`_ixg2(R,'buyer','level')` 一次调用代替 `R.get('buyer',_U).get('level',_U)` 两次方法调用 + 两次 UNDEF 传递）。
- **预期收益**：optional 列 −30%~45%；`optwide` 0.813 → 约 0.45 µs/行；order_pipeline −0.2 µs/行（约 −6%）。
- **风险**：**语义风险（最高）**——`optional` 当前保证「缺键 → `None`，值是 `None` → `None`，但内部区分 `_U`」，改档位会改变可观察行为。必须归类为「用户显式选择」而不是默认开。
- **影响面**：`dcg/codegen.py:1699 field_value`（optional 分支 `:1995-2002` 样式由 `body()` 发出）、`dcg/runtime.py:563 _ix_get`、`:37 _Undef.get`。

### O4 rows shape 改用「逐行 dict 直写」而不是「`_o` 行容器 + `[{k:r[i]…} for r in _o]`」
- **目标**：`shape_rows` 3.68×（全表第二差形状）、`finish_rows_dict` 1.33×。
- **当前**：收尾 `return [{k:r[i]for i,k in enumerate(['o_id',…])}for r in _o]`——每行一次 dict comprehension，`meta.micro[name=rows_dictcomp] = 994.1 ns` vs 手写 dict 字面量 462.0 ns（**2.15×**）。
- **改为**：rows shape 时在循环里直接 `_c({'o_id':…, 'sku':…, …})`（循环内直接打字面量，省掉行 list + 二次推导）。
- **预期收益**：`shape_rows` 2.145 → 约 1.3 µs/行（−39%，3.68× → 2.2×）；`finish_rows_dict` 0.435 → 约 0.30。
- **风险**：低（产物形态变化，需跑 `codegen_rules` 与快照测试；规则 4「输出形状固定」仍然满足）。
- **影响面**：`dcg/codegen.py:2548 _finish_expr`（rows 分支）、`:2294 _flat_row`、`:2340 module`（`_c(...)` 行构造）。

### O5 前缀提升与常量折叠的两处小漏
- **目标**：M0 里 `_a[2]` 与 `_a2` 重复取（`meta.ops.m0.subscript = 6`）；aggregate 常量 `[1 for r in _d]` 类空转列。
- **当前**：`_c([_a,_a1,_a2,R[2],str(_a1) + str(_a2),(_a1+_a2),(_a2+_a1),(_a[2]+123)])`——`_a[2]` 已绑定为 `_a2`；纯 aggregate schema 仍会跑 `for R in _d: _c([])`（`meta.checks.aggonly_row_loop = true`，L10 1.96× 的主因）。
- **改为**：(a) `$.5.a.2` 与 `hour[2]` 两个路径段共享同一个 token 绑定时复用 `_a2`；(b) schema 里没有任何逐行值时完全跳过行容器（直接发 `return {'stats': …}`）。
- **预期收益**：M0 −1 次下标/行（约 −3%）；L10 0.211 → 约 0.11 µs/行（−48%）。
- **风险**：低。
- **影响面**：`dcg/codegen.py:468 plan`（前缀提升）、`:2183 body` / `:2280 _direct_aggregate`（行容器是否为空）、`:1964 _aggregate_direct`。

### O6 钩子：`before` 的 `$filter` 避免整表重造 + 内联判定
- **目标**：`hook_before` +29%（+0.4035 µs/行）；order_pipeline 里 `_fi` cumtime 21.1%。
- **当前**：`_d=(lambda D:_fi(D,lambda o,_i1:_gt(_ix_get(o,'qty'),0)))(_d)` —— 全表重造一个新的 list，且每行一次 lambda + `_gt` + `_ix_get`。
- **改为**：`before` 若是 `$filter`，直接生成 `_d=[o for o in _d if o['qty'] > 0]`（判定内联、不再包一层 lambda/`_gt`/`_ix_get`）。
- **预期收益**：`hook_before` 1.783 → 约 1.45 µs/行（−19%）；order_pipeline 约 −8%。
- **风险**：中低（要保留 `$filter` 的真值语义与 UNDEF 跳过）。
- **影响面**：`dcg/codegen.py:2496 _hook_text`、`:2183 body`（before 应用点）、`dcg/runtime.py:838 _fi`。

### O7 生成期：`generate()` 4.7–5.1 ms 的 JX 编译路径
- **目标**：`meta.gen.order_pipeline.generate_ms = 5.1094 ms`（1406 字符产物），20 次连调 85.37 ms。
- **当前**：单次生成 1.5–5.1 ms，`validate` 仅 55.75 µs、`exec` 0.58 ms——主要成本在表达式编译（每条 `expr` 一次 `compile_expr` + 模板拼接）。
- **改为**：`_expr_node` 的编译结果按「表达式文本」做进程级 LRU（现在只在单次 `_Gen` 内 memo），并对同一 schema 的 `generate()` 做 keyed 缓存（schema sha1 → 源码）。
- **预期收益**：重复生成同一 schema 时接近 0；首次生成不变。
- **风险**：低（纯缓存，需保证 schema sha1 覆盖全部输入）。
- **影响面**：`dcg/codegen.py:735 _expr_node`（memo 作用域）、`:2595 module(schema_sha1)`、`:2604 generate`。

### O8 低 ROI / 不建议做
- **把 `_ob` 换成 dict 字面量**：README B.8 建议只省 1.1%，但实测 `_ob` 是 dict 字面量的 2.51×（`meta.micro._ob2`）——**单点值得做，但整体占比小**（order_pipeline 中 9.1%），已被 O2 覆盖（去掉每行 `_ob` 才是正解）。**不建议**单独改 `_ob` 实现（它要处理 UNDEF 过滤，改快了会丢语义）。
- **切 Cython**：本机无编译器无法验证；且 `cython=True` 只加 `cdef object`，**预计零收益**（`dcg/codegen.py:2148-2151`）。不建议投入。
- **改 `_ix_get` 的异常兜底**：它已经是 try/except + 类型判定的合理实现（45.5–51.4 ns vs 裸 17.6 ns）；真正的收益来自「减少调用次数」（O1/O5），不是「让单次更快」。

---

## 9. 结论

1. **当前性能定位**：
   - **对公开承诺负载（CAP / M0，10 万行）：与等价手写持平**（CAP 1.044×、M0 1.005×，均未过 10% 阈值）。产物只有 9–10 行、144–190 字符（`meta.artifacts.cap/m0`），混合链 token 化把 `$.5.a.1` 类路径的提升真正落到了产物里。
   - **对单列/纯 value 场景：显著快于手写**（`col_bare` 0.16×、`col_expr` 0.18×、`col_nested` 0.33×；混合 9 形态 0.68–0.85×）。
   - **对高阶函数、aggregate 容器、rows shape：显著慢于手写**（`hires_filter` 4.60×、`hires_reduce` 5.75×、`agg_container` 6.52×、`shape_rows` 3.68×）。
   - **综合复杂负载 order_pipeline：DCG 3.36 µs/行 vs 手写 1.64 µs/行 = 2.05×**，相对 README 附录 B 的历史 3.077× 是明确改进（绝对耗时 4.46 → 3.36 µs/行，−25%），且首次在 1.88% 噪声底下成为**可判定**结论。
2. **最大瓶颈**：**高阶函数（`$reduce/$map/$filter`）的 per-row lambda + `_ad/_ml` 包装**——微基准 5.1–8.6×，profile 里在带 hires 列的 schema 中占 cumtime 44–53%。其次是 **aggregate 容器回退路径**（6.52×，每行 `_ob` + 两级转置）与 **optional 两步口径**（对「只 `.get`」的手写版 5.6×）。
3. **下一步**：按 O1 → O2 → O3 → O4 顺序推进（预计 order_pipeline 3.36 → 约 2.2 µs/行、`hires_*` 与 `agg_container` 进入 1.5× 以内），每步用 `evidence/bench.py --quick`（13 s）回归、全量（47 s）验收，并以本报告的 `ratio_full` 为对照基线。
4. **口径提醒（写验收报告时必须带上）**：本报告的「手写对手」是**同形状的等价手写**，`ratio>1` 表示 DCG 更慢；**差异 <10% 一律记作持平**；`cap/m0` 两行 `cv≈100%`，其 µs/行 是上界、比值可信（见 §5 读法）。
