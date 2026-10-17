# DCG v2 —— 生成码与运行期对照报告（task-3, verifier-dev）

> 状态：**框架已建，v2 数据列未填**。`dcg/codegen.py` 目前是半成品
> （`dcg/codegen.py:308` 仍有 `raise NotImplementedError`；`dcg/codegen.py:35 __all__` 声明了
> 未定义的 `build`），按 Lead 指令**在 parser-dev 发出「可用」信号前不写入任何 v2 数字**，
> 以免污染 `evidence/`。
>
> 环境：Windows / Python 3.13.3 (MSC v.1943 64bit) / pytest 9.1.1 / numpy 2.3.2 / Cython 3.1.1。
> 所有数字均出自 `evidence/` 下的冻结原始输出。

## 0. 阅读约定（重要）：通过 ≠ 跳过

本报告严格区分三态：

| 记号 | 含义 |
|---|---|
| PASS | 真跑真过，有冻结输出为证 |
| FAIL | 真跑真错，附最小复现 |
| **SKIP** | **因 v2 未就绪而跳过，不构成任何证据**（既不证明对，也不证明错） |

当前 `pytest tests -q` 是「全绿」的，但其中 v2 部分**全部是 SKIP**；
`tests/test_e2e_flight.py` 现在只有 `TestFrozenV1Baseline` 4 项是真跑真过。

## 1. 方法学（CONTRACT §7 + Lead m00414 裁决，冻结，不得改动）

- 同机、同数据（200000 行 × 8 字段）、**同进程**；`gc.collect()` 后计时；预热后取 **3 次最小值**。
- 行形状：`(code:str, a:int, b:int, c:int, d:int, payload:{'a':[4]})`。
- 计时实现：`bench/_workload.py bench_reps()`；`bench_row.py` 在计时前对所有变体做
  **对称 settle sweep**，并实现**自适应追加**（理由与实测见 §5.3/§5.6）。
- 报告一律给出 **最小值 + 3 次波动区间 + 实际 settle 轮次数**，不用单次值下结论。

**Lead m00414 正式裁决（本报告按此执行）**

1. **主判定 = 同进程、同顺序控制下的 v1↔v2 相对比值**；门只看比值，不看绝对 µs/行。
2. **0.457（M0 手写下限）/ 0.480（v1 冻结基线）降为参考值**；报比值时必须附**当次实测**的手写参照值。
3. **硬规则：禁止把跨会话的绝对数字直接两两相减/相比。**
4. 效率承诺表述为比值：**v2 ≤ 1.05× 手写参照**（§12 冻结基线不改，它是历史记录）。
5. **波动 >5% 时必须追加 settle 轮次**，并报告最小值与轮次数；不允许只报一次。

### 1.1 为什么 v1 那一列只有**纯 Python** 路径（Lead m01316 要求写清）

v1 引擎（`dcg.py` + `format.py`）只有一种运行形态：把生成的 Python 函数编译进当前解释器执行。
它**没有** Cython 路径、也没有类型化列输入路径，所以：

- 三方对照里 v1 的耗时**只能**与 v2 的 `dcg.generate()` 纯 Python 产物比；把 v1 与 v2 的
  `cython=True` 产物放同一列比是**不同成本的对手**，会得出「v2 快/慢」的错误结论。
- 因此本报告的 v2 计时分三档，且**禁止跨档比较**：
  | 档 | 产物 | 可比的对手 |
  |---|---|---|
  | 纯 Python | `generate(schema)` | 手写参照、v1（**主判定在这里**） |
  | Cython（`cdef object`） | `generate(schema, cython=True)` | 只能与自己那档比（§5.12：只快 ~4%） |
  | 类型化列输入（C 类型） | **本次不实现** | 无（§7 的 0.419 目标属这一档，留作可选第二产物） |
- 手写参照（`bench/decompose.py` 变体 A / `_workload.ref_tight`）同样是纯 Python、同样构造
  `{'doc': [...]}`，所以它和 v1、v2 是同一成本结构 → 比值才有意义。

### 1.2 PROVISIONAL 纪律与静默窗口（Lead m00521 / m01316 → **m01965 作废静默窗口**）

- **性能终值不再需要静默窗口**（Lead m01965 ⑦）：`bench/row_v2.py` 已改为 **CPU 时间口径**
  （`time.process_time()`，K=8 遍摊销、交替测量、配对聚合、A/A 噪声底、3 轮 worst-of），
  对并发 agent 抢占免疫 → `--frozen` 单跑即出可引用终值（§3.2e-final）。
  历史壁钟口径（含 W2 FAIL）保留在 §3.2b/§3.2e，标注 `contaminated_by_concurrent_agents`。
- 仍必须带 **python 进程表 + 时间戳 + sha1**；**未钉 sha1 的字符数/性能数字一律不可引用**
  （parser-dev 的 codegen 曾是分钟级变动：同一探针 CAP 202→174、M0 287→283，其间还有崩溃中间态）。
- v2-native（B 版）的字符数**只能当辅助信息**，禁止单独引用它宣称达标（Lead m01316 ①）。
- `bench/quiet_window.py` 仍在仓库里作为**历史流程**的存档（窗口标签流程已不用于性能终值）。


## 2. 生成码字符数（Verdict: 上限）

**基准必须写清楚是哪一版 schema** —— 同一个语义 workload 有两版写法，字符数差 70–80（Lead 追查的
「预算打架」根因，最小复现已冻结在 `evidence/v2_m0_basis_probe.txt`，探针 `bench/probe_v2_m0_basis.py`）：

- **A 版 `ext`（v1-faithful，就是 v1 基线 445/537 用的那份 schema）**：`input.source="index"`（多一行
  `_in=_in['index']`，且调用约定是 `run({"index": rows})`）+ 反引号段点名了 `hourB` → codegen 必须物化
  别名 `hour/hourA/hourB`。**这才是「同一 workload 公平比较」的对象。**
- **B 版 `v2-native`（等价语义的 JSONata 写法）**：`$string($.5.a.1) & $string($.5.a.2)` 直接内联
  `str(_a1)+str(_a2)`，不需要别名行；无 `input.source`。

revision（**sha1 重锚到 W3**，Lead m01666 ④ / m01965）：**`dcg/codegen.py sha1=A40457511277FFDA701ECEB515BA37E4BA8CFDA2`**（44136 B）、
`dcg/runtime.py sha1=CD760A6FBA57B8144118A60520C8CAF2A62848BB`、`dcg/schema.py=F4CE36DA9C9A127AEE67568B320BF13DB7F522CE`
（2026-10-17 01:44 复采，run 前后 sha1 相同 = revision stable；证据 `evidence/v2_codegen_report_current.txt`
含时间戳 + 三份源码 sha1 + run 后 sha1）。下表的字符数在 W3 上**逐项复采仍为** CAP[A]=157 /
CAP[B]=149 / M0[A]=195 / M0[B]=177，九条规则四组全 9/9，`product-exec` 编译 1 次，确定性 True。
revision 漂移轨迹（parser-dev 的 task-7 `py{}` 接线期间）：`FE989B54 → 28921845 → 2DC5FCB4 → A4045751`，
**每个数字都必须按采集当时的 sha1 锚定，不得拿旧数字充终值**。
历史 revision 对照：内联前 `sha1=5FE957B8…` CAP[A]=174 / M0[A]=283（下述「内联前」列）。

| 负载 | 基准 | 字段数 | v1 实测（冻结） | v2 上限 | v2 实测（内联后） | 判定 | 内联前 |
|---|---|---|---|---|---|---|---|
| CAP | A `ext` | 4 | **445**（+118 空桩） | ≤180 | **157**（nodoc 156） | **PASS**（−64.7%） | 174 |
| CAP | B `v2-native` | 4 | —（同一语义） | ≤180 | **149**（nodoc 148） | **PASS**（−66.5%）*[informational]* | 149 |
| M0 | A `ext` | 8 | **537**（+117 空桩） | ≤210 | **195**（nodoc 194） | **PASS**（−63.7%） | **283 FAIL** |
| M0 | B `v2-native` | 8 | —（同一语义） | ≤210 | **177**（nodoc 176） | **PASS**（−67.0%）*[informational]* | 177 |

- **Lead m01316 ① 的裁决已执行**：A（ext, v1-faithful）是**主基准**，带 PASS/FAIL；B 只作辅助数据，
  行尾强制标注 `[informational — 不得单独用来宣称达标]`（`bench/bench_codegen.py` 内置该标注）。
- **M0[A] 由 283 FAIL → 195 PASS**，靠的是 CONTRACT §4.3.1 编译期内联（可证类型安全的字段走裸运算、
  不再发 `_ix_get(hour,2)`、不再有 `is _U` 守卫）。曾用于过渡的两条 `xfail` 已按裁决**删除并转真断言**
  （`tests/test_e2e_flight.py::TestCodegenRules::test_char_budget_m0_v1_faithful_basis` 与
  `::test_inline_leaves_no_helper_for_statically_provable_index`）。
- `chars_nodoc` 与总字符数只差 1（docstring 已被 parser-dev 去掉），所以**上限口径（总/无 docstring）
  现在不再影响判定**。

`compile()` 次数（口径见 §4a —— 必须区分「产物编译」与「反引号段编译期校验」）：

| | v1 | v2 |
|---|---|---|
| 产物（含 `def run(`）编译次数 | **2**（118/117 空桩 + 真产物） | **1**（CAP/M0 皆是，`build()` 亦 1） |
| 反引号段校验次数 | 0 | **2 次/每段**（A 版；B 版 0 —— 同一段重复校验，可省成本，非产物编译） |

确定性：同一 schema 两次 `generate()` 产物**字节完全相同** → 第 ⑧ 条 **PASS**。
证据：`evidence/v1_capture_src.txt`（v1）、`evidence/v2_codegen_report_inlined.txt`（本次，含
run 前/后 sha1 + 进程表 + 四组 A/B）、`evidence/v2_m0_basis_probe.txt`（基准差异）、
`evidence/v2_compile_count_probe.txt`（compile 口径）。

## 3. 运行期性能

### 3.1 冻结基线（**历史值，仅作定位参考**）

| 项 | 冻结值（历史） |
|---|---|
| M0 手写下限（纯 Python） | 0.457 µs/行 |
| v1（`format.Format.parse`） | 0.480 µs/行 · 2,083,896 行/秒 · 构建 3.05 ms |
| Cython row-object | 0.401–0.419 µs/行 |
| Cython 类型化列输入 | 0.284 µs/行 |
| v2 目标（Lead m00414 改为比值口径） | **v2 ≤ 1.05× 当次手写参照** |

⚠️ 上表**禁止**用于与本次实测做绝对差值比较（Lead 硬规则 3）。

### 3.2 本次实测（`evidence/bench_row_v1side.txt`，N=200000，min of 3，自适应 settle）

