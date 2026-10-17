# `bench_examples.py` — 六个 `format.py` 样例的 v1 / v2 对照基准

**状态：W3 的 CPU 口径 `--final` 已跑（终值 = `W3-2026-10-17T01-41`，revision `A40457511277`）。**
**§5 判决 = `MET (RESOLVED; all 3 blocks ≤ 1.05)`**，`x/hand` worst = **1.013**（margin 0.0372 ≥ A/A 噪声底 0.0127，§6.2.2）。
注意余量只有 1–4%、与噪声底同量级：同 revision 早 5 分钟的 `T01-36` 就是 `WITHIN_NOISE`（worst 1.026，**不是**不达标）。
wall-clock 口径**仍然**是 `INCONCLUSIVE (wall-clock has no 5% resolution)`（§6.2.1，W2 三次 Run A/B/C 的 1.117 / 0.901 / 0.937）。

Lead 已裁决（m00064 / m00451 / m01149）：可对外引用的**终值**由他开静默窗口后重跑认定；
W3 起**静默窗口要求作废**（CPU 口径免疫抢占），改为测量前后各取一次 sha1 并断言 `revision_stable_during_run`。
本文档记录：**通路已打通** + **三次 provisional 实测** + **三次 W2 `--final`** + **两次 W3 CPU 口径 `--final`**，
用来证明①方法学可执行、②wall-clock 绝对值此刻不可引用、③内联对性能形态的影响方向、
④§5 的 5% 阈值在 wall-clock 下无判定力、在 CPU 口径下**可以判定但余量极小**。

- 脚本：`bench/bench_examples.py`
- 终值原始输出：`evidence/examples/bench_examples_final_W3-2026-10-17T01-41_{raw.txt,json}`
  （同 tag 拒绝覆盖；`T01-36` 保留但最后一行按 `WITHIN_NOISE` 读，见 §6.2.2）
- provisional 原始输出：`evidence/examples/bench_examples_raw.txt`、`evidence/examples/bench_examples.json`
- 历史 W1/W2 产物（旧的不带 tag 命名，已不再被写）：`bench_examples_final_{raw.txt,json}`（= Run C）、
  `bench_examples_final_W1_untagged_{raw.txt,json}`（= Run A 留证）
- helper 开销归因：`evidence/examples/probe_helper_cost.py`
- 计时原语复用 `bench/_workload.py` 的 `bench_reps()`（未改动该文件）；
  §5 的 CPU 口径**必须**用 `bench/bench_row.py` 提供的 `measure_interleaved_cpu`（唯一实现，§6.1b）

---

## 1. 对照的是什么

| | v1 侧 | v2 侧 |
|---|---|---|
| 来源 | `format.py:577-816` 六例冻结的 `describe`（`evidence/examples/v1_examples_frozen.json`） | `tests/examples/v1_parity/schemas.py` 的同例重写 |
| 执行 | `bench/_v1bridge.py` → `Format.parse()` → handler | `dcg.generate()` → `exec` → `run()` |
| 输入 | 样例自带的 10 行载荷循环放大到 N 行（两侧**同一批对象**） | 同左 |

**v1 侧两个标志强制关掉，这是刻意的**：

- `CYTHON_MODE=False` —— `CYTHON_MODE=True` 是 v1 的**默认值，且对例2/3/4 是坏的**
  （`TypeError: Expected list, got int|tuple`；叠加 `TRYE_MODE=True` 就变成**静默丢数据**，
  见 `docs/examples_v1_to_v2.md` §4）。给坏路径计时不是给引擎计时。
- `TRYE_MODE=False` —— 去掉逐行 `try/except` 包装；v1 冻结基线 **0.480 µs/行**就是这个配置。

`FREE_MODE` 保留样例自己的取值（它改变 v1 生成的代码形态，统一化等于测一个没人用过的配置）。

## 2. 方法学（`bench/bench_compare.md` §7 + Lead 五条裁决）

- 同机、同数据、**同进程**；每轮计时前 `gc.collect()`；预热后**取最小值**。
- **两侧对称**：各 2 轮不计时 settle sweep，再 `reps=3` 计时。
- **自适应追加**：波动率 `(max-min)/min > 5%` 就再追加一轮，最多 12 轮；一律报
  **最小值 + 波动率 + 实际轮次**（裁决⑤）。
- **主判定 = 本进程内实测的 v1/v2 相对比值**，不做跨会话绝对数字相减/相比（裁决③）。`ratio > 1` = v2 更快。
- 每次测量都附**时间戳 + 当时的 python 进程表**（裁决①）。
- **为什么没有 v1 的 Cython 列**（不解释会被误读成"漏测"）：v1 的 `CYTHON_MODE=True` 是**另一条 codegen 路径** ——
  它按**字段名**推 `cdef` 类型标注，于是在纯 Python 路径下无害的"同名重复声明"会变成运行期
  `TypeError: Expected list, got int`（例2/3/4 实测，见 `docs/examples_v1_to_v2.md` §4），
  再被 `TRYE_MODE=True` 吞成**静默丢数据**。也就是说：**v1 的"加 Cython 就快"是以改变语义（且静默丢数据）为代价的**，
  而 **v2 的 Cython 只做类型标注、语义不变**（`CONTRACT.md` §4.2 规则⑨，实测前 2000 行逐行相等）。
  两者不是同一件事，**不可比**，因此本基准只给 v1 的纯 Python 路径（`CYTHON_MODE=False`）计时 ——
  这是**对 v1 的公平处理**，不是给它放水：Cython 那条路根本产不出正确产物，无法作为性能对照的基线。

## 3. 结果

### 3.1 第二次运行（内联进行中）：N = 200000，2026-10-17T00:51:03.241317+08:00

同机 **9 个 `python.exe`** 并发（`21648/11688/20484/6436/10568/1348/18720/4440/14208`，
除两个 192 K 的空闲进程外，其余 11.6 M–72.0 M 常驻）。

| 例 | 行数 | v1 µs/行 | v1 行/秒 | v1 min(s) | v1 波动 | v2 µs/行 | v2 行/秒 | v2 min(s) | v2 波动 | **v1/v2** |
|---|---|---|---|---|---|---|---|---|---|---|
| EX1 | 200000 | 0.747 | 1,338,383 | 0.1494 | 42.1% | 1.526 | 655,464 | 0.3051 | 39.4% | **0.490** |
| EX2 | 200000 | 0.800 | 1,249,695 | 0.1600 | 33.5% | 1.395 | 716,954 | 0.2790 | 45.5% | **0.574** |
| EX3 | 200000 | 0.142 | 7,024,346 | 0.0285 | 44.8% | 0.162 | 6,165,095 | 0.0324 | 3.8% | **0.878** |
| EX4 | 200000 | 0.166 | 6,030,490 | 0.0332 | 3.5% | 0.209 | 4,795,059 | 0.0417 | 88.9% | **0.795** |
| EX5 | 200000 | 0.500 | 2,000,014 | 0.1000 | 116.6% | **v2 N/A** | — | — | — | 不参与 |
| EX6 | — | **v1 N/A** | — | — | — | **v2 N/A** | — | — | — | 不参与 |

**加权总计（EX1–EX4，每例同为 200000 行，共 800000 行）**

| | µs/行 | 行/秒 | min 总时长 |
|---|---|---|---|
| v1 | 0.464 | 2,155,693 | 0.3711 s |
| v2 | 0.823 | 1,215,370 | 0.6582 s |
| **比值 v1/v2** | **0.564** | | |

### 3.2 第一次运行：N = 200000，2026-10-17T00:48:12.152701+08:00（6 个 `python.exe`）

| 例 | v1 µs/行 | v2 µs/行 | v1/v2 |
|---|---|---|---|
| EX1 | 0.520 | 2.156 | 0.241 |
| EX2 | 0.865 | 1.997 | 0.433 |
| EX3 | 0.185 | 0.219 | 0.847 |
| EX4 | 0.205 | 0.247 | 0.830 |
| EX5 | 0.532 | v2 N/A | — |
| **加权** | **0.444** | **1.155** | **0.384** |

### 3.3 第三次运行（**内联完成后**，仍是 provisional）：N = 200000，2026-10-17T01:03:21.971664+08:00（8 个 `python.exe`）

生成器 sha1（报告头部自动记录）：`codegen 3e0b2c6a25a188b79404ccaf4ffa97def7348d4b`、
`runtime cd760a6fba57b8144118a60520c8caf2a62848bb`、`schema f4ce36da9c9a127aee67568b320bf13db7f522ce`、
`jx/compiler 9c5b2c8128a0c5de4d8a2309544f4128e153ee8a`。

| 例 | v1 µs/行 | v2 µs/行 | v1/v2 |
|---|---|---|---|
| EX1 | 0.780 | 0.565 | **1.381** |
| EX2 | 0.898 | 1.100 | 0.816 |
| EX3 | 0.169 | 0.149 | **1.137** |
| EX4 | 0.149 | 0.161 | 0.927 |
| EX5 | 0.367 | v2 N/A | — |
| EX6 | v1 N/A | v2 N/A | — |
| **加权** | **0.499**（2,003,383 行/秒） | **0.494**（2,025,699 行/秒） | **1.011** |

产物：`evidence/examples/bench_examples_postinline_{raw.txt,json}`（`--tag postinline`，不覆盖前两次的证据）。

**这是三次运行里唯一一次 v2 与 v1 基本打平的**（0.494 vs 0.499 µs/行，比值 1.011）；
EX1 与 EX3 上 v2 **快于** v1。与前两次的差别不是机器变安静了 —— 本次同样有 8 个 `python.exe`、
EX1 的 v2 波动率 149.5% —— 而是**生成器真的变了**：内联让 v2 的 EX1 从「每行 3×`_ad` + 4×`_ix_get`」
变成「零 helper、零中转局部变量」（见 §4.3）。**数字仍是 provisional，绝对值不可引用**；
但"内联把 v2 从 ~0.38–0.56× 拉到 ~1.0×"这个**方向**在三次同进程测量里是一致的。

### 3.4 三次相隔 3 分钟的运行说明了什么

1. **v2 侧确实变了**：`dcg.generate` 在此期间从「所有算术都走 `dcg.runtime` helper」变成
   **完全编译期内联**（EX1 生成码 352 → 342 → 215 字符，`_ad(hourA,hourB)` → `(hourA+hourB)` → 裸 `_a1+_a2`）。
   v2 加权 1.155 → 0.823 → 0.494 µs/行。
2. **v1 侧也在动**：v1 加权 0.444 → 0.464 → 0.499，EX1 单例 0.520 → 0.747 → 0.780（+50%）。
   v1 引擎代码在这十几分钟内**没有任何改动** —— 所以这个变化纯粹是**并发抢占**。
3. **因此本文档的绝对值一律不可引用**，只有 §2 的方法学和 §4 的归因是结论；
   **主判定只看同一次运行内的 v1/v2 比值**（裁决③）。

前两次运行的波动率全部 >5%（最高 EX5 116.6%），自适应追加**触顶 12 轮仍未收敛** —— 与并发一致。
第三次的 v1 侧波动率降到 2–5%（v1 侧趋稳），但 v2 侧仍有 27–149% 的波动。

### 3.5 `N/A` 的判定

- **EX5 → `v2 N/A`**：样例寻址 `$.data.ID`（两个符号段），`CONTRACT.md` §6① 明令不支持，
  v2 侧**写不出来**（实测 `Unsupported: 第 2 列: 深路径 [data,ID] 不可逐行编译`，见下 §4.4）。
  保留在表里是为了让迁移缺口可见，并且**不参与任何比值**。
- **EX6 → 两侧 `N/A`**：v1 的 index 模板 `return index_{$code}, [doc for doc in chain(...)]`（`dcg.py:138`）
  调用**从未被 import 的 `chain`**（`format.py` 只有 `from itertools import groupby`）→ 无条件 `NameError`；
  且该例没有 `datas`，v1 从未产出任何东西。v2 不转写：v1 的 `index` 与 v2 的 `shape="index"` 不是同一个东西。

### 3.6 静默窗口 W2 的三次 `--final` 运行（Run A / B / C）