| 变体 | 最小值 µs/行 | 3 次明细 µs/行 | 波动 | 实际 settle 轮次 | 行/秒 | vs 手写参照 |
|---|---|---|---|---|---|---|
| 手写参照（unpack once） | **0.475** | 0.698 / 0.571 / 0.475 | 47.1% | 5（**到上限仍 >5%，已如实报告**） | 2,106,532 | 1.00x |
| v1（`format.Format.parse`） | **0.507** | 0.733 / 0.535 / 0.507 | 44.8% | 5（同上） | 1,973,981 | **1.07x** |
| v2（`dcg.generate` + run） | SKIP | — | — | — | — | — |
| v2 `cython=True` | SKIP | — | — | — | — | — |

正确性前置：手写参照与 v1 在 200000 行上**逐行相等**（`matches hand ref: True`）。
**计时前先验正确性，绝不给错代码计时。**

#### 3.2b v2 侧（**PROVISIONAL (concurrent load)**，`evidence/bench_row_v2side_inlined.txt`，N=200000，M0 8 字段负载，`V2_M0` A 版基准）

revision: `dcg/codegen.py sha1=FE989B549A212998AB7B4D397A3F1DCE6C93E339`（**run 前 = run 后**，
`revision stable during run = YES`）；进程表 5 个 python.exe 并存。

| 变体 | 最小值 µs/行 | 3 次明细 µs/行 | 波动 | 实际 settle 轮次 | 行/秒 | vs 手写参照 | vs v1 |
|---|---|---|---|---|---|---|---|
| 手写参照（unpack once） | 0.567 | 0.759 / 0.601 / 0.567 | 33.9% | 5（触上限，已报告） | 1,763,668 | 1.00x | 0.95x |
| v1（`format.Format.parse`） | 0.597 | 0.597 / 0.750 / 0.637 | 25.6% | 5（触上限，已报告） | 1,675,042 | 1.05x | 1.00x |
| **v2（`dcg.generate` + run）** | **0.593** | 0.672 / 0.603 / 0.593 | 13.4% | 5 | **1,686,341** | **1.05x** | **0.99x** |
| v2 `cython=True`（同负载，pyximport 真编译 6.61s） | **0.455** | 0.462 / 0.474 / 0.455 | **4.2%** | 5 | **2,197,802** | **0.80x** | **0.76x** |

正确性前置（计时前先验）：`[v2] matches hand ref: True  matches v1: True`；cython 变体
`matches hand ref: True`（pyximport 真编译后语义一致）。
判定（主判定 = 比值）：`[PASS] v2 纯 Python 1.05x hand ref（上限 1.05x）`、
`[PASS] v2 Cython row-object 0.80x hand ref`。

**跨轮稳定性（同一内联 revision 家族，全部 PROVISIONAL）**——比值区间而非单点：

| 轮次 | revision sha1（前 8 位） | hand ref | v1 | v2 | **v2/hand** | **v2/v1** |
|---|---|---|---|---|---|---|
| 00:58 | 710DFD7B | 0.744 | 0.735 | 0.598 | 0.80x | 0.81x |
| 00:59 | CBC93A8D | 0.685 | 0.732 | 0.614 | 0.90x | 0.84x |
| 01:00 | FE989B54 | 0.689 | 0.851 | 0.514 | 0.75x | 0.60x |
| 01:01（含 cython） | FE989B54 | 0.567 | 0.597 | 0.593 | **1.05x** | 0.99x |

即 **v2/hand = 0.75–1.05x、v2/v1 = 0.60–0.99x，四轮全部落在 ≤1.05x 门内**；但波动 13–40%（并发负载），
所以**这只是 PROVISIONAL：终值必须在 Lead 的静默窗口里重跑**（§1.2）。v2 的 min 在轮间跳 0.51–0.61，
与手写参照同步跳 0.57–0.74，比值比绝对值稳。

**内联前（历史，同一脚本、同一负载）**：v2 = **1.776 µs/行 = 3.26x hand ref / 3.12x v1（FAIL）**，
cython 0.858（仅快 4%）。→ CONTRACT §4.3.1 内联把 v2 从 3.26x 拉到 ≤1.05x。

#### 3.2c 效率门 harness `bench/row_v2.py`（`evidence/accept.py:698` 等待的文件）——**PROVISIONAL（01:05:32）**

`bench/row_v2.py` 是 `evidence/accept.py` 的「§5 efficiency (ratio vs hand reference)」gate 显式等待的
harness（`accept.py:698-701` 检查它是否存在），接口：

- 就绪门：`W.v2_ready()` 失败 → **exit 4 且一个 v2 数字都不写**；
- 方法学**不重复实现**：直接 `import bench_row`，复用 `measure()`（warmup + `gc.collect()` + 3 次取最小 +
  波动 >5% 自适应追加 settle 至上限），避免两份口径漂移；
- 输出一行机器可读结论：`EFFICIENCY_RATIO v2_over_hand=… v2_over_v1=… limit=1.05 verdict=PASS|FAIL
  n=… reps=… revision_stable=… provisional=…`；
- 写 `evidence/row_v2.json`：`provisional` / `window` / `verdict` / `ratios` / `us_per_row` /
  `sha1` + `sha1_before` / `revision_stable_during_run` / `others_python_processes` / `process_table`；
- 退出码：0 PASS / 1 FAIL（含 revision 漂移）/ 3 拒绝（`--frozen` 未给 `--window`）/ 4 未就绪；
- **`--frozen --window <标签>`**：只有 Lead 开窗时才允许把该轮标为非 provisional 终值，否则拒绝（exit 3）。

实测（N=200000，M0 8 字段负载，**锚定 `dcg/codegen.py sha1=2892184500C19B4EE5F2C1063A875108C9364455`**、
`runtime.py=CD760A6FB…`，run 前后 sha1 相同 → `revision_stable=True`，5 个其他 python 进程；时间戳 01:05:32）：

| 变体 | min µs/行 | 波动 | vs hand | vs v1 |
|---|---|---|---|---|
| 手写参照 | 0.517 | **11.5%（触 settle 上限）** | 1.00x | 0.83x |
| v1 | 0.430 | 1.3% | 0.83x | 1.00x |
| **v2 纯 Python** | **0.409** | 1.1% | **0.79x** | **0.95x** |
| v2 `cython=True` | 0.335 | 5.0% | 0.65x | 0.78x |

`EFFICIENCY_RATIO … verdict=PASS`（`v2/hand=0.791`、`v2/v1=0.953`）。

⚠ **诚实的保留意见（不得当作终值）**：本轮**手写参照是被测变体里最慢的一个**（0.517，波动 11.5%，
settle 触上限），而它恰好是**第一个被测**的变体——与 Lead 在 m00414 认定的「顺序伪影」同形态
（首轮 0.520/0.576 偏高）。因此 `0.79x` 里有可归因于测量顺序的乐观成分。建议（待 Lead 裁决，
不擅自改冻结方法学）：静默窗口内把各变体的**测量顺序在 3 次明文轮次间轮转**（`min` 语义不变），
消除「谁先测谁吃亏」，并同时报 `v2/v1`（本轮 0.953，不依赖手写参照的噪声）。

#### 3.2d 顺序轮转**通路验证**（Lead m01666 ② 采纳；**PROVISIONAL，不是证据**，01:07:58）

`bench/row_v2.py` 现在跑**两轮**：`ROUND 1/2 固定顺序` + `ROUND 2/2 轮转顺序`
（轮转由 `bench_row.VARIANT_ORDER` 钩子实现，只换首测位；`min` 语义 / settle 规则 / 同进程同数据全部不动）。
`evidence/row_v2.json` 同时保存 `rounds.fixed` 与 `rounds.rotated`（外加 `us_per_row_by_round` 与两轮逐变体
取小的 `us_per_row`），顶层 `ratios`/`verdict` = **两轮中较差者**（`worst_of: "fixed|rotated"`），
`rounds_agree` 记录两轮结论是否一致。

实测（N=200000，锚定 `dcg/codegen.py sha1=2DC5FCB4…`，run 前后相同；6 个其他 python 进程）：

| 轮次 | hand | v1 | v2 | **v2/hand** | v2/v1 | 判定 |
|---|---|---|---|---|---|---|
| 固定顺序 | 0.492 | 0.474 | 0.516 | **1.048** | 1.088 | PASS（贴门） |
| 轮转顺序 | 0.503 | 0.468 | 0.449 | **0.893** | 0.959 | PASS |

→ 两轮都 PASS，但**差异 0.155x（17%）**，且固定顺序那一轮只差 0.002 就 FAIL，**这正是必须留两个口径的理由**。
另一观察：轮间的方差主要来自 **v2 自身**（0.516 vs 0.449），不是手写参照（0.492 vs 0.503）。

**结论注记（Lead m01792 要求：同时给差值、偏置方向、哪一轮贴边）**
- **差值**：`v2/hand` 固定 1.048 ↔ 轮转 0.893，相差 **0.155x（17%）**；`v2/v1` 1.088 ↔ 0.959。
- **偏置方向**：差异几乎全部落在 **v2 变体自身**（0.516 → 0.449，−13%），手写参照只动 2%（0.492 → 0.503）。
  也就是说「固定顺序」这一轮把 **v2 测慢了**（而不是把 hand 测快了），所以固定顺序是**保守侧**、轮转是**乐观侧**。
  （仅两轮 + 当时 6 个 python 进程，方向判断的证据强度有限；窗口轮若要定性，应加第三轮。）
- **哪一轮贴边**：**固定顺序轮 = 1.048，距 1.05 的门只差 0.002**。→ 当前 v2 的性能余量**≈0**，
  任何额外每行开销（例如 task-7 `py{}` 的每行一次 helper 调用，方向一致）都会把它推出门；
  窗口那一轮必须**无旁路进程**，并且我会把这一轮的贴边事实写进 `bench_compare.md` 的结论而不是只写 `PASS`。
- **口径（不改）**：两轮都留证（`rounds.fixed` / `rounds.rotated`），顶层 `verdict` = 较差者；
  但**报告与最终答复必须同时呈现两轮数字**，不得只报较好的一轮。

另一次 N=20000 的干跑（同一 revision）两轮都是 **FAIL（1.212 / 1.285）**——**N 越小比值越差**，
所以静默窗口那一轮**必须钉 CONTRACT §7 的 200000 行**，不能用小 N 结论；小 N 只用来验证通路。

#### 3.2e-final ✅ **W3 终值（CPU 口径，可引用）** —— `evidence/row_v2.json`，`provisional=false`

**这是本报告的性能结论**。口径按 Lead m01965/m02010 重建（CPU 时间 + 配对聚合 + A/A 噪声底 + 判决规则），
不再需要静默窗口：`time.process_time()` 只计本进程 CPU 消耗，不受并发 agent 抢占影响。

**锚定 revision（run 前后 7 文件 sha1 逐字节一致 → `revision_stable_during_run=True`；parser-dev 已确认不再改 `dcg/`）**

| 文件 | sha1 | 字节 |
|---|---|---|
| `dcg/codegen.py` | `A40457511277FFDA701ECEB515BA37E4BA8CFDA2` | 44136 |
| `dcg/runtime.py` | `CD760A6FBA57B8144118A60520C8CAF2A62848BB` | 14792 |
| `dcg/schema.py` | `F4CE36DA9C9A127AEE67568B320BF13DB7F522CE` | 11137 |
| `dcg/jx/compiler.py` | `9C5B2C8128A0C5DE4D8A2309544F4128E153EE8A` | 19410 |
| `dcg/ir.py` / `dcg/build.py` / `dcg/__init__.py` | `1C9F537A400D…` / `79EC43214916…` / `712B4F4A5863…` | 5654 / 375 / 884 |