Lead 开窗后共跑了三次 `--final`。**三次的 revision 都是 `A40457511277…`**（脚本启动时自算），
`equal_ref_v1` / `equal_ref_v2` 三次全 `True`、`errors` 三次全空、`settle=2` 全部成立。

| Run | 启动时刻 | 内容 / 产出 | 进程表是否干净 | 逐例 `v1/v2`（EX1 / EX2 / EX3 / EX4） | 加权 `v1/v2` | §5 `ratio_v2_over_ref` |
|---|---|---|---|---|---|---|
| **A** | `01:10:49.842` | 留证副本 `bench_examples_final_W1_untagged_*` | ⚠ 锚点不符（下达锚点 `80DCA0418503`，实测 revision `a40457511277`，见 §6.1） | 1.029 / **0.635** / 0.968 / 0.891 | 0.347 / 0.443 = **0.783** | **1.117 → NOT MET** |
| **B** | `01:12:47.635` | 写 `bench_examples_final_*`（**后被 Run C 覆盖**） | ❌ **含非本任务的 `python.exe`（pid 1412 / 113,364 K、pid 12812 / 24,036 K）→ 被并发污染** | 0.958 / **0.614** / 0.893 / 0.747 | 0.369 / 0.495 = **0.746** | 0.901 → MET（spread 89.1%） |
| **C** | `01:13:28.315` | 写 `bench_examples_final_*`（**当前内容**） | ✅ 全程只多出本进程 pid 3928（24,148 K）；下一个外来 python `pid 13900` 出现在 `01:13:55.941`（本次已结束） | 1.174 / **0.619** / 0.854 / 0.903 | 0.370 / 0.460 = **0.803** | 0.9367 → MET（spread 6.7%） |

Run A 的另外两个数字：§5 `hand 0.4653 / v1 0.4581 / v2 0.5198`（`ratio_v1_over_ref 0.985`），
波动率 `4.3% / 3.5% / 52.8%` —— **最后一次 12 轮自适应追加触顶仍未收敛，这个触顶记录必须保留**，
它就是"wall-clock 在 5–9 个并发 python 进程的机器上没有 5% 分辨率"的证据。
Run C 的 `v2_source_chars`：EX1 **215** / EX2 **329** / EX3 100 / EX4 110 / §5 块 177。

**与 verifier-dev 的排队**：Run B 之前，进程表里出现 verifier 的 `bench/row_v2.py --frozen --window W2-… --n 200000`
（pid 11164，StartTime `01:12:06`，WS 342 MB，dCPU +5.06 s/5 s ≈ 满一核）。按 Lead 规则**不插队**：
等它 `01:12:42.548` 退出（等待 **15.2 s**）后才开跑，退出瞬间四个 sha1 均无变化。

> **三次 §5 判定互相矛盾**（1.117 / 0.901 / 0.937），其中 A 与 C 两次自证干净却给出相反判定
> → §5 的 5% 阈值判定记 **`INCONCLUSIVE`**，不是 NOT MET 也不是 MET。理由与噪声底标定见 §6.2。
> **可以引用的是逐例方向**：三次 `v1/v2` 同向（0.783 / 0.746 / 0.803，v2 慢 **1.25–1.34×**），
> 主因稳定落在 **EX2**（0.614–0.635）；绝对 µs/行不可引用。

## 4. v2 的差距归因（这部分与并发无关，是代码事实）

### 4.1 helper 开销（`evidence/examples/probe_helper_cost.py`，min of 3 × 2,000,000 次）

| helper | 替代 | helper (ns) | 内联 (ns) | 开销 (ns) | 倍数 |
|---|---|---|---|---|---|
| `_ad(a,b)` | `a+b` | 337.5 | 44.9 | +292.6 | 7.52× |
| `_sb(a,b)` | `a-b` | 374.1 | 55.9 | +318.2 | 6.69× |
| `_ml(a,b)` | `a*b` | 351.1 | 45.3 | +305.8 | 7.74× |
| `_cc(a,b)` | `a & b` | 425.9 | 72.1 | +353.8 | 5.91× |
| `_lt(a,b)` | `a<b` | 402.6 | 49.6 | +353.0 | 8.11× |
| `_eq(a,b)` | `a==b` | 409.7 | 48.9 | +360.8 | 8.37× |
| `_nt(a)` | `not a` | 204.9 | 47.2 | +157.7 | 4.34× |
| `_neg(a)` | `-a` | 235.7 | 59.9 | +175.8 | 3.93× |
| `_ix_get(l,2)` | `l[2]` | 134.9 | 41.9 | +93.0 | 3.22× |
| `_ix_get(l,9)`（越界） | — | 337.0 | — | — | — |
| `_ar(1,2,3)` | `[1,2,3]` | 252.1 | 74.0 | +178.1 | 3.41× |
| `_S(a)` | `str(a)` | 180.0 | 99.0 | +81.0 | 1.82× |
| `_N(a)` | `int(a)` | 219.1 | 48.7 | +170.4 | 4.50× |

- helper 名取的是 `dcg/codegen.py` **实际发射**的名字（`runtime.py` 里没有 `_concat`/`_not`/`_str`，
  它们叫 `_cc`/`_nt`/`_S`）。
- 第一次运行里 EX1 每行有 **3× `_ad` + 4× `_ix_get` ≈ 1412 ns/行**，而实测 v2−v1 差 ≈ **1640 ns/行**
  → 该例的差距**基本可以完全归因**到 helper 调用；第二次运行内联落地后差距相应缩小。

### 4.2 无 helper 的例只慢 ~12–21%

```python
# ex3 (130 chars)                      # ex4 (140 chars)
def run(_d):                           def run(_d):
 _d=_d['index']                         _d=_d['index']
 _o=[]                                  _o=[]
 for R in _d:                           for R in _d:
  if R:                                  if R:
   _o.append([R[1],R[2],R[3]])            _o.append([R[5]['a'],R[0],R[1],R[2]])
 return _o                              return _o
```
EX3/EX4 的 v1/v2 比是 0.878 / 0.795，即 v2 只慢 14–26% —— 这两个生成码里**一个 helper 都没有**。
这直接印证 §4.1：差距的来源是 helper 调用，不是 codegen 结构。