**口径**（`methodology_cpu`）：`time.process_time()`，每次测量 **K=8 遍摊销** ~15.6 ms 计时粒度、变体**交替**测量、
每轮 **7 个 rep**、共 **3 轮**；主聚合 = **每个 rep 内按变体配对的比值中位**，判决取 **worst-of-rounds**（保守，
防「挑轮次达标」）。**A/A 噪声底**（同一 callable 占两个槽位、顺序与实测一致、3 个 block）= **0.0142**。

```
EFFICIENCY_RATIO v2_over_hand=0.985 v2_over_v1=0.957 limit=1.05 verdict=PASS confidence=RESOLVED margin=0.0646
```

| 轮次 | hand | v1 | v2 | v2cy | **v2/hand** | v2/v1 | 判定 |
|---|---|---|---|---|---|---|---|
| R1 | 1.3867 | 1.3770 | 1.3574 | 1.2500 | **0.9789** | 0.9858 | PASS |
| R2 | 1.2402 | 1.3086 | 1.3086 | 1.2500 | **0.9710** | 0.9620 | PASS |
| R3 | 0.9082 | 0.9082 | 0.9082 | 0.8594 | **0.9854** | 0.9568 | PASS |
| **worst-of（判决）** | — | — | — | — | **0.9854** | **0.9568** | **PASS / RESOLVED** |
| 稳健参考（中位） | — | — | — | — | 0.9789（margin 0.0711） | — | RESOLVED |

- `blocks_disagree_in_sign=False`，`round_verdicts=["PASS","PASS","PASS"]`，`confidence=RESOLVED`
  （margin 0.0646 ≫ noise floor 0.0142，约 **4.6×**）。
- **诚实注记 1（绝对 µs/row 不可引用）**：三轮绝对时间从 1.39 掉到 0.91 µs/行（35%），是机器负载在测量期间
  下降（进程表仍见 5 个空闲 python 残留）→ 按 Lead 纪律**只引用比值**；绝对列仅作标定。
- **诚实注记 2（计时量化）**：K=8、n=200000 → 单次测量 = 1.6e6 行遍；`process_time()` 网格 15.625 ms
  ⇒ 每行网格 ≈0.0098 µs（约 1.1%）。这正是 `us_per_row_cpu` 里 hand/v1/v2 会落在同一格点、
  而**配对 per-rep 比值（7 对）仍能分辨差异**的原因；判决用的 margin（6.5%）是噪声底（1.42%）的 4.6 倍，
  故 RESOLVED 成立，但**分辨率约 1% 是本轮方法的上限**，不得据此宣称小于 1% 的改进。
- `v2 cython=True`（**不计入 CAP/M0 预算**）0.8594 µs/行。
- 命令：`python -X utf8 bench/row_v2.py --frozen --n 200000`（窗口自动生成 `CPU-2026-10-17T01:40:37+08:00`），
  stdout 冻结在 `evidence/row_v2_w3_stdout.txt`；同 revision `pytest tests/` = **717 passed / exit 0**
  （`evidence/v2_pytest_current.txt`）。
- **W2（下节）的 FAIL 已被本节取代**：W2 的失败主因是测量方法（wall-clock 无 5% 分辨率），见 §3.2f。

#### 3.2e 窗口终值历史 —— W2-2026-10-17T01-12（`evidence/row_v2.json` 旧快照，`provisional=false`）

> **W2 = `A40457511277`，FAIL，已被 W3 取代；终值见 §3.2e-final（PASS / RESOLVED）。**
> （Lead m01892 裁定：本 JSON 作为**有效历史证据**保留——它记录「在 `A40457511277` 上 wall-clock 口径
> 曾判 FAIL」这一已发生的事实；但它**不是最终答复的数字**。）

Lead m01810/m01812 开的窗；`bench/row_v2.py --frozen --window W2-2026-10-17T01-12 --n 200000`
（run 前后 sha1 逐字节一致，`revision_stable_during_run=True`）：
`dcg/codegen.py A40457511277…` / `dcg/runtime.py CD760A6FBA57…` / `dcg/schema.py F4CE36DA9C9A…`
/ `dcg/jx/compiler.py 9C5B2C8128A0…` / `dcg/ir.py 1C9F537A400D…`。
机器：`machine.load` PRE 17.1% → POST 8.5%（3×1s 均值），进程表 = 5 个 22:20/22:21 起的**空闲残留**
（完整原文 + 时间戳在 `evidence/row_v2_window_machine.txt`，stdout 在 `evidence/row_v2_window_stdout.txt`）。

| 轮次 | 顺序 | hand | v1 | v2 | **v2/hand** | v2/v1 | 判定 |
|---|---|---|---|---|---|---|---|
| 固定 | hand→v1→v2→v2c | 0.477 | 0.484 | 0.472 | **0.988** | 0.975 | PASS |
| 轮转 | v1→v2→v2c→hand | 0.468 | 0.471 | 0.498 | **1.065** | 1.057 | **FAIL** |
| **worst-of（顶层）** | — | — | — | — | **1.0645** | **1.0572** | **FAIL** |

逐变体明细（min / 3 次 reps / 波动 / settle 轮数）：

| 变体 | 固定轮 | 轮转轮 |
|---|---|---|
| hand | 0.477 / [0.490,0.477,0.499] / 4.5% / 2 | 0.468 / [0.468,0.470,0.473] / **1.1%** / 2 |
| v1 | 0.484 / [0.512,0.484,0.578] / 19.5% / 5（上限） | 0.471 / [0.549,0.536,0.471] / 16.7% / 5（上限） |
| v2 | 0.472 / [0.479,0.472,0.557] / 18.1% / 5（上限） | 0.498 / [0.504,0.499,0.498] / **1.3%** / 3 |
| v2 cython | 0.475 / [0.489,0.475,0.513] / 8.0% / 5（上限） | **0.392** / [0.394,0.402,0.392] / 2.6% / 2 |

**终值结论（诚实版，不美化）**
- **主判定 `v2/hand = 1.065 > 1.05` → FAIL**；但它是**压在门上**的临界结果（固定轮 0.988 PASS、
  轮转轮 1.065 FAIL，两轮都贴近 1.00），不是显著退化。`evidence/accept.py` 的
  「§5 efficiency (ratio vs hand reference)」gate 因此会**红**，且这是真数字而非基础设施故障。
- **两轮差 0.077x（约 7.8%）**；`rounds_agree=False`。**偏置方向与 3.2d 干跑相反**：这次是**轮转轮把 v2
  测慢**（0.472→0.498，hand 只动 0.477→0.468），上次干跑是固定轮把 v2 测慢 → **偏置不固定偏向某一顺序**，
  取决于哪个变体撞上 CPU 爬坡/抢占瞬态（固定轮 v2 波动 18.1% 撞上 0.557 离群；轮转轮 v2 极稳 1.3% 但值偏高）。
- 合并（两轮逐变体取小）：hand 0.4676 / v1 0.4708 / v2 0.4716 / v2c 0.3918 µs/行。
- `v2 cython=True`（**不计入 CAP/M0 预算**）轮转轮 **0.392 = 0.84x hand / 0.83x v1**，已优于 §7 的
  cython row-object 参考 0.419；但该轮 `pyximport 真编译 0.00s` 是**缓存命中**（build_dir 已有产物），
  干净编译耗时是 5.75–7.64 s（早期测量）。
- 正确性前置：四变体 200000 行全部 `matches hand ref: True`，`v2 matches v1: True`。

#### 3.2f 测量方法学推翻与重建（Lead m01939；**这是本轮最重要的方法学交付**）

**① W2 的「未达标」主因是测量方法，不是 codegen。**
Lead 在同一进程 / 同一数据 / 同一产物上用 `time.process_time()`（CPU 时间口径，每测 K=4 遍摊销 Windows
~15.6 ms 计时粒度、交替 7 轮取 min、3 个独立 block）重测：

| 变体 | µs/row | x/hand（3 block） |
|---|---|---|
| hand ref `ref_tight`（无守卫） | 0.7031 | 1.000 / 1.000 / 1.000 |
| v2 M0 `filter=true`（冻结 `A4045751`） | 0.7031 | **1.028 / 1.000 / 1.000** |
| v2 M0 `filter=false`（同样行） | 0.7031 | 1.028 / 1.000 / 0.973 |

三个产物的行输出与手写参照**逐行相等**（已断言）→ **可控测量下 `v2/hand ≈ 1.00–1.03`，即 ≤1.05 达标**。
噪声底标定（**同一个函数**交替测量 15 轮）：单变体自身 spread **39%–97%**，「比值」在 **0.97–1.17** 随机漂
→ **wall-clock 方法根本没有 5% 分辨率**；W2 的三轮 1.117 / 0.901 / 0.937 与 examples-dev 的 12 轮触顶
52.8%，同源。

**② 一条被撤回的结论（留档，教训）**：Lead 曾用 wall-clock 单行 A/B 断言「恢复 `_ap = _o.append` 预绑定
可回收 5.5%」，据此下派 parser-dev；在 5–9 个并发 python 进程下那是噪声。独立复测（`time.process_time()`、
K=4、交替 7 轮 min、3 block、200k 行）的 2×2 消融：

| 变体 | µs/row | x/base（3 block） |
|---|---|---|
| base（无守卫、未绑定） | 0.6641 | 1.000 / 1.000 / 1.000 |
| **prebind only（`_ap`）** | **0.6641** | 1.000 / 0.972 / 1.000 |
| guard only（`if R:`） | 0.6836 | 1.000 / 0.972 / 1.029 |
| prebind + guard | 0.6836 | 1.000 / 0.972 / 1.029 |

→ **`_ap` 的效果 = 0（精确相同）**；`if R:` = **+2.9%**。**裁决 R2：`_ap` 不落地，规则③与
`tests/test_codegen_snapshot.py:202-203` 的形状判据照原样保留**（不发射 prebind 就不会误伤）。
**教训：把两个不同变体的 wall-clock 数相减当效应量，是无效结论。**

**③ final 窗口的新方法学（Lead m01939，本 harness 将升级为双口径）**
1. **CPU 时间口径（新主判定）**：`time.process_time()`，每测内跑 **K 遍**摊销 Windows 计时粒度，
   **min of 7**，`hand/v1/v2/v2c` **交替（interleaved）** 而非分块顺序测 → `us_per_row_cpu`。
2. **wall-clock 口径保留透明**：现有 `us_per_row`，标注 `contaminated_by_concurrent_agents`。
3. **A/A 噪声底标定（必做）**：把同一 callable 测两次（A/A），打印 `noise_floor = ratio(A2/A1)` 与两 slot spread；
   超出 ±5% 则该轮**宣布方法无判定力**（`INCONCLUSIVE`），不硬判 PASS/FAIL。
4. `verdict` 只在 CPU 口径下判；`EFFICIENCY_RATIO` 行同时打两种口径。
5. 同一 revision 上跑 **3 轮 CPU 口径**并报跨轮一致性。

**④ 验证方对该方法学的补充（已发 Lead，待裁）**
- **判定余量规则**：不能只看「噪声底 > ±5%」。应加 `margin = |ratio - limit|`；`margin < noise_floor` 时
  仍可给二元 `PASS/FAIL`（保住 `accept.py` 的机器接口），但必须标 `confidence="WITHIN_NOISE"`，避免
  又一次「拿噪声当判决」。
- **K 的量化下限**：200k 行 × ~0.7 µs ≈ **0.14 s/遍**；Windows 粒度 ~15.6 ms → K=4 时单测量量化 ~2.8%，
  比值最坏 ~4–5%，**不足以支撑 1.05 的门**；建议 **K=8**（≈1.1 s/测量，量化 ~1.4%）。§7 钉死 N=200000，
  只能抬 K。
- **A/A 需 ≥3 个 block 且复用同一 slot 顺序**，报最差 `|ratio-1|`；单组 A/A 可能碰巧合格。

**归因证据（`bench/probe_helper_overhead.py`，`evidence/v2_helper_overhead_inlined.txt`，CAP 负载）**：

| 变体 | min µs/行 | vs inline |
|---|---|---|
| inline（内建 `str`/`+`，最优手写上界） | 0.464 | 1.00x |
| helpers（runtime `_S`+`_ad`，契约 §4.4 强制路径） | 1.274 | **2.75x** |
| **product（`dcg.generate` 产物）** | **0.484** | **1.04x** |

→ 内联后产物的结构开销已降到 **1.04x**（不再是内联前的 1.98x）：`product/inline = 1.04x` 说明
「v2 产物 ≈ 最优手写」，而 `helpers/inline = 2.75x` 是**契约强制的 helper 形态**的代价。
正确性前置：`product==inline True  helpers==inline True`。

**⑤ 重建后的方法学在 W3 上的结果（本节结论）**

Lead m01965/m02010 采纳上面的 ①②③④ 并把判决规则钉死（`margin` / `noise_floor` / `confidence` /
`blocks_disagree_in_sign`）。**W3（`A40457511277`）按新口径实测 = PASS / `confidence=RESOLVED`**
（`v2_over_hand=0.985`、`margin=0.0646`、A/A `noise_floor=0.0142`，三轮 `PASS`，`rounds_agree=True`），
详见 §3.2e-final。三条可直接引用的教训：

1. **wall-clock 差值不可作效应量**：W2 的 1.065 FAIL 与「`_ap` 回收 5.5%」同源，都是并发负载噪声；
   同一 A/A callable 的 wall-clock 单变体 spread 达 39%–97%。
2. **CPU 口径 + K 遍摊销 + 配对比值**把可判定分辨率压到 ~1%，足以支撑 1.05 的门。
3. **判决仍必须报 `confidence`**：`WITHIN_NOISE` / `INCONCLUSIVE` 不得当作达标（`accept.py` 按 Lead
   m02010 对这两态直接 FAIL）。

**跨轮对比 —— 这组数字正好证明了 Lead 的比值裁决是对的：**

| 轮次 | 手写参照 min | 波动 | v1 min | 波动 | **v1/hand 比值** |
|---|---|---|---|---|---|
| 早期（2 轮 settle） | 0.472 | 5.5% | 0.519 | 9.2% | 1.10x |
| 本次（5 轮，含 3 个并行 agent） | 0.475 | 47.1% | 0.507 | 44.8% | **1.07x** |

绝对 min 都在 0.47–0.52 之间乱跳、波动最高 47%，但**比值稳定在 1.07–1.10x**。
→ 绝对 µs/行 只能定位，**比值才是可判定的量**。


### 3.3 微分解对照（`bench/decompose.py`，N=50000，历史冻结输出）

| 变体 | µs/行 | vs A |
|---|---|---|
| A `ref_tight`（解包一次） | 0.558 | 1.00x |
| B `ref_relookup`（每字段重复下标链） | 0.793 | **1.42x** |
| C `ref_nooutput`（只算不输出） | 0.331 | 0.59x |
| D `ref_outonly`（只输出不算） | 0.327 | 0.59x |
| E `ref_prealloc`（不 append） | 0.598 | 1.07x |
| F `ref_tupleout`（tuple 行） | 0.585 | 1.05x |

结论：v1 最大的单项浪费是 **B「每字段重复下标链」(+42%)**，正是 §4.2 第 ① 条要消灭的东西。

### 3.4 Cython 天花板（`bench/cy_ceiling2.py`，N=20000，本次独立复跑，exit 0）

`correctness: all variants match python reference -> True`

| 变体 | µs/行 | 行/秒 |
|---|---|---|
| py row-object（7 字段） | 0.390 | 2,562,460 |
| cy row-object list rows | 0.416 | 2,404,655 |
| cy row-object tuple rows | 0.256 | 3,902,591 |
| cy row-object **NO output** | 0.115 | 8,685,079 |
| py typed columns | 0.642 | 1,558,154 |
| cy typed columns | 0.380 | 2,628,535 |

**独立复现的一条重要反直觉结论**：在「行对象」形状上 `cy list-rows`(0.416) **慢于**
`py`(0.390)——与 v1 冻结基线里 cy 相对 py 约 −18% 的结论**同向**。
→ 写进结论：**v2 不该盲信 Cython**；Cython 的收益来自「tuple 行 / 类型化列 / 去掉输出」，
不来自「加个 `cython=True`」。

## 4. CONTRACT §4.2 九条硬规则 —— 逐条判定

分析器：`bench/codegen_rules.py`（对**实际生成源码文本**做 AST + 文本判定，不读实现自述）。

| # | 规则 | v1（CAP/M0 真产物） | v2（`evidence/v2_codegen_report_current.txt`，`codegen sha1=A40457511277…`（W3），四组 A/B 全 9/9） |
|---|---|---|---|
| ① | 行首全局解包（同一下标链只取一次） | **PASS** | **PASS**（CAP: `_a=R[5]['a']; _a1=_a[1]; _a2=_a[2]`，每条前缀各一次） |
| ② | 零恒等赋值 / 零空桩 | **PASS** | **PASS**（无恒等赋值/空桩） |
| ③ | 零只用于 return 的中转 | **FAIL（4→3 处，见下）** | **PASS**（无多余中转；CAP assigns=4 / M0 assigns=5；`_in=_in['index']` 与字段别名按 Lead m01316 ② 属豁免） |
| ④ | 输出形状编译期固定 | **PASS** | **PASS**（`_o.append([...])` 编译期定形，无 `tuple(doc.items())`/`itertools.chain`） |
| ⑤ | 行级 try/except 移出循环 | **PASS** | **PASS**（循环内无 try） |
| ⑥ | 类型约束编译期分支（无 isinstance 链） | **PASS** | **PASS**（产物无 `isinstance(`；`int(`/`float(` 出现 **0** 次 —— type 是声明不是转换） |
| ⑦ | 只有 `optional: true` 才生成守卫 | **PASS**（守卫 0 处） | **PASS**（字段级判定：optional 字段有守卫、非 optional 无守卫；`CR.rule7_optional_guard_only` 6/6 schema 通过） |
| ⑧ | 变量名确定性（无 random） | **FAIL**（`cctpnrvgwn`） | **PASS**（两次 generate 字节相同，短名 `_a/_a1/_R5` 由声明顺序决定） |
| ⑨ | Cython 模式同源类型标注 | **FAIL**（未提供 cython 产物） | **PASS**（`cython=True` 产物 = 纯 Python 产物 + 每局部一行 `cdef object`，剥 cdef 后逐字节相同；pyximport 真编译后语义一致） |

> ⚠️ **方法学教训（本报告最重要的一条）**：在第 0:41 revision 上，v2 的**九条静态规则 + 字符上限曾经全绿**
> （CAP 176 / M0 210），而产物当时仍然**语义错误**（行被 `_o+=[...]` 摊平成扁平 list、optional 守卫反向
> 写成了 `grade=None if _t1 is not _U else _t1`）。→ **规则型静态检查单独不充分，行为 e2e 才是主判定**；
> 九条规则只能当「形态回归门」，不能当选「正确」的门。冻结证据：`evidence/v2_shapes_probe.txt`（
> 扁平 list 与 `grade=None` 的原文）、`evidence/v2_e2e_snapshot_d4b.txt`（30 项失败）。

v1 rule③ 的 4 处（可作 v2 的对照基线）：

1. 中转解包 `datas = datas['index']`（line 2）
2. 中转解包 `doc_cctpnrvgwn = buffer_cctpnrvgwn['doc']`（line 4）
3. append 中转别名 `doc_cctpnrvgwn_append = doc_cctpnrvgwn.append`（line 5）
4. 只被再次解包的容器 `buffer_cctpnrvgwn = {'doc': []}`（line 3）

即 v1 在 **③零冗余中转** 与 **⑧命名确定性** 上确实违规，v2 必须两条都比 v1 好。
分析器自检 6 项全 OK（rule1/rule2/rule3/rule8 正反例），见 `evidence/bench_codegen_v1.txt`。

### 4a. 规则 ⑥「只 compile 一次」的**测量口径**（本次修正，`bench/probe_compile_count.py`）

裸数 `builtins.compile()` 会把两种完全不同的东西混在一起：
`ast.parse()` / `compile(seg, …, "eval")` 是**反引号段的编译期校验**，不是产物编译。我最初的测法把
CAP[ext] 记成 5 次并被读成规则 ⑥ 违规；实测分解后：

| schema | 产物编译（源码含 `def run(`） | 段校验 |
|---|---|---|
| CAP[ext] / M0[ext]（有反引号段） | **1** | **2**（同一段；内联前为 4 —— parser-dev 已减半，仍属 build 期可省成本） |
| CAP/M0 v2-native（无反引号段） | **1** | 0 |

- 冷/热两次测量完全相同（5 = 1 + 4）→ **不是 import 噪声**，我原先的假设被自己的探针推翻。
- 规则 ⑥ 判定：**v2 产物 1 次 vs v1 的 2 次（空桩 + 真产物）→ PASS**。
- 附带可省成本：反引号段被重复校验 4 次（`ast.py:54` → 调用方 `analyze_py_segment` 一族），
  compile 期成本，不影响运行期；已报 parser-dev。
- 该口径已固化为测试 `TestCodegenRules::test_product_compiled_exactly_once`。

## 5. 独立验证发现（按发现顺序，附最小复现）

### 5.1 P0：`dcg/` 包遮蔽 `dcg.py`，v1 对照链路断裂

`dcg/__init__.py` 一旦存在，`import format` → `format.py:13 from dcg import ERROR, ...` 取到 v2 包 →
`ImportError: cannot import name 'ERROR' from 'dcg'`。这会打断 §7 的三方对照。
裁决 A′（Lead）：`bench/_v1bridge.py` 在 v1 导入期间把 `sys.modules["dcg"]` 钉到 `dcg.py`，
导入完立刻换回 v2 包 —— 同进程 v1/v2 共存。
**已自动化**：`tests/test_e2e_flight.py::TestFrozenV1Baseline::test_v1_and_v2_package_coexist`。