### 4.3 内联落地后仍然残留的 helper：常量下标走了 `_ix_get`

内联落地后的 EX1 生成码（342 字符）第 13 行：

```python
_o.append([hour,hourA,hourB,R[2],str(_a1) + str(hourB),(hourA+hourB),(_a2+_a1),
           _ad(_ix_get(hour,2),123),
           _ad(_ix_get(_ix_get(hour,3),2),_ix_get(_ix_get(hour,3),1))])
```

`(hourA+hourB)` / `(_a2+_a1)` 已内联 ✅，但 `hour[2]`（**常量下标**，且 `hour` 的 `type` 是 `"list"`）
仍然发成 `_ix_get(hour,2)`，`#.hour.3.2` 更是一次表达式里 4 个 `_ix_get`。
`CONTRACT.md` §3.6 把常量下标列为 `("index",base,("lit",k,loc),loc)`，而 `_ix_get` 的 undefined 安全形式
是给**动态下标**准备的（也是 `a[0] == a` 标量语义的实现处）。
**优化建议（不在本任务写入域内，已报 Lead）**：当 base 的类型静态已知为 list/str 且下标是字面量时，
直接发 `hour[2]`；只在类型未知时保留 `_ix_get`。可一次性拿掉 EX1 全部 4 次 `_ix_get`。

**复测（2026-10-17，`evidence/examples/_dump_v2_examples.py` + `ast` 计数）—— 已归零 ✅**：

| 项 | 内联前 | 中途复测 | **最新复测** |
|---|---|---|---|
| EX1 生成码长度 | 352 字符 | 336 字符 | **215 字符** |
| EX1 `_ix_get` 调用点（AST 计） | 4–5 | 5（仍非 0） | **0** |
| `from dcg.runtime import *` | 有 | 又出现（`_ix_get` 仍需 import） | **整个 import 行消失**（EX1 已无任何 helper） |
| `def run(_d)` / `for R in _d` | `_d`/`_d` | `_d`/`_d` | **`_d`/`_d`，一致** |

最新 EX1 生成码（215 字符，`evidence/examples/_dump_v2_examples.py`）：
```python
def run(_d):
 _d=_d['index']
 _o=[]
 for R in _d:
  if R:
   _a=R[5]['a']
   _a1=_a[1]
   _a2=_a[2]
   _o.append([_a,_a1,_a2,R[2],str(_a1) + str(_a2),(_a1+_a2),(_a2+_a1),(_a[2]+123),(_a[3][2]+_a[3][1])])
 return _o
```
`hour[2]` → 裸 `_a[2]`、`#.hour.3.2` → 裸 `_a[3][2]`，且 `hour`/`hourA`/`hourB` 三个中转局部变量也被消掉（`CONTRACT.md` §4.2 规则③）。
即 `CONTRACT.md` §4.3.1「常量下标 + 声明为 list 的字段 → 裸下标」**已落地**。

**仍残留 1 处（EX2_DOCS，是另一种情形，已报 Lead）**：`_ix_get(hourX, 0)`。基址 `hourX` 的 FieldSpec
是 **`{"type": "list", "expr": "[1, 2, 3]"}`** —— 口径校正：Lead 称之为"`const` 数组字面量"，按 schema 实测
是 **`expr` 键**（值是字符串 `"[1, 2, 3]"`），`const` 在 v2 里是另一个键（`EX2_DOCS` 的 `keyx` 才是 `"const": 100`）。
无论哪种叫法，它的取值表达式本身就是字面量、**静态可知**，按同一条思路也可以折叠成裸下标/常量；
区别在于静态可判定性来自**表达式树**而不是**字段的列绑定 + 类型声明**。定性 = **已确认的优化机会**，
不是缺陷、不是"未达标"（详见 `docs/examples_v1_to_v2.md` §10.3 / §10.4）。

### 4.4 与并发无关的两条 v2 事实（用于解释 `v2 N/A`）

```
compile_expr('$.data.ID')            -> Unsupported: 第 2 列: 深路径 [data,ID] 不可逐行编译（…契约 §6.1…）
compile_expr('$.data.TASK_TYPE[0]')  -> Unsupported: 第 2 列: 深路径 [data,TASK_TYPE,0] 不可逐行编译（…）
compile_expr("$.data.f'AIRPORTIATA_{len(IATA)}'")
                                     -> Unsupported: 第 9 列: 语法错误 … 多余内容: "'AIRPORTIATA_{len(IATA)}'"
compile_expr('$.data.AIRPORTIATA_1,$.data.AIRPORTIATA_2')
                                     -> Unsupported: 第 21 列: 语法错误 … 多余内容: ','
```

## 5. 产物形态不是同一个东西（写结论时必须带上）

| | v1（本基准配置） | v2 |
|---|---|---|
| 返回值 | `{'doc': [行, ...]}` / `{'docs': [...]}`：**帧名 → 行** 的 dict | `[行, ...]`：裸行列表（`shape="list"`） |
| 行 | 位置列表（9/3/4/15 字段） | 位置列表（同字段、同顺序） |
| 出错行 | `TRYE_MODE=True` 时是 `(结果, 错误桶)`；本基准关掉 TRYE，故是裸结果 | 无错误桶 |

`protocol=False`（EX4 原样）**不是** `tuple(doc.items())` 的输出形态 —— 实测 v1 EX4 输出是
`({'docs': [{'hour': [0,1,2], 'hourA': 'a0', 'hourB': 0, 'hourC': 0}, ...]}, [])`，即**逐行 dict**。
`tuple(doc.items())` 在 `dcg.py:109-110`，作用对象是**输入**行：
`datas = {key: tuple(doc.items()) if isinstance(doc, dict) else tuple(doc) for key, doc in datas.items()}`。
本基准为让两侧产物同形，EX4 的 v1 侧强制 `protocol=True`（记录在 `notes`）。
v2 的 `shape="dict"` 是**列式** `{'h': [0]}`、`shape="index"` 是 `{0: [0]}` —— 与 v1 的逐行 dict 也不等价。