### 5.2 v1 运行期波动（+22%）→ 必须报最小值

同一台机、同一份代码：冻结 0.480 µs/行，本次 0.519–0.588 µs/行。
结论：**单次值不可作证据**，一律 min-of-3 + 波动区间。

### 5.3 计时顺序伪影（本次实测，方法学修正）

未加 settle sweep 时（`evidence/bench_row_v1side.txt` 旧版）：
手写参照第一轮 = **0.794** µs/行（min 0.529，波动 **50.2%**），而 `v1`（第二个被测）min 0.485
**看起来比手写参照还快**（0.92x）——这是纯粹的「第一个被测变体吃亏」伪影，不是代码差异。
加 2 轮 settle sweep 后：手写 0.472（波动 5.5%）、v1 0.519（9.2%），顺序伪影消失。
→ `bench_row.py` 已固化为「所有变体先 settle，再各自计时」。

### 5.4 v2 上限 0.457 µs/行 不可复现 → **已裁决：改用比值**（Lead m00414）

- 冻结「M0 手写下限 0.457」；本次手写参照实测 min **0.475**（早期轮 0.472；`bench/decompose.py` 变体 A 在 N=50000 给 0.558）。
- 即**任何一个手写参照现在都跑不到 0.457**。若把 0.457 当硬门槛，v2 很可能被判 FAIL 而非因为它慢。
- **Lead 裁决（采纳）**：主判定 = 同进程相对比值（`v2 ≤ 1.05× 当次 hand ref`，并同时报 `v2/v1`）；
  0.457 / 0.480 降为参考值，且**禁止跨会话绝对值相减**。`bench_row.py` 的 VERDICT 段已按此重写：
  先打印「当次实测 hand ref」与「v1/hand = 1.07x」，再给 `v2/hand` 的 PASS/FAIL 和 `v2/v1`。

### 5.6 并行 agent 污染 + 自适应 settle 到上限仍 >5%（本次新增，已如实报告）

- 本次测量时同机并行跑着 **parser-dev / jx-dev / lead + 我自己的进程**（4 个 python 进程），
  波动因此从 5.5%/9.2% 升到 **47.1%/44.8%**，且 3 次明细**单调递减**（0.698 → 0.571 → 0.475），
  典型的「进程还在被抢占/预热」形态，不是代码差异。
- 自适应 settle 已按 Lead 规则启动（2 → 5 轮），**到上限 5 轮后波动仍 >5%**：
  `bench_row.py` 打印 `settle_rounds=5  <-- 追加 settle 至上限仍 >5% (已如实报告)`，
  **不隐藏也不伪造**。证据里同时冻结测量时刻的 python 进程表（`machine load` 头）。
- 结论：这类噪声下**只有比值可用**（见 §3.2 跨轮对比），绝对 µs/行 只做定位；
  最终验收建议在机器安静时（其他 agent 停写后）重跑一次 §3.2 留档。


### 5.5 pytest 门禁陷阱（已留痕 `evidence/pytest_gate_trap/output.txt`）

模块级 `pytest.skip(..., allow_module_level=True)` 在 pytest 9.1.1 下产生 **0 个 item** →
**退出码 5**（NO_TESTS_COLLECTED），且 stdout 为空，与「测试文件损坏/语法错」不可区分。
改为 `pytestmark = pytest.mark.skipif(...)` → 测试被收集并报为 skipped，**退出码 0**。
本仓库 `tests/test_e2e_flight.py` 已按后者实现（`requires_v2`）。

### 5.7 阻断性缺陷（已被 parser-dev 修复，逐条复核通过）—— 留档原因：它们证明静态门不够

在 **0:41 revision**（`dcg/codegen.py` sha1 `BDF7B10C…`）上实测，冻结在 `evidence/v2_shapes_probe.txt`：

| 代号 | 症状 | 最小复现 | 现状 |
|---|---|---|---|
| D-A | `shape=list` 的行被**摊平**（`_o+=[...]` 是 extend 不是 append） | FLIGHT 3 行 → `[10,7,None,'flight',11,3,None,'flight',12,0,None,'flight']`（12 个标量），期望 `[[10,7,'A','flight'],…]`；连带 `shape=dict` 生成 `{k:[r[i]for r in _o]…}`（把标量当行）、`shape=index` 生成 `{i:r for i,r in enumerate(_o)}` | **已修**：现为 `_o.append([...])`，四形状语义测试全 PASS |
| D-B | `optional` 守卫**方向反了** | `grade=None if _t1 is not _U else _t1` → 有值(95)也产出 `None`，缺值反而保留 | **已修**（`tests/test_sentinel_integrity.py::test_present_zero_in_optional_field_is_kept` 转绿） |
| D-C | 数值 `0` 被当哨兵嗅探 | `$substring(s,0,0)` 显式长度 0 → 返回剩余全文 | **已修**（`test_explicit_length_zero_is_not_the_default` PASS） |
| D9 | `optional: true` 字段被 **generate() 整个丢弃** | 6 例矩阵：expr/value/const/missing + optional 四种形态产物都只 `return(a,)` | **已修** |

结论：这四条里有三条在**九条静态规则 + 字符数全绿**的状态下存在（见 §4 警示框）。

### 5.8 runtime 的 HELPERS / SOURCE 双实现漂移 1942 处（`bench/probe_helper_drift.py`）

`dcg/runtime.py` 同时维护 `HELPERS`（:471，真实 callable，**当前产物跑这套**）与 `SOURCE`（:499，
同名函数的源码字符串）。`runtime.py:5` 称 SOURCE 是「产物 helper 的真相源」，`runtime.py:498` 与
`codegen.py:60` 都声称 `tests/test_codegen_snapshot.py` 做差分对拍防漂移 —— **该文件不存在**
（`Test-Path tests/test_codegen_snapshot.py` → False），守卫是空的。

对拍结果（26 个取值 × 1/2 元 = 16094 组，`dcg/runtime.py` sha1 `D38AFA67…`，证据 `evidence/v2_helper_drift.txt`）：

| 类别 | 函数 | 不一致组数 | 性质 |
|---|---|---|---|
| **真语义差** | `_cn`（`$contains`） | 1 | `_cn({'a':1},'a')`：HELPERS `False` vs SOURCE `True` |
| 真语义差 | `_in` / `_ct` / `_jn` / `_ix_get` | 1 / 2 / 28 / **368** | 同输入不同结果 |
| **NameError** | `_fl` / `_ce` / `_sq` | 11 / 11 / 8 | SOURCE 版引用未定义 `_m`（`SOURCE_NEEDS_MATH` 只声明依赖没提供名字） |
| 未定义名 | `SOURCE['_N']` | — | `type(v) in _NNN`（**runtime.py:733**），`_NNN` 全仓无定义（疑为 `_NN` 笔误） |
| 仅报错文案 | `_ad` / `_ml` / `_sb` | 各 504 | HELPERS `TypeError('$add: 操作数必须是数值')` vs SOURCE `TypeError('$add')` |

影响面：产物 `from dcg.runtime import *` → 跑 HELPERS，所以**产物侧无此缺陷**；但
`evidence/accept.py:454-462` 是 `g={}` 后 `setdefault(runtime.SOURCE)` → 一旦产物改回「内联 helper」
形态，**验收门测的就是 SOURCE 而不是产物**（与我早先报的哨兵 gate 结构性失灵同源，§5.9）。
建议：`SOURCE = {n: inspect.getsource(f) for n, f in HELPERS.items()}` 由 HELPERS 反推（顶层补
`_NN = (int, float)`、处理 `_m`），或删除 SOURCE 与 `codegen.py:98` 的 `set(SOURCE)`。
已上报 Lead（待裁决）与 parser-dev。

### 5.9 Lead 的 `evidence/accept.py` 哨兵 gate 曾有两处结构性失灵（已报 Lead，未改其文件）

- 静态半边（`accept.py:485`）在 `_body(schema)` 上扫 `^\s*(_U|UNDEF)\s*=`，而 `_body()`（`accept.py:268-277`）
  只返回 `def run(...)` 之后的部分；哨兵绑定位于共享前导段 → **结构上不可能扫到**（实测 `_U=0` 位置 = -1）。
- 行为半边（`accept.py:500`）喂 **dict 行** `[{'a':0,'b':5,'i':1},…]`，而 CAP 表达式是 `$.5.a.1` → 产物做 `R[5]`
  → **`KeyError: 5`**，它想要的 `a+b -> [5,5]` 断言从未执行；`accept.py` 自己有正确布局常量 `ROW`（:94）却没用。
- 我用 `bench/run_accept_gate.py sentinel` 单跑复现：旧 revision `[FAIL] … KeyError: 5`；**当前 revision 已 `[PASS]
  sentinel integrity: undefined is a distinct object`**。
- 我自己的 `test_binding_is_not_a_literal` 有**同一个盲点**（曾要求产物出现 `_U = …` 绑定行），已修正为
  「不得绑假值字面量 + 必须能从 runtime 拿到哨兵 + runtime 哨兵与 0/None/False/"" 不同一」。

### 5.10 性能承诺（≤1.05× 手写参照）与 §4.4 helper 设计冲突 —— **已由 CONTRACT §4.3.1 内联解决**

**当时的数据（内联前，`evidence/v2_helper_overhead.txt`，CAP 负载，基线形态）**：

| 变体 | µs/行 | vs inline | 说明 |
|---|---|---|---|
| `inline`（内建 `str`/`+`） | 0.453 | 1.00x | 手写上界 |
| `helpers`（runtime `_S`/`_ad`） | 0.878 | **1.94x** | 契约 §4.4 强制的专用 helper（每次调用做 `is _U` + `_num`/`type(v) in _NN`） |
| `product`（真实产物） | 0.897 | **1.98x** | codegen 结构开销仅 **1.02x** |

即差距来自 helper 调用本身，M0 上 v2/hand = **3.26x**（§3.2b 历史行）→ 我判定「现有 helper 设计下 ≤1.05× 不可达」，
建议按「type 是声明不是转换」在**编译期可证安全**时内联。

**Lead 裁决（m01114 ① / m01316 ③）**：走内联，依据 CONTRACT §4.3.1；性能承诺不动。

**内联后复测（同一探针，`evidence/v2_helper_overhead_inlined.txt`，revision `FE989B54…`）**：

| 变体 | µs/行 | vs inline |
|---|---|---|
| `inline` | 0.464 | 1.00x |
| `helpers`（仅 `type=any` / 不可证时保留） | 1.274 | **2.75x** |
| `product`（真实产物） | **0.484** | **1.04x** |

→ 冲突消解：产物与最优手写只差 **1.04x**，且 bench_row 四轮 `v2/hand = 0.75–1.05x`（全部 ≤1.05x 门内）。
`helpers` 仍慢是**设计使然**——它只服务于 `type="any"` / 静态不可证的字段（§4.3.1），不是热点路径。