## 6. 复现

```powershell
python -X utf8 bench/bench_examples.py                 # 200k 行，标 provisional，写 evidence/examples/
python -X utf8 bench/bench_examples.py --n 2000        # 快速通路冒烟
python -X utf8 bench/bench_examples.py --only 1        # 单例
python -X utf8 bench/bench_examples.py --tag postinline --label "PROVISIONAL (post-inline, concurrent load)"
                                                       # 追加一组证据，不覆盖既有原始文件
python -X utf8 bench/bench_examples.py --final         # 仅限 Lead 的静默窗口
python -X utf8 evidence/examples/probe_helper_cost.py  # helper 开销归因
```

退出码：`0` 全部成功、`1` 有例报错（原文同时进 JSON 的 `errors`）、`3` v2 未就绪。

**⚠️ 退出码 `1` 的两种含义必须分清**：把 stdout 用 `Select-Object -First N` 截断会提前关掉管道，
Python 收到 `BrokenPipeError` → **退出码 1 而 JSON 的 `errors` 是空的**。判断成败**读 `errors`，不要只看退出码**。

### 6.1 流程（`--final` **必须**带 `--tag`；W2 跑了 Run A/B/C，W3 起改用 CPU 口径）

1. ~~Lead 发"停手/开窗"信号，逐个确认没有后台 `python` job~~ —— **静默窗口要求已作废**
   （Lead m01149：CPU 口径对抢占免疫）。改为：**测量前后各取一次 sha1**，报告头部断言
   `revision_stable_during_run`；若期间 `dcg/**` 被改写，脚本把差异写进 `errors` 并**退出码 1**。
2. 命令与产物（tag 自己定，**同一个 tag 不重跑**）：

```powershell
python -X utf8 bench/bench_examples.py --final --tag W3-2026-10-17T01-41 --n 200000
#  -> evidence/examples/bench_examples_final_W3-2026-10-17T01-41_raw.txt   <- 这是终值
#     evidence/examples/bench_examples_final_W3-2026-10-17T01-41.json
```

   **N 必须 ≥ 50000**：CPU 口径一次计时内跑 K=8 遍，N 太小时连一个 Windows tick（~15.6 ms）都填不满，
   `dt = 0.0` → 脚本显式抛 `RuntimeError` 并把原因写进 `errors`（**绝不拿 0 算比值**）。

   `--final` 不带 `--tag` → 退出码 `2`（固定文件名会让同一次窗口的多次运行互相覆盖，见 §6.1b）；
   目标文件已存在 → 也退出码 `2`，要重跑就换 tag 或显式 `--overwrite`。
   `--tag X` 不带 `--final` → `bench_examples_X_*`（provisional 证据用这条）。
3. 同一次运行里会带上 **CONTRACT §5 手写参照块**（CPU 口径，见 §6.2），所以 Lead 能一次判完
   「v2 ≤ 1.05× 手写参照」这条契约承诺。
4. 报告头部自动带本次测量对应的 **sha1**（`dcg/codegen.py` / `dcg/runtime.py` / `dcg/schema.py` /
   `dcg/jx/compiler.py`，缺文件记 `MISSING` 而不静默跳过）+ 时间戳 + 进程表；
   四条合起来才能证明"这个数字对应这份生成器代码"。

**sha1 审计轨迹（Run A 为什么标 `W1_untagged`）**：下达的锚点是 `codegen 80DCA0418503`（采样于
`01:10:09` 的 mtime）；我在 `01:10:46.204` 重取 sha1 时**逐字相符**，但就在同一条命令执行期间
（`01:10:46.784`）`codegen.py` **被改写**（43,816 B → 44,136 B）；`01:10:49.842` 启动 `--final`
时脚本自算 revision 已是 `a40457511277ffda701eceb515ba37e4ba8cfda2`。据此**按规则停手上报**，
产物留证为 `bench_examples_final_W1_untagged_*`，**不作终值**。Lead 裁定：该次数字有效但锚定
`a4045751`；重跑时正文须写明

```
label_actual        = FINAL
measurement revision = A40457511277（脚本启动时自算；下达锚点 80DCA041 在其改写前 0.58 s 被采样，不构成测量锚点）
```

（顺带排除换行归一化：三个未变文件的 raw 哈希与 CRLF→LF 哈希相同，`codegen.py` 的 CRLF 计数为 0。）

注意：把输出接进 `Select-Object -First N` 之类会提前关管道，Python 收 `BrokenPipeError` 后
**退出码会变成 1 而 `errors` 为空** —— 判断成功与否请读 JSON 的 `errors`，不要只看退出码。

### 6.1b `--final` 产物的两个缺陷 —— **已在 W3 修掉**（2026-10-17）

1. **产物名不含 tag、会互相覆盖** → **已修**：`--final` 现在**必须**带 `--tag`，产物名
   `bench_examples_final_<tag>_{raw.txt,json}`；目标文件已存在时**拒绝覆盖**（退出码 2），
   要盖只能显式 `--overwrite`（对齐 `dcg.build(..., overwrite=False)` 的语义）。
   两个真实事故：Run B 的 JSON 被 Run C 覆盖（数字只留在 §3.6）；provisional 的 `00:48:12` 那次
   被 `00:51:03` 覆盖（`bench_examples_raw.txt` 现在只剩后者，前者只活在 §3.2）。
2. **`label` 写死在 JSON 里** → **已修**：`--label` 缺省值改为 `None`，由 `--final` 决定
   （`FINAL (CPU clock)` / `PROVISIONAL (concurrent load)`）；JSON 里现在同时记
   `label_requested` / `label_actual` / `final` / `tag` / `output_txt` / `output_json`，
   报告头部也打印这一行。历史产物（`bench_examples_final_*`、`..._W1_untagged_*` 等）保持原样不动。

配套的硬约束（Lead m01026，**已落地**）：
**§5 块的 CPU 口径计时器只能从 `bench/bench_row.py` `import`，不得在本脚本里另写一份**
（`measure_cpu(fn, n, reps=7, k=8)` / `measure_interleaved_cpu(variants, …)`，唯一实现处；
本脚本实际用的是后者）。理由是双实现漂移已经吃过一次亏（`dcg/runtime.py` 的 `HELPERS` 与 `SOURCE`
两套实现对拍出 **1942 处**不一致）—— **测出来的数不可信比测不出来更糟**。

### 6.2 CONTRACT §5 手写参照块（同一次运行，**CPU 口径**）

§3 的表回答的是「把 v1 的六例搬过来，两个引擎谁快」；契约 §5 问的是**另一个问题**：
「v2 是否 ≤ 1.05× **手写**纯 Python 循环」（`bench/_workload.py` 的 `ref_tight` / `V1_DESCRIBE` /
`V2_SCHEMA`，即 8 字段 M0 负载）。这条**不共用 §3 的墙钟口径**，用的是 CONTRACT §7.1 的 CPU 口径：

```
metric  = time.process_time()，一次计时内跑 k=8 遍摊销 Windows ~15.6ms 粒度，
          变体 **interleaved** 交替，取 reps=7 的 min，跑 blocks=3 个独立 block
实现    = bench/bench_row.py::measure_interleaved_cpu（唯一实现，只 import）
先决条件 = 三种变体的产物必须**逐行相等**（equal_ref_v1 / equal_ref_v2），否则比值无意义
A/A     = 同一个 callable 放进两个 slot，跑 ≥3 block -> worst |ratio-1| = 噪声底
判决    = margin=|worst_ratio-1.05|；blocks_disagree_in_sign -> INCONCLUSIVE；
          margin < noise_floor -> WITHIN_NOISE；否则 RESOLVED（PASS 要求所有 block 都 ≤1.05）
```

报告块（JSON 里是 `report["hand"]`）逐字形态（`--n 50000` 冒烟，`--tag cpu_smoke2`，产物已删）：

```
CONTRACT §5 workload (CONTRACT §5 M0 (8 fields)), **CPU-clock** metric (CONTRACT §7.1):
  time.process_time(), k=8 batched passes per timing, interleaved, min of 7 reps -- 计时器 = bench/bench_row.py::measure_interleaved_cpu（唯一实现）
  variant      us/row       rows/s     min(s)       spread/blk
  hand          0.625      1600000     0.0312   16.7%-35.3%
  v1            0.703      1422222     0.0352   15.8%-33.3%
  v2            0.664      1505882     0.0332   16.7%-22.2%
  products equal to the hand reference: v1=True  v2=True  (rows 50000/50000/50000)
  x/hand per block: v1 = 1.056 1.188 1.059
  x/hand per block: v2 = 1.000 1.062 1.059   (worst = 1.062)
  A/A noise floor (SAME callable, 2 slots, 3 blocks): 0.944 0.941 1.000 -> worst |ratio-1| = 0.0588
  §5 verdict  (target v2 <= 1.05x hand): INCONCLUSIVE  (blocks_disagree_in_sign; margin=0.0125, noise_floor=0.0588)
```

这只证明通路可跑通 + 产物逐行相等；5% 的判定力要看 N=200000 的终值（§6.2.2）。
JSON 字段：`us_per_row`、`us_per_row_per_block`、`rows_per_s`、`min_s`、`spread_{min,max}_pct`、
`ratio_v1_over_ref_per_block`、`ratio_v2_over_ref_per_block`、`ratio_v2_over_ref`(worst)、
`aa_noise_floor{ratios_per_block,worst_abs_dev_from_1,fallback_used}`、`verdict_v2`/`verdict_v1`
（含 `verdict`/`reason`/`margin`/`noise_floor`/`pass`）、`sec5_target_met`、`v2_source_chars`。

**⚠️ 单位陷阱（我自己踩过，已修，记在这里免得下一个人再踩）**：`bench_row.measure_interleaved_cpu`
返回的**已经是 µs/行**（内部 `dt/(k*n)*1e6`）。第一版实现又除了一次 `n`，把 `us/row` 与 `rows/s`
两列分别放大/缩小 5 倍（N=200000 时 hand 印成 `6.055 µs/row`，真值 `1.211`）。
**比值不受影响**（分子分母同源），所以 §5 判定当时就是对的；但绝对值两列是错的 ——
这正是"数字看着合理却无意义"的典型，故该版产物已删除、改用修正后的 tag `W3-2026-10-17T01-36`。

**小 N 会被显式拒绝**：`process_time()` 在 n 太小时（k=8 遍跑不完一个 tick）测得 `dt=0.0`，
脚本抛 `RuntimeError` 并把原文写进 `report["errors"]`（**绝不拿 0 去算比值**）。
若 A/A 噪声底本身跑失败，则回落到 Lead 标定的 `0.0506` 并在报告里标 `[FALLBACK]`，不静默。
若手写参照块整体出错，同样进 `report["errors"]`、退出码变 1。

#### 6.2.1 §5 的正式判定：**`INCONCLUSIVE`（wall-clock 没有 5% 分辨率）**

W2 三次 `--final` 在同一 revision `A40457511277` 上给出 **1.117 / 0.901 / 0.937**，
其中 Run A 与 Run C **都自证进程表干净**却给出**相反判定**。Lead 的噪声底标定给出解释：
把**同一个逐字相同的函数**（`ref_tight` 形态）放进两个 slot 交替测 15 轮 ——

```
单变体自身 spread        : 39.3% / 67.7%（第二次实验 82.8% / 97.0%）
两个 slot 的"比值"       : ratio(med) 0.974 / ratio(min) 1.020 / ratio(max) 1.168
```

→ **两个完全相同的函数，"比值"能在 0.97–1.168 之间漂**。在这种机器上（5–9 个并发 `python.exe`）
判 1.05 这种 5% 阈值**不可能有判定力**。

**因此**：`§5 target v2 ≤ 1.05× 手写参照` 记

> `INCONCLUSIVE (wall-clock has no 5% resolution; see noise-floor calibration)`

**不得据此宣称达标，也不得据此宣称不达标**；1.117 不是结论，0.937 也不是结论。
Run A 那次"12 轮自适应追加触顶 52.8% 仍未收敛"的记录**保留**（§3.6），它正是这个结论的证据。