### 5.11 仓库根临时文件残留（**归因已更正**）

- **更正（Lead m01557 ①，parser-dev 举证）**：`_cy_ceiling2.pyx` **不是** parser-dev 的残留，而是
  `bench/cy_ceiling2.py:75` 的运行期产物（`:77` `import _cy_ceiling2` 使用，`:124-134` 跑它的
  `run_list/run_tuple/run_noout/run_typed`）。按写入域它归 **task-3**：我已把生成与清理一起写进脚本
  —— `bench/cy_ceiling2.py` 现在把 `.pyx` 与 Cython build dir 全部放进 `tempfile.mkdtemp(prefix="cy_ceiling2_")`，
  并 `atexit` 清理（`DCG_KEEP_CY=1` 可保留）。**该 .pyx 与 `bench/cy_ceiling2.py` 同生共死**。
  实测：`python bench/cy_ceiling2.py 20000` → exit 0、`correctness: all variants match python reference -> True`、
  根目录零残留。
- 早先列出的 `_p2.py _p3.py _p4.py _p5.py _tmp_*.py _tmp_*.txt` 现已不在仓库根（parser-dev 已清理）；
  当前根目录只剩 `.gitignore CONTRACT.md dcg.py DESIGN.md format.py jsonata_poc.py PLAN_v2.md pyproject.toml`。

### 5.12 `cython=True` 路径：语义独立验证一致；内联后收益从 ~4% 升到 ~23%，但仍**不是 §7 的 typed-column 档**

- 产物 = **纯 Python 产物 + 每个局部变量一行 `cdef object`**（结构断言：剥掉 cdef 行后与纯 Python
  产物逐字节相同；`bench/probe_cython_diff.py` 曾用它发现我自己的一个尾换行比较 bug，属工具缺陷非产物缺陷）。
- 语义：用 pyximport **真编译**后与纯 Python 产物/手写参照逐行一致（`bench_row.py` 的
  `load_cython_product()`；编译+导入 6.61 s）→ §4.2 第 ⑨ 条**行为级 PASS**（此前只有静态 `cdef` 存在性判定）。
- 计时（M0 8 字段，PROVISIONAL）：内联前 cython 0.858 vs 纯 Python 0.897 = **仅快 ~4%**；
  **内联后 cython 0.455 vs 纯 Python 0.593 = 快 ~23%**（0.80x hand ref / 0.76x v1，波动 4.2%）。
  原因：内联消掉了 Python helper 热点后，`cdef object` 局部变量对循环内赋值的收益才显出来。
- **但** `cdef object` 仍只是「声明成 Python 对象」，没有 C 类型；距 §7 的 Cython row-object 0.419 µs/行
  以及 typed-column 0.284 的目标仍有距离（此次 0.455 已达 0.419 档，但波动与并发负载下不算终值）。
- 定位（Lead m01316 ⑤ 采纳）：**同源、语义不变、收益有限**；拿满 C 类型收益需**类型化列输入**路径，
  属可选第二产物，**本次不实现**。cython 产物**不计入** CAP/M0 字符预算（预算只算纯 Python 产物）。
- 对照：`bench/cy_ceiling2.py` 手写 cy row-object 0.416 / cy tuple 0.256 / cy NO output 0.115、
  cy typed columns 0.380 → 收益来自「少构造对象 + C 类型」，不是 `cdef` 这个关键字本身。

### 5.13 验证方自我更正（Lead m01316 ④要求留档：这是报告可信度的一部分）

按发现顺序，**每条都是我自己判定器/测试的错**，不是实现缺陷：

| # | 我原先的做法 | 为什么错 | 更正 |
|---|---|---|---|
| 1 | `test_binding_is_not_a_literal` 要求产物里出现 `_U=` 字面量绑定 | parser-dev 已改成 `from dcg.runtime import *`（更正确的做法），我的断言把**正确实现判成红** | 改为行为断言（0 不被当 undefined），并留档 |
| 2 | rule① 只比较「最长下标链」是否重复出现 | `r[5]['a'][1]` 与 `r[5]['a'][2]` 文本不同 → **漏判** v1 的重复查找 | 改为按**所有下标前缀**计数（契约原文是「前缀只取一次」） |
| 3 | rule⑦ 用「整模块 `is _U` 出现次数」与 optional 字段数比较 | runtime helper 自带的 `if a is _U or b is _U` 预检把计数喂饱 → **静默丢弃 optional 字段（D9）被误判 PASS** | 改字段级判定（optional 字段必须有产出行 + 守卫；非 optional 不得有守卫） |
| 4 | rule③ 把 `_in=_in['index']` 与反引号别名（`hour=_a`）都算中转 | 它们不在**输出构造路径**上（Lead 裁决：输入适配 + 字段别名物化） | 只对「局部字面量容器被再解包」计违规；v1 由 4 处 → 3 处，v2 A 版 M0 由 1 处 → 0 处 |
| 5 | rule⑥ 直接把裸 `compile()` 计数当产物编译数（记到 5 次）→ 我猜是懒加载 import 噪声 | 假设被**我自己的探针推翻**（冷/热都 5 次） | 分解为 **产物 1 次 + 反引号段校验 4 次**；判定 PASS（v1 是 2 次产物编译） |
| 6 | §3.7「反引号与 JSONata 路径产出必须逐字节相同」 | 唯一差异是冗余括号 `(_a1)` vs `_a1`，契约要的是**共用实现**不是文本相等 | 只归一「包住单个标识符的冗余括号」后再比 |
| 7 | FLIGHT 夹具原先只有 `a=0,b=0` | `0+0=0` 恰好掩盖了 P0-1（0 被当 undefined） | 增加 `a=0,b=5` 行 |
| 8 | `test_inline_for_statically_provable_typed_fields` 断言具体拼写 `hourA+hourB` | codegen 正按「别名能否消除」迭代 → 造出**只在命名变化时变红的假失败** | 改为断言形态（无 helper、无守卫、有裸 `+`） |
| 9 | `bench_row.py` 的 cython 变体：①用 CPython `compile()` 装 cython 产物 ②对 `input.source="index"` 的 schema 直接传 `run(rows)` ③`strip-cdef == pure` 漏了尾换行 | ①`cdef` **不是**合法 Python，报 `SyntaxError` 是预期，但被我打成「不可用」像产物缺陷；②M0 的调用约定是 `run({"index": rows})`，报 `TypeError: list indices must be integers`；③纯字符串比较把尾换行算成差异，误报「结构断言失败」 | 改为 pyximport 真编译 + `W.v2_input()` + `rstrip("\n")`；失败原因如实写进 `[SKIP]` 行（见 `bench/probe_cython_diff.py`） |
| 10 | 两条 `xfail(strict=False)` 长期挂着（M0[ext] 字符预算 / `_ix_get` 内联） | 内联落地后它们**全部 XPASS**，而 `strict=False` 让 XPASS 静默通过 —— 等于用「可能失败」的标签掩盖已经达标 | 按 Lead m01316 ① 转成真断言并**删除 xfail**（`test_char_budget_m0_v1_faithful_basis`、`test_inline_leaves_no_helper_for_statically_provable_index`） |
| 11 | `TestEfficiencyHarness::test_refuses_frozen_without_window` 用 `subprocess.run(..., text=True)` 读子进程 UTF-8 中文输出 | 父进程按系统 locale（中文 Windows = GBK）解码 → `UnicodeDecodeError` → `p.stdout=None` → `None + str` 崩；**被 jx-dev 跑全量时抓到**（Lead m01706）——我的文件挡住了全绿 | 加 `encoding="utf-8", errors="replace"` + `(p.stdout or "")`；并顺手把 `bench/row_v2.py`、`bench/quiet_window.py`、`bench/run_verify.py` 里读 `tasklist`（GBK）的同类隐患一起加了 `errors="replace"` |
| 12 | `bench/row_v2.py` 直接对 CPU 计时值做除法（A/A 的 `slot2/slot1`、逐轮比值） | n/k 太小时单次测量落进 Windows ~15.6 ms `process_time` 粒度 → 计时被量化成 `0.0` 或 1 tick，实测报出 `ZeroDivisionError: float division by zero`（`row_v2.py:212`），且同一 callable 的 A/A 会显示成 2.0/0.0 的**纯量化伪影** | 新增 `_resolution_failures(res)` + 所有除法加零守卫；任何计时值 ≤0 时**exit 3 拒绝出数**并打印 `TIMER RESOLUTION TOO COARSE ... 提高 --k 或 --n`（实测 `--n 5000 --k 2` → exit 3；`--n 500 --k 1` 作为永久回归测试）。**教训：测量工具的「分母」也必须先证明它有分辨率。** |

> 教训一句话：**过严的测试和过松的测试一样有害** —— 过松会放过真缺陷（#3），过严会把正确实现判红并掩盖它（#1/#6/#8）。

**关于 Lead 提的可选建议 `_SRC = """<本产物源码>"""`（产物自证来源）——我的建议是：不做，理由留档：**
1. 契约 §4.1 的溯源要求（记 `schema_sha1`）已由 `build()` 的 manifest 满足：实测 manifest keys =
   `['artifact', 'chars', 'cython', 'lines', 'name', 'schema_sha1']`，`schema_sha1` 在。
2. 把源码自身作为字符串常量塞进产物会让「字符数」这一代理指标多出 ≈`len(src)` 的**非代码**字符；
   A 组预算（对 v1 的 445/537）历史上是**纯代码**口径，这会污染同一张表，且必须额外引入
   「哪些字符算预算」的复杂规则。
3. 产物自证来源的需求可以由 `build()` 写在 manifest/文件名里满足，不必改产物形态。
   若 Lead 仍要求做，请同时裁决「预算是否排除 `_SRC` 行」；我这边可以选择在 `chars_nodoc`
   之外再报一个 `chars_code`（去掉字符串常量行）——但这是**额外口径**，不是当前基线。

### 5.14 独立复核 CONTRACT §8 已知落差（Lead m02234/m02652，revision `a40457511277…`）

命令与冻结输出：`python -X utf8 bench/probe_contract8_gaps.py` → `evidence/v2_contract8_gaps.txt`；
`bench/probe_schema_error_paths.py` → `evidence/v2_schema_error_paths.txt`；
`bench/probe_deferred_errors.py` → `evidence/v2_deferred_errors.txt`。

**（a）O-1b（`expr` 侧 `$..a`）—— 我的初报有一处口径错误，已更正。** 我第一版探针只打印 `exc.msg`，
于是看到「`path=None`、无列号」；**补测 `str(exc)` 后两者其实一致**：

```
dcg.compile_expr('$..a') -> Unsupported  str='第 2 列: 区间 a..b 不可逐行编译（需要序列语义）'  msg='需要序列语义'
dcg.generate(expr='$..a') -> Unsupported  str='第 2 列: 区间 a..b 不可逐行编译（需要序列语义）'  msg='需要序列语义'
dcg.validate(schema)      -> NO ERROR（schema 被放行；拒绝延迟到 generate/compile）
```
→ 因此 O-1b 的偏离是两点：**(i) `validate()` 放行；(ii) 消息把 `$..a` 解释成源文本里并不存在的区间 `a..b`
（读者会以为是自己写错区间）**，而**不是**「无列号」。—— **「`$..a` 被静默当成 `R['a']`」在 `expr` 入口不成立**，
真正的静默错值在 `value` 入口（见 §8 的 O-1）。