**有判定力的是 CPU 时间口径**（CONTRACT §7.1，Lead m01149；本脚本已按此实现，见 §6.2）：
`time.process_time()`、一次计时内跑 **K=8** 遍摊销 Windows ~15.6 ms 粒度、变体 **interleaved** 交替、
取 **reps=7** 的 min、跑 **3 个独立 block**；A/A 噪声底（同一 callable 两 slot，≥3 block）本机标定
**worst |ratio-1| = 0.0506**（`bench/probe_noise_floor.py` → `evidence/noise_floor.json`）。

Lead 在同一冻结 revision `A4045751` 上用该口径测真产物 vs 冻结手写参照（产物已 assert 逐行相等）：

```
hand ref (ref_tight) 1.0938 µs/row
v2 M0 filter=true    1.0645 µs/row   -> x/hand = 0.965 / 0.991 / 0.965   (三个 block 全 ≤1.00)
v2 M0 filter=false                  -> x/hand = 0.991 / 0.973 / 0.965
```

→ **`RESOLVED PASS`**（`v2/hand ≈ 0.96–0.99`）。注意**绝对值跨会话漂 56%**（本轮 hand 1.0938 vs
上一轮 0.7031）→ 只有**比值**可以引用，绝对 µs/行 只作参考。
判决规则逐字如下（`bench/bench_examples.py::_sec5_verdict` 实现同一套）：

```
margin = |worst_ratio - 1.05|
blocks_disagree_in_sign        -> INCONCLUSIVE
margin < noise_floor           -> WITHIN_NOISE
否则                            -> RESOLVED（PASS 要求所有 block 都 ≤1.05）
```

**W3 起本脚本已按 CPU 口径实现并跑出终值 → 见 §6.2.2**（终值 `W3-…T01-41`：`RESOLVED` / `MET`，worst 1.013）。
§6.2.1 这一段说的是 **wall-clock** 口径（W2 的 Run A/B/C）为什么不可用，两者不要混读：
**wall-clock = `INCONCLUSIVE`；CPU 口径 = 可判定，但余量只有 1–4%（§6.2.2）。**

#### 6.2.2 W3 终值：同一 revision `A4045751` 上跑了两次 CPU 口径 `--final`（**N = 200000**）

两次都 `exit 0`、`revision_stable_during_run = True`、四项 sha1 相同
（`codegen a40457511277ffda701eceb515ba37e4ba8cfda2` / `runtime cd760a6fba57b8144118a60520c8caf2a62848bb` /
`schema f4ce36da9c9a127aee67568b320bf13db7f522ce` / `jx/compiler 9c5b2c8128a0c5de4d8a2309544f4128e153ee8a`）、
`products equal to the hand reference: v1=True v2=True`（20 万行逐行相等）：

| 运行 | hand µs/行 | v2 µs/行 | `x/hand` v2 per block | worst | A/A 噪声底 | margin | 判决 |
|---|---|---|---|---|---|---|---|
| `W3-…T01-36`（16:36Z→01:36） | 1.113 | 1.104 | 1.008 / 1.026 / 0.991 | 1.026 | 0.0270 | 0.0241 | **`WITHIN_NOISE`**（三个 block 全部 ≤1.05） |
| `W3-…T01-41`（**终值**） | 0.762 | 0.771 | 1.000 / 1.000 / 1.013 | 1.013 | 0.0127 | 0.0372 | **`RESOLVED` → MET（三个 block 全部 ≤1.05）** |

- **终值产物**：`evidence/examples/bench_examples_final_W3-2026-10-17T01-41_{raw.txt,json}`。
  同一次运行里的逐例墙钟（v1 / v2 / `v1÷v2`，比 <1 = **v2 更慢**）：EX1 0.439 / 0.411 / **1.069**（v2 略快）、
  EX2 0.626 / 0.953 / **0.657**（**v2 慢 ~1.5×，六次 `--final` 方向全一致，是唯一稳定的大差距**）、
  EX3 0.118 / 0.133 / 0.891（v2 慢 ~12%）、EX4 0.139 / 0.166 / 0.835（v2 慢 ~20%）、
  EX5 v1 0.361 / v2 `N/A`；加权（800000 行）v1 **0.331** µs/行（3,025,459 行/秒）/
  v2 **0.416** µs/行（2,405,293 行/秒）/ `ratio v1/v2 = 0.795`。
  ⚠️ 逐例方向在运行间**只有 EX2 稳**：EX1 在五次墙钟 `--final` 上是
  `1.029 / 0.958 / 1.174 / 1.015 / 1.069`（只有 W2-Run B 那次 v2 慢 4%），故 **EX1 不能单独引用**；
  EX3/EX4 每次都是 v2 慢但幅度小（5–20%）。
- **`T01-36` 产物保留**：`bench_examples_final_W3-2026-10-17T01-36_{raw.txt,json}`。
  它的**最后一行按当时的直译印 `NOT MET`**，而三个 block 其实全部 ≤1.05 —— 这个 wart 已在
  `sec5_label` 三态里修掉，**读那份产物时请把该行读作 `WITHIN_NOISE`**（数字本身有效）。
- **两次的差别只说明噪声**：同一 revision、同一口径，`hand` 绝对值 **1.113 → 0.762（漂 46%）**，
  `x/hand` worst **1.026 → 1.013**。绝对值跨运行不可引用（Lead 实测漂 56%），
  **只有比值是结论**；而比值的 5% 判定力取决于当次 A/A 噪声底，所以 §5 只报
  `verdict` + `margin` + `noise_floor`，**不报"达标/不达标"这种二元话**。