**（b）深路径 `$.a.b` / `$.a.b.c` —— 归入已文档化能力 O-4（Lead m02699 裁定），但要补崩溃面。**
契约 §6 只支持单级 `$.name` 与数字下标，但 `$.a.b` / `$.a.b.c` **validate 与 generate 都放行**，产物是嵌套下标：

```
expr='$.a.b'    -> _o.append([R['a']['b']])     (72 chars)
expr='$.a.b.c'  -> _o.append([R['a']['b']['c']]) (77 chars)
run([{'a':{'b':{'c':11}}}, {'a':7}]) -> TypeError: 'int' object is not subscriptable
```
→ 数据恰好嵌套时**给出值**，数据平铺时**运行期崩**；对照单级 `$.a` 正常（`[[{'b': {'c': 11}}], [7]]`）。

**（c）O-2 —— 完全复现。** `post` / `trace` 被接受且产物**零代码**：

```
bytes(post) == bytes(no post)    : True
bytes(trace=True) == bytes(no t) : True
post=123 -> SchemaError path='output.post' msg='必须是 "pkg.mod.fn" 形式的字符串，得到 123'
```
但 `post` 的实际校验只有「str 或 None」：`'$.a'`、`'$.a + 1'`、`` '`$.a`' ``、`'$sum($.a)'`、`None` **全部放行且产物相同**
→ **错误消息宣称的 `pkg.mod.fn` 形式校验并不存在**。

**（d）`SchemaError.path` 精度 —— Lead 的 `g_schema_errors` 门已覆盖；verifier 收到该裁定前未完成，
随后按真实形状（顶层 `out.fields`，而非 Lead 指令里的 `output.fields`）修好探针补做完成，结论一致，并额外发现 5 例放行：**

| 用例 | `validate()` | `generate()` |
|---|---|---|
| `missing type` / `two keys` | `SchemaError path='out.fields.a'` | — |
| `bad type name` | `SchemaError path='out.fields.a.type'` | — |
| 未声明裸名（items 互斥） | `SchemaError path='out.fields.v.fields.a'` | — |
| `single + dict` | `SchemaError path='output.single'` | — |
| 深路径 `$.a.b` / `$.a.b.c` | **放行** | **放行**（= O-1b） |
| `$sum(...)` 整批函数 | 放行 | `Unsupported`（延迟报错，可接受） |
| 反引号语法错 `` `1 +` `` | 放行 | `Unsupported`（延迟报错，可接受） |
| `list` 缺 `items` | **放行** | **放行**（契约要求必填） |
| `object` 缺 `fields` | **放行** | **放行**（契约要求必填） |

→ 结论：**错误对象一旦抛出，`path` 都是字段级的**（与 Lead 门一致）；但 `list.items` / `object.fields`
缺失属于「契约要求必填却静默放行」，建议一并登记。

## 6. v2 待测项 —— 现状（原「待补」表，2026-10-17 复核后逐项转绿）

| # | 项 | 状态 |
|---|---|---|
| 1 | 端到端 flight（shape=list，含 filter、optional 守卫、嵌套） | **PASS**（`evidence/v2_e2e_snapshot_d4d.txt`） |
| 2 | 输出形状裁决：list / **dict 列式** / index(int 键) / single 展平；`single+dict/index` 报 SchemaError | **PASS** |
| 3 | §4.2 九条规则 + 字符上限（CAP≤180 / M0≤210） | 规则 **9/9 PASS**（四组 A/B）；字符：CAP[ext] **157 PASS**、CAP[native] 149 PASS、**M0[ext] 195 PASS**（内联前 283 FAIL，xfail 已删）、M0[native] 177 PASS —— 见 §2 |
| 4 | `type` 是声明不是转换：产物中**不得**出现 `int(` | **PASS**（出现 0 次） |
| 5 | 确定性：两次 `generate()` 字节相同；`gen_name` 无 random | **PASS**（字节相同，短名可读） |
| 6 | 每次 build 只 `compile()` 一次（v1 是两次） | **PASS**（产物编译 generate 1 次、build 1 次，口径见 §4a；v1 = 2 次） |
| 7 | v1↔v2 **逐行逐字段比「值」**（形状是有意变更，不比容器） | **PASS**（M0/CAP 逐位置相等） |
| 8 | CONTRACT §3.6 动态下标：取行内元素 + IR 无 `$ix` 节点 | **PASS**（7 项，含「未声明裸名 build 期 SchemaError」） |
| 9 | CONTRACT §3.7 反引号数字段：`` `str($.5.a.1) + str(hourB)` `` 端到端 == v1 `ext` | **PASS** |
| 10 | §3.7.3 反引号链与 JSONata 路径**共用同一实现** | **PASS**（去注释 + 归一 `def <name>` 后逐行相同） |
| 11 | 三方性能对照（手写 / v1 / v2 / v2-cython）+ 构建耗时，**以比值判定** | **PASS（终值，可引用，`RESOLVED`）**：W3 CPU 口径 `v2/hand=0.985`、`v2/v1=0.957`（门 ≤1.05x，margin 0.0646 ≫ noise floor 0.0142），三轮全 PASS，cython 0.859 µs/行不计预算 —— 见 §3.2e-final（`evidence/row_v2.json`，锚 `A40457511277`） |
| 12 | 先跑 **Lead 重写后的 `evidence/accept.py`**，输出原文回报 Lead | **PASS**：Lead 侧 `evidence/accept.py` = **16 PASS / 0 FAIL / 0 SKIP（exit 0）**，冻结 `evidence/accept_final.txt`；其 §5 效率门读到我的 JSON：`v2_over_hand=0.9854 <= 1.05, confidence=RESOLVED, noise_floor=0.0142, k=8, reps=7` |
| 13 | CONTRACT §8 已知落差（O-1/O-2）独立复现 | **完成（Lead m02699 已按我的结论改写 CONTRACT §8）**：O-2 完全复现并细化（只做 str-or-None，消息夸大）；**`expr` 侧 `$..a` 的「静默当成 `R['a']`」不成立**（`compile_expr` 与 `generate` 都抛 `Unsupported`，`str(exc)` 均带 `第 2 列`），故降为 **O-1b（🟡 可诊断性：`validate()` 放行 + 消息虚构区间 `a..b`）**；**真正的静默错值在 `value` 侧 = O-1（🔴，我独立复现 Lead 的 4 例）**；深路径归 **O-4**（已文档化能力 + 平铺数据崩溃面）；`validate` 放行 `list.items` / `object.fields` 缺失记为 **O-7（🟠）** —— 见 §5.14 与 §8 |

## 7. 证据索引（`evidence/`）与一键闸门

一键闸门：`python -X utf8 bench/run_verify.py [--fast]`
（顺序：**先跑 Lead 的 `evidence/accept.py` 并把原文回报 Lead** → 我的 e2e → jx conformance →
codegen 复核 → 三方计时；v2 未就绪时在第 0 步**直接停止且不写任何 v2 数字**，已实测 exit=4）。
原 `bench/run_verify.ps1` 已删除：本机脚本执行策略拒绝未签名 `.ps1`（`PSSecurityException`），
Python 版无需改策略且保持 UTF-8。

| 文件 | 内容 |
|---|---|
| `evidence/bench_row_v2side_inlined.txt` | **内联后** bench_row 原始输出（run 前/后 sha1 + stable=YES + 进程表；v2 纯 Python / v2 cython 两个 PASS） |
| `evidence/v2_codegen_report_inlined.txt` | **内联后**四组 A/B 字符数 + 九条规则 9/9 + compile 口径 + run 前/后 sha1 |
| `evidence/v2_helper_overhead_inlined.txt` | 内联后归因：inline 0.464 / helpers 1.274 / **product 0.484（1.04x）** |
| `evidence/quiet_window_<ts>.txt` | 静默窗口终值采集（`bench/quiet_window.py --window`；缺 `--window` 时 exit 3 拒绝采集） |
| `evidence/bench_row_v1side.txt` | 本次 bench_row 原始输出（手写参照 + v1，min-of-3 + 波动 + settle 轮次） |
| `evidence/v1_capture_src.txt` | v1 生成器真产物源码 + CAP/M0 字符数 + `compile()` 次数 |
| `evidence/bench_codegen_v1.txt` | 分析器自检 6 项 + v1 CAP/M0 九条规则判定 |
| `evidence/pytest_e2e_v1side.txt` | `tests/test_e2e_flight.py` 当前运行结果（4 pass / 其余 SKIP） |
| `evidence/pytest_gate_trap/output.txt` | pytest 退出码 5 陷阱的最小复现（改造前/后对照） |
| `evidence/accept.py` | **Lead 侧**的验收门，非本报告产出，只读不改（我重写它之前已确认为 Lead 所有） |
| `evidence/accept_by_verifier.txt` | v2 可用后，我运行 Lead 那道门的**原文冻结**（回报给 Lead 的证据） |
| `evidence/v2_e2e_snapshot_d4d.txt` | **当前 revision** 的 e2e + 哨兵套件结果（81 passed / 0 failed，含 codegen sha1） |
| `evidence/v2_e2e_snapshot_d4b.txt` | 0:41 revision 的失败诊断快照（30 项失败，仅诊断，非验收） |
| `evidence/v2_shapes_probe.txt` | 四形状 + single + filter 的产物/实得值速查（含 D-A 扁平 list、D-B `grade=None` 原文） |
| `evidence/v2_codegen_report_current.txt` | 当前 v2 的九条规则判定 + 字符数 + compile() 次数 + build manifest（**已按 W3 `codegen sha1=A40457511277…` 重锚**，含时间戳/三份 sha1/run 后 sha1；CAP[A]=157 / M0[A]=195 / CAP[B]=149 / M0[B]=177，四组全 9/9） |
| `evidence/row_v2_w3_stdout.txt` | **W3 终值完整 stdout**（CPU 口径：K=8、7 reps、3 轮 + A/A 噪声底 3 block + 配对比值 + `EFFICIENCY_RATIO` 行），`evidence/row_v2.json` 的同源原文 |
| `evidence/pytest_full_suite.txt` | **全量 `pytest tests/`** 结果（含时间戳 + sha1；W3 revision 上为 717 passed / 0 failed / exit 0） |
| `evidence/v2_helper_drift.txt` | `HELPERS` vs `SOURCE` 差分对拍（16094 组，1942 处漂移） |
| `evidence/v2_helper_overhead.txt` | v2 慢在哪：inline 0.453 / helpers 0.878 / product 0.897 µs/row（PROVISIONAL） |
| `evidence/v2_selfcontained_probe.txt` | 产物自足性：空 globals `exec` + `run()`；`runtime.__all__` 56 项含 `_U/_ad/_eq/_S/_up/_ix_get` |
| `evidence/bench_row_v2side.txt` | v2 三方对照原始输出（PROVISIONAL，含 python 进程表 + 时间戳） |
| `evidence/v2_accept_gate_gap.txt` | `accept.py` 哨兵 gate 结构性失灵的最小复现 |
| `evidence/v2_cython_path_probe.txt` | `cython=True` 产物源码 + pyximport 真编译 + 语义一致性 + 计时 |
| `evidence/v2_final_check.txt` | 当前 revision 的合并复核（81 passed / 0 failed + accept 哨兵 gate PASS，前后 sha1 相同） |
| `evidence/v2_pytest_current.txt` | 最新套件结果：**717 passed / 0 failed / exit 0**（W3 revision；两条旧 xfail 已 XPASS → 转真断言并删除，见 §5.13 #10；harness 测试改用可解析的 n=50000/K=8 配置并新增 coarse-timer 拒绝测试） |
| `evidence/pytest_final_t3.txt` | **task-3 收口时的最终全量套件证据**：时间戳 + 7 文件 sha1 + `717 passed in 5.22s` + `exit code: 0`（注：`pyproject.toml` 的 `addopts="-q"` 若再叠加命令行 `-q` 会变 `-qq`、**摘要行被抑制**，故本文件用 `pytest tests/` 裸跑以保留摘要） |
| `bench/probe_contract8_gaps.py` + `evidence/v2_contract8_gaps.txt` | CONTRACT §8 的 O-1b/O-2 独立复现：`expr='$..a'` 在 `compile_expr`/`generate` 均抛 `Unsupported`（`str(exc)`='第 2 列: 区间 a..b 不可逐行编译（需要序列语义）'，**非**静默错值），但 `validate()` 放行；`post`/`trace` 产物字节相同（True/True）、`post` 只做 str-or-None 校验（消息宣称 `pkg.mod.fn`） |
| `bench/probe_path_leniency.py` + `evidence/v2_path_leniency.txt` | **O-1（🔴 value 侧静默错值）独立复现**：`value="$..a"` → `_o.append([R['a']])`、`"$.a..b"` → `R['a']['b']`、`"$.a."`/`"$...a"` → `R['a']`，全部 validate+generate 放行；只有 `"$.*"` 被正确拒绝（`SchemaError path='out.fields.v.value'`）；`"$.5.a.1"` → `R[5]['a'][1]`（下标链，按 doc 支持） |
| `bench/probe_deferred_errors.py` + `evidence/v2_deferred_errors.txt` | **O-7（🟠 validate 非完整闸门）**：`list` 缺必填 `items`、`object` 缺必填 `fields` 在 validate 与 generate **都放行、永不报错**；`$sum(...)` / 反引号语法错则延迟到 generate 才抛（可接受） |
| `bench/probe_schema_error_paths.py` + `evidence/v2_schema_error_paths.txt` | `SchemaError.path` 精度独立复核（形状已修正为顶层 `out.fields`）：抛错时 path 均为字段级（`out.fields.a` / `out.fields.a.type` / `out.fields.v.fields.a` / `output.single`）；**并额外发现 O-7 的 5 例放行**（见上） |
| `bench/row_v2.py` | **效率门 harness**（`accept.py:698` 等待的文件）：`EFFICIENCY_RATIO` 单行 + `evidence/row_v2.json`；**双口径**（CPU 主 / wall 辅）+ A/A 噪声底 + 3 轮 worst-of + `confidence`/`margin_over_limit`/`noise_floor_blocks`。exit 码：0=PASS 且 `RESOLVED`、1=FAIL/`WITHIN_NOISE`/`INCONCLUSIVE`/revision 不稳、3=用法拒绝（含计时分辨率不足，**不写任何数字**）、4=v2 未就绪。`--frozen` 单跑即可（静默窗口已作废，Lead m01965 ⑦）；`--window` 只与 `--frozen` 搭配 |
| `evidence/row_v2.json` | `bench/row_v2.py` 的机器可读结论：`verdict=PASS` / `confidence=RESOLVED` / `ratios{v2_over_hand:0.9854, v2_over_v1:0.9568}` / `noise_floor=0.0142` / `noise_floor_blocks`（3 块 A/A 逐 rep 比值）/ `cpu_rounds_detail` / `margin_over_limit=0.0646` / `k=8` / `reps=7` / `window='CPU-2026-10-17T01:40:37+08:00'` / `sha1` 前后（7 文件）/ `others_python_processes=5` / `process_table` |
| `evidence/bench_codegen_v2_current.txt` | 四基准（CAP/M0 × ext/native）字符数 + 九条规则 + compile 口径 + cython 结构断言（含 revision 行） |
| `evidence/v2_m0_basis_probe.txt` | 「预算打架」根因：ext vs v2-native 两份 schema 的产物逐行 diff（205/293 → 174/283 → 149/177） |
| `evidence/v2_compile_count_probe.txt` | 规则 ⑥ 口径：冷/热均 = 1 产物编译 + 4 段校验；build 后热路径 = 1 + 0 |
| `evidence/row_v2_window_machine.txt` | **窗口机器负载侧路证据**（Lead m01832 选 (A) 方案）：PRE 17.1% / POST 8.5% 的 1 分钟 CPU 均值 + 完整 tasklist 原文 + 时间戳 + 锚定 sha1（不改 `.py`、不混进产物 JSON） |
| `evidence/row_v2_window_stdout.txt` | 窗口轮 `bench/row_v2.py --frozen --window W2-2026-10-17T01-12 --n 200000` 的完整 stdout（两轮变体表 + reps/波动/settle + VERDICT + `EFFICIENCY_RATIO` 行；UTF-16 由 PowerShell `*>` 写出） |
| `bench/frozen/baseline_v1.txt` | v1 冻结基线（Lead 侧，只读） |

## 8. 独立复核台账（CONTRACT §8 追加项，Lead m02699 全部采纳）

**供 Lead 原样引用的一句话总结：**

> 本轮独立复核新发现 2 条静默类缺陷（O-1 value 路径空段、O-7 validate 非完整闸门），已在 CONTRACT §8 登记、未修。

复核对象：冻结 revision `codegen A40457511277…`（44136 B）等 7 文件（sha1 表见 §3.2e-final）。
`CONTRACT.md` 属 Lead 所有、由 Lead 依上表改写，**本报告不把它的 sha1 记为锚点**（已按 m02699 要求）。

| 编号 | 入口 / 严重度 | 最小复现（本报告命令） | 实测 | 修复 |
|---|---|---|---|---|
| **O-1** | `value` 侧路径 / 🔴 静默错值 | `bench/probe_path_leniency.py` → `evidence/v2_path_leniency.txt` | `value="$..a"`(空段) → `_o.append([R['a']])`、`"$.a..b"` → `R['a']['b']`、`"$.a."`/`"$...a"` → `R['a']`；全部 validate+generate 放行，只有 `"$.*"` 被拒（`SchemaError path='out.fields.v.value'`） | 未修 |
| **O-1b** | `expr` 侧 `$..a` / 🟡 可诊断性 | `bench/probe_contract8_gaps.py`（补测 `str(exc)`） | `compile_expr` 与 `generate` **都抛** `Unsupported`，`str(exc)`='第 2 列: 区间 a..b 不可逐行编译（需要序列语义）'（**列号存在**；`.msg` 只有 '需要序列语义'）；偏离是 `validate()` 放行 + 消息虚构源文本里没有的区间 `a..b` | 未修 |
| **O-2** | `post` / `trace` / 🟠 未接线 | `bench/probe_contract8_gaps.py` | `bytes(post)==bytes(no post)` True、`bytes(trace)==bytes(no trace)` True；`post` 只做「str 或 None」，`'$.a'`/`'$.a + 1'`/`` '`$.a`' ``/`'$sum($.a)'` 全放行，仅 `123` 被拒，而消息宣称 `pkg.mod.fn` 形式 | 未修 |
| **O-4** | 深路径 `$.a.b.c` / 已文档化能力（+崩溃面） | `bench/probe_contract8_gaps.py`、§5.14(b) | 反引号深链是 README §3.3 声明的能力：嵌套数据 `run([{'a':{'b':{'c':11}}}])` 给出 `[[{'c':11}]]`；**数据平铺时 `run([{'a':7}])` → `TypeError: 'int' object is not subscriptable`**（generate 接受本身不是缺陷） | 未修（能力，文档需补崩溃面） |
| **O-7** | `validate()` 完整性 / 🟠 静默放行 | `bench/probe_deferred_errors.py`、`bench/probe_schema_error_paths.py` | `{"type":"list"}` 缺必填 `items`、`{"type":"object"}` 缺必填 `fields`：**validate 与 generate 都放行、永不报错**；`$sum(...)` / 反引号 `` `1 +` `` 由 generate 才抛（延迟报错，可接受） | 未修 |

**与原登记的三处差异（均由实测更正，Lead m02699 已采纳）：** ① `expr` 侧「`$..a` 被静默当成 `R['a']`」**不成立**，降为 O-1b；
② 真正的静默错值在 **`value` 侧**（O-1，4/5 畸形路径被静默编译到别处）；③ `$.a.b` 深链属**已文档化能力**（O-4），不是新缺陷，但必须补写平铺数据的崩溃面。

**[5]（`SchemaError.path` 精度）按 Lead 指定写法：** Lead 的 `g_schema_errors` 门已覆盖字段级 path 精度；verifier 在收到该裁定前未完成，
随后修好探针补做完成，结论一致，并额外发现 `list.items` / `object.fields` 缺失被静默放行。
（我初版探针把 schema 形状写成 `output.fields`，真实形状是顶层 `out.fields`；该形状来源是 Lead 的指令文本，已按被测包实际形状修正。）

**本报告未独立验证 / 无法独立验证的边界（诚实清单）：**
1. 上表所有 O-* **均未修改任何代码**：本轮 verifier 写域仅 `tests/`、`bench/`、`evidence/`，冻结 revision 保持 `A40457511277`；若修复，§2、§3.2e-final 的锚点与数字必须整体重采。
2. `CONTRACT.md` 的**原文**我未读取比对（Lead 所有）；上表依据的是 Lead m02699 的转述 + 我的实测，二者不一致处以我的实测为准并已回报。
3. `evidence/accept.py`（Lead 侧验收门）我只**运行并冻结输出**，未审其判据实现；其 §5 效率门读到我的 `evidence/row_v2.json`。
4. **绝对时间（µs/行）不可引用**：W3 三轮内 hand 从 1.3867 漂到 0.9082（−35%），跨会话漂 56%；本报告只主张**比值**（`v2/hand=0.9854`）。
5. `cython=True` 路径验证的是「语义一致 + 结构断言 + 收益」，**不是** CONTRACT §7 所说的 typed-column 档（现有路径仍走对象语义），该档未验证。
6. 计时分辨率与噪声底只在**本机 Windows / CPython 3.11 / 空载至 5 并发 python** 条件下测得；其它平台需重测 `noise_floor`。
7. `post` / `trace` 的**预期语义**只有 CONTRACT 文本、无参考实现（v1 也没有这两个入口），因此只能判定「未接线」，无法验证「接对」。