- **三态标签**（`report["hand"]["sec5_label"]`，`--final` 的正文最后一行直接印它）：

  | `sec5_label` | 含义 |
  |---|---|
  | `MET (RESOLVED; all N blocks <= 1.05)` | 有判定力，且每个 block 都达标 |
  | `NOT MET (RESOLVED; blocks = …)` | 有判定力，且有 block > 1.05 |
  | `WITHIN_NOISE (blocks = …; all_blocks_pass=…)` | **判不出**：margin 落在 A/A 噪声底以内。**`all_blocks_pass=True` 时它不是"没达标"** |
  | `INVALID (v2 产物与手写参照不逐行相等)` | 产物本身有问题，比值无意义 |

  `sec5_target_met` 仍是布尔，语义**逐字保持** Lead 的规则：只有 `RESOLVED` 且全 block 达标才 `True`。
  两个字段一起读：`sec5_target_met=False` + `sec5_label=WITHIN_NOISE(...)` = "没判出来"，不是"失败了"。

  > **`WITHIN_NOISE` = 判不出，既不算达标也不算不达标**（Lead m01400 加固要求 ①）。
  > 它与 `MET` / `NOT MET` 是三个互斥的出口，**任何地方都不许把它简写成其中任一个**。
  >
  > **每处报 `MET` 都必须紧邻 `margin` 与 `noise_floor`**（加固要求 ③）：本节的 `MET` 永远写成
  > `MET (RESOLVED; x/hand worst 1.013, margin 0.0372 >= noise_floor 0.0127)`。
  > 当 `margin` 与噪声底**同量级**时（本轮两次运行正好一个 0.0241 vs 0.0270、一个 0.0372 vs 0.0127），
  > **不得**在别处（README / docs / 报告 / 口头结论）简写成「**v2 已达标**」——
  > 正确写法见 §7 第 5 条："贴着 1.00×，最坏观测 1.026×，从未超过 1.05×，余量仅 1–4%、与噪声底同量级"。
- 顺带一条对照：**v1 自己**在同一门上是 `WITHIN_NOISE`（T01-41：`x/hand` = 1.025/1.038/1.026，margin 0.0120 < noise_floor 0.0127）。
  这与 §4 的结论一致 —— 纯 Python 路径上 v2 与 v1 本来就贴着（EX1/EX3/EX4 甚至互有胜负），
  真正稳定的差距只在 **EX2**（见 §4.3 与 `docs/examples_v1_to_v2.md` §10.3）。

## 7. 结论（只有**方向**、**产物事实**与**归因**可用；绝对 µs/行与 5% 阈值判定不可用）

1. **通路成立**：v1 与 v2 可在同一进程内同时装载、同时计时；v1 侧六例（EX6 例外，见 §3.5）、
   v2 侧四例全部真实执行，`errors` 为空（三次 provisional + 三次 `--final` 全部如此），
   且 `equal_ref_v1` / `equal_ref_v2` 三次 `--final` 全 `True`（产物与手写参照逐行相等）。
2. **第一次运行时 v2 在样例负载上明显慢**（加权 0.444 → 1.155 µs/行，v1/v2 = **0.384**），
   差距**几乎全部**来自逐运算符 helper（每个 +81…+361 ns）；无 helper 的 EX3/EX4 只慢 ~18%。
3. **编译期内联部分落地后差距收窄**（加权 0.464 → 0.823 µs/行，v1/v2 = **0.564**），
   此时 `hour[2]` 一类**常量下标**仍在走 `_ix_get`（§4.3）。
4. ~~内联完成后 v2 已在样例负载上追平 v1（1.011）~~ —— **此行已被静默窗口的三次 `--final` 推翻**：
   那次 `1.011` 是 provisional 序列里波动最大的一轮（8 个并发 `python.exe`）。W2 三次 `--final`
   （revision `A40457511277`）的 `v1/v2` = **0.783 / 0.746 / 0.803**，**三次同向**：
   **在样例负载上 v2 稳定慢 v1 的 1.25–1.34×，主因始终是 EX2（0.614–0.635）**。
   这个方向性结论**超出噪声底**（三次同向、跨 Run A/B/C），可以写；**绝对 µs/行不可以写**。
5. **`§5 target v2 ≤ 1.05× 手写参照`：wall-clock 口径 = `INCONCLUSIVE`；CPU 口径 = `MET`（但只有约 3% 的余量）**。
   W2 三次 wall-clock `ratio_v2_over_ref` = 1.117 / 0.901 / 0.937，两次自证干净的测量给出相反判定；
   噪声底标定（同一函数"比值"漂 0.97–1.168）证明 wall-clock 无 5% 分辨率 → **不得据此宣称达标或不达标**（§6.2.1）。
   W3 改用 CPU 口径（`time.process_time()`、K=8、interleaved、reps=7、3 blocks + A/A 噪声底）后：
   终值 `W3-…T01-41` 给出 `x/hand` = 1.000 / 1.000 / 1.013（worst **1.013**，A/A 噪声底 0.0127）
   → **`RESOLVED` / `sec5_target_met = True`**；同 revision 早 5 分钟的 `T01-36` 给出 worst 1.026、
   margin 0.0241 < 噪声底 0.0270 → `WITHIN_NOISE`（**不是**不达标，见 §6.2.2 的三态标签）。
   两次合起来的唯一诚实说法：**v2 在这条负载上贴着 1.00×，最坏观测 1.026×，从未超过 1.05×，但余量只有 1–4%，
   与噪声底同量级** —— 所以判 "MET" 时必须同时给出 `margin` 与 `noise_floor`，不能只给结论。
6. **归因可引用**（量与噪声差 2 个数量级）：每行 3×`_ad` + 4×`_ix_get` ≈ 1412 ns/行，
   v2−v1 差 ≈1640 ns/行 → 差距基本归因到 **helper 调用本身**，而非 codegen 结构。
   产物侧另有与时钟无关的硬事实：**CAP 445 → 149 字符、M0 537 → 195 字符**，
   且 `_ix_get(`、`_ad(`、`is _U` 三个标志在产物里**全为 0**。
7. **绝对数字不可引用**：v1 侧代码未改而十几分钟内单例漂移 **+50%**（0.520 → 0.747 → 0.780），
   波动率最高 149.5%；`--final` 三次也有 52.8% / 89.1% 的轮次；**CPU 口径也一样**——
   同 revision 两次 `hand` = 1.113 → 0.762 µs/行（漂 46%），与 Lead 实测的 56% 一致。
   可引用的是**比值**（且要带 `margin` / `noise_floor`），或与时钟无关的**产物字符数/标志计数**。
