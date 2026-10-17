# v1 `format.py` 测试样例 → v2 迁移对等报告

**任务**：用户要求「新版需要实现旧版测试样例」，并且要如实报告哪些样例无法对等。
**权威来源**：`format.py:577-816` 的样例块（**实测 6 例**，不是 9 例）、
`CONTRACT.md` §2/§3/§6/§7、`evidence/examples/v1_examples_frozen.json`（真实执行 v1 引擎后冻结）。

| 交付物 | 位置 |
|---|---|
| v1 期望（真跑冻结，机器可读） | `evidence/examples/v1_examples_frozen.json` |
| v1 期望（人读，含生成码与异常原文） | `evidence/examples/v1_examples_raw.txt`、`evidence/examples/v1_examples_source.txt` |
| v2 对等 schema | `tests/examples/v1_parity/schemas.py` |
| 对等测试（**15 passed, 0 skipped**） | `tests/examples/test_v1_parity.py` |
| 文档↔测试可追溯性门（**4 passed**） | `tests/examples/test_docs_traceability.py` |
| v1 缺陷最小复现 | `evidence/examples/probe_v1_defects.py` → `v1_defects.json` |
| v2 规格缺口实测 | `evidence/examples/probe_v2_gaps.py` |
| 性能对照 | `bench/bench_examples.md`（W3 CPU 口径 `--final` 终值 `W3-…T01-41`：§5 = `MET`，`x/hand` worst 1.013；wall-clock 口径仍 `INCONCLUSIVE`；见本文 §10） |

复现：`python -X utf8 -m pytest tests/examples -q`

---

## 0. 先更正三处前提（都以实测为准）

1. **样例是 6 个，不是 9 个。** `grep -n '^\s*#\?\s*describe\s*=' format.py` 在 577–816 段命中 589/626/700/734/767/794
   共 6 处（另外 3 处命中在 `format.py:287/494/533` 的真实代码里，不是样例）。
   **原任务书预估 9 例（按 `format.py:575-816` 的段落数推断，未核实），实测可对等/可判定的只有 6 例**；
   原因：该段是**被整段注释掉的 `__main__` 块**，v1 从未自动执行过它，段落数与 `describe =` 的真实个数不等；
   以 `describe =` 的实测命中数为准（6 处），**不是漏了三例**。
2. **六例 as-written 全部真实执行成功，一例都没抛异常。**
   `evidence/examples/probe_v1_examples.py`（每例独立 subprocess，避免全局 `MODE` 跨例泄漏）
   的结论是 `SUMMARY: 6 chunks, 6 ran, 0 raised`。
3. **v1「三处硬缺陷」中有一处的成立条件与描述不同。**
   `_field` 裸名**是会被展开的**，但**只在 `FREE_MODE=True` 时**；`FREE_MODE=False`（v1 默认值）时才会
   `NameError`，而叠加 `TRYE_MODE=True` 会升级成**不抛异常的静默丢数据**。详见 §3 的 D-B。
4. **Lead 已裁决（m00540）**：`show: False` 记为 **v2 的显式能力缺口**（不作为 v2 需求，见 §7）；
   `_ix_get` 常量下标问题并入 parser-dev 的内联任务（不在本任务写入域）；
   性能终值等 Lead 的**静默窗口信号**，在那之前 `bench_examples.md` 一律 `PROVISIONAL`。
5. **Lead 复核确认样例是 6 个**（他先前任务书里的"7/8/9 请自行逐个定位"是错误指令），
   当前结论 `5 完全等价 / 0 部分等价 / 1 无 v1 产物可对` 被接受为最终结论（**2026-10-17 第三次更新**：
   初版 `2/2/2`；例5 曾改判为「仅复现」；第二轮先修 `pyseg` 单段链 bug、再加 `shape="rows"`，
   例4 与例5 双双升级为**逐行结果完全等价**；本轮把例2 的整篇异构文档做成**多产物 + 组装层**，
   19/19 段逐值相等，例2 也升级为结果一致，详见 §4、§5、§6）。

## 1. 逐例对等结论总表

| 例 | `format.py` 行 | v1 真实产物 | v2 对等 | 结论 |
|---|---|---|---|---|
| 例1 | 578–623 | `({'doc': [10 行 × 9 字段]}, [])` | `EX1` ✅ | **完全对等**（含 `#.`→裸名、`hour[2]`、反引号段） |
| 例2 | 626–697 | `({'docs': [], ...}, [10 行原样进错误桶])` | `EX2_DOCS` + `bench/compose_v1_document.py` ✅ | **结果一致（两段式）**：`docs` 组用 `EX2_DOCS`；整篇 15 组用「12 个 v2 产物 + 6 个常量段落」组装，与 v1 关 `CYTHON_MODE` 后 **19/19 段逐值相等**（§4） |
| 例3 | 700–730 | `({'docs': [[0,0,0],[2,-1,1],...]}, [])` | `EX3` ✅ | **完全对等** |
| 例4 | 734–764 | `({'docs': [10 个逐行 dict]}, [])` | `EX4` ✅ | **完全对等**：v1 `protocol=False` 发逐行 dict，v2 用 `output.shape="rows"`（本轮新增）发出**同构**的逐行 dict（§5） |
| 例5 | 767–791 | `([[14528086, 'W', None]], [])` | `EX5` ✅ | **完全对等（规则等价）**：第三字段的运行期动态键写成反引号段 `` `$.data['AIRPORTIATA_' + str(len($.data.AIRPORTIATA))]` ``（§6） |
| 例6 | 794–816 | 无产物（只 parse） | 部分 | v1 的 index 模板从未能跑（`NameError: chain`），v2 的 `shape="index"` 可用但语义不同 |

对等 = 逐字段、逐行、按位置比较；v1 的帧 dict（`{'doc': ...}` / `{'docs': ...}`）是 v1 的容器产物，
v2 `shape="list"` 发裸行列表，比较时按行取值比对（见 `tests/examples/v1_parity/harness.py`）。

**每一例都对应一个真实可跑的测试**（`tests/examples/test_v1_parity.py`，15 个，0 skip / 0 xfail）：

| 例 | 测试 |
|---|---|
| 例1 | `test_ex1_paths_and_ext_shapes`、`test_ex1_v2_matches_v1_rows`、`test_ex1_v2_rows_cover_all_ten_input_rows` |
| 例2 | `test_ex2_v1_cython_mode_loses_every_row`、`test_ex2_the_cause_is_cython_mode_not_trye_mode`、`test_ex2_v2_produces_the_rows_v1_lost` |
| 例3 | `test_ex3_v1_expands_field_aliases`、`test_ex3_v2_matches_v1_rows` |
| 例4 | `test_ex4_v1_protocol_false_emits_row_dicts`、`test_ex4_v2_rows_shape_is_exactly_equal` |
| 例5 | `test_ex5_expr_side_rejects_the_deep_path_the_example_needs` ／ `test_ex5_value_side_accepts_the_deep_path_and_matches_v1` ／ `test_ex5_dynamic_key_is_rule_equivalent_not_a_constant` |
| 例6 | `test_ex6_v1_index_shape_raises_before_producing_anything` |
| 冻结记录本身 | `test_frozen_record_has_six_really_executed_examples` |

上表由 `tests/examples/test_docs_traceability.py` **机器核对**：文档里出现的每个 `test_*` 名字必须真的
定义在 `test_v1_parity.py` 里，且例1..例6 每例都必须被一个 `test_ex<N>_*` 引用 —— 文档与测试脱节会直接 FAIL。

## 2. 例1 —— 迁移规则全部走通的样板

v1 例1 的 9 个字段覆盖了全部迁移难点，是**唯一一个「v1 正确 + v2 完全对等」的复杂例**：

| v1 写法 | v2 写法 | 结果 |
|---|---|---|
| `value: '$.5.a'` | `{"type":"list","value":"$.5.a"}` | ✅ 前缀 CSE：生成码里 `_a=R[5]['a']` 只取一次 |
| `value: '$.5.a.1'` | `{"value":"$.5.a.1"}` | ✅ 数值下标路径（§3.7） |
| `value: '$.2'` | `{"value":"$.2"}` | ✅ 直接引用列，零开销 |
| `ext: 'str($.5.a.1) + str(hourB)'` | ``{"expr":"`str($.5.a.1) + str(hourB)`"}`` | ✅ 反引号内联 Python，段内 `hourB` 是本 schema 字段名（§4.3 `free_field_names`） |
| `ext: 'hourA + hourB'` | `{"expr":"hourA + hourB"}` | ✅ 裸标识符 = 字段（§13.4） |
| `ext: '$.5.a.2 + $.5.a.1'` | `{"expr":"$.5.a.2 + $.5.a.1"}` | ✅ |
| `ext: 'hour[2] + 123'` | `{"expr":"hour[2] + 123"}` | ✅ 值对等（生成码走 `_ix_get`，见性能报告） |
| `ext: '#.hour.3.2 + #.hour.3.1'` | `{"expr":"hour[3][2] + hour[3][1]"}` | ✅ **`#.x.i.j` → `x[i][j]` 规则实测成立** |

v1 与 v2 的逐行结果（10 行 × 9 字段，按位置）**完全相同**，测试
`test_ex1_v2_matches_v1_rows` 直接断言 `v2 行 == v1 行`；`test_ex1_v2_rows_cover_all_ten_input_rows`
另外断言第 4 列确实来自输入的 `data[2]`（`0,-1,0,3,-4,5,-6,7,8,9`），排除"照抄常量也能过"。

## 3. v1 三处缺陷的最终归类

### D-A `format.py:551` 缺 `'filter'` 键 → 裸 `KeyError`

最小复现（`evidence/examples/v1_defects.json`，真跑 v1 引擎）：

```python
describe = {"datas": "$.index", "mode": "", "protocol": True, "single": False,
            "_field": {}, "struct": {"doc": [{"name": "hourA", "value": "$.1"}]}}
# 注意：没有 "filter" 键
fmt.Format.OPT_MODE = True          # 默认值
info, handler = fmt.Format.parse("X", describe)
```
```
File "C:\Users\black\Desktop\DCG\format.py", line 551, in parse
    temp = cls.expParse(describe["filter"], tempMapping)
KeyError: 'filter'
```
`OPT_MODE=False` 时正常得到 `{'docs': [['a0']]}`。
**归类：v1 缺陷，确认存在，但不被六例触发** —— 六例都显式给了 `filter`（例1–4 `""`、例5 `'Flight_ID'`、例6 `None`）。
v2 的对应行为：`output` 是必填块，`shape`/`filter` 等键在 schema 校验期定型，
既不会缺键、也不会发裸 `KeyError`；未知键报带 JSON 路径的 `SchemaError`
（实测 `v2 `output.protocol` key → SchemaError: output: 未知键: protocol`）。

### D-B `ext` 引用 `_field` 裸名 —— **条件性缺陷，不是无条件缺陷**

| v1 配置 | 结果（真跑） |
|---|---|
| `FREE_MODE=True`（例3/例5 自己的配置） | ✅ 正常：生成码 `docs_..._append([data[1], data[2], data[3]])`，裸名被展开 |
| `FREE_MODE=False` + `TRYE_MODE=False` | ❌ `NameError: name '_k' is not defined`，栈顶 `<generated>", line 7, in _inline_call_DSS` |
| `FREE_MODE=False` + `TRYE_MODE=True` | ☠️ **不抛异常**：`({'docs': []}, [('a0',1),('a1',2),('a2',3)])` —— 全部行被静默吞进错误桶 |

**归类：v1 缺陷，成立条件是 `FREE_MODE=False`（默认值）+ `ext` 里用 `_field` 裸名。**
在 `TRYE_MODE=True` 时会升级为**静默数据丢失**。

> ### ☠️ 最高危的一条：静默数据丢失（整份 v1 缺陷清单里最有价值的一条证据）
> **`FREE_MODE=False` + `TRYE_MODE=True` → v1 不抛任何异常，直接返回 `({'docs': []}, [('a0',1),('a1',2),('a2',3)])`：
> 结果为空、错误桶里却整齐地排着全部 3 行。**
> 它证明 v2「`SchemaError` 带 JSON 路径、绝不静默降级」这条设计原则**不是洁癖**：
> 同一份配置在 v1 上会**返回空结果而不报错**，下游拿到一张空表**根本不会知道是配置写错了**，
> 只会以为"今天没有数据"。这类失败比任何性能差距都贵 —— 性能慢是看得见的，静默丢数据是看不见的。

v2 的对应行为：v2 **没有 `_field` 别名层**；裸标识符一律按「本 schema 已声明字段」解析，
未声明则 **build 期**报错并列出可用字段名（实测）：
```
SchemaError: out.fields: 裸名 '_houseA' 不是本次 schema 声明的字段；已声明: ['h']（行内取值请写 $._houseA 或 $.路径）
```
→ **v1 的运行期 `NameError` 在 v2 里被前置为 build 期 `SchemaError`，且带可操作提示。这是 v2 明确修好的一处。**

### D-C `Template.index` 用 `chain` 但从未 import

模板在 `dcg.py:138`：`return index_{$code}, [doc for doc in chain([i for d in index_{$code}.values() for i in d.values()])]`；
`format.py` 只 `from itertools import groupby`（`chain` 从未 import）→
**无条件** `NameError: name 'chain' is not defined`（写在 return 语句里，零行也抛）。
例6 的四种组合实测：

| 组合 | 结果 |
|---|---|
| as-written（`TRYE_MODE=True`） | `NameError: name 'chain' is not defined` |
| `TRYE_MODE=False` | `NameError: name '_key' is not defined`（D-B 先发作） |
| 零行 + `TRYE_MODE=False` | 仍是 `_key`（说明 `_key` 的解包被提到循环外） |
| `FREE_MODE=True` + `TRYE_MODE=True` | 仍是 `chain` |

**归类：v1 缺陷，无条件成立，`index` 形状在 v1 从未被跑过。**
v2 的 `shape="index"` 是真实可用且被 `evidence/accept.py` 的形状语义门覆盖的，
但**语义不同**：v1 的 `'index': {'key': 'merge', 'number': ''}` 是「按 key 建索引 + 去重合并」，
v2 没有这个键（实测 `v2 shape='index' + index key → SchemaError: output: 未知键: index`），
v2 的 index 只是「行下标 → 行值」的容器形状。

### D-D（本次新发现）`CYTHON_MODE=True`（v1 默认值）对例2/3/4 是坏的
见 §4。

## 4. 例2 —— v1 在**自己的默认配置**下把 10 行全部丢掉

例2 as-written 的产物：`'docs': []`，而 10 行原始 tuple 原封不动躺在错误桶 `[10 行]` 里
（`TRYE_MODE=True` 把每行的异常吞了）。

**定因实验（`probe_v1_examples.py` 的 `variants`，每个变体只翻一个标志）**：

| 变体 | 例2 | 例3 | 例4 | 例1 | 例5 |
|---|---|---|---|---|---|
| as-written（`CYTHON_MODE=True` + `TRYE_MODE=True`） | ❌ `docs=[]`，10 行进错误桶 | ✅ | ✅ | ✅ | ✅ |
| 仅关 `TRYE_MODE` | ❌ `TypeError: Expected list, got int` | ❌ `TypeError: Expected list, got tuple` | ❌ `TypeError: Expected list, got tuple` | ✅ | ✅ |
| 仅关 `CYTHON_MODE` | ✅ 10 行 | ✅ | ✅ | ✅ | ✅ |

异常原文（verbatim，pyx 名每次随机）：
```
File "_cython_yutwmjsrci.pyx", line 26, in _cython_yutwmjsrci._inline_call_DSS
TypeError: Expected list, got int
```
根因：`CYTHON_MODE` 按**字段名**生成 `cdef` 类型标注，而例2 的 struct 里 `hour` **被声明两次**
（先 `'$.4'` 后 `'$.5.a'`）→ 标注取到 `list`，运行期却把 `data[4]`（int）赋给同一个局部变量。
**归类：v1 缺陷。（a）`CYTHON_MODE=True` 是 v1 默认值；（b）`TRYE_MODE=True` 会把异常升级成静默丢数据；
（c）两个标志在六例中的出现率都很高（`TRYE_MODE=True` 出现在 5/6 例）。**

> **这**是 v1 的缺陷，**不是 v2 的问题**，两者必须分开看：
> - v1 的 Cython 路径是**另一条 codegen 路径**：它按字段名推 `cdef` 类型，于是"同名重复声明"这种
>   在纯 Python 路径下无害的写法会变成运行期 `TypeError`，再被 `TRYE_MODE` 吞成静默丢数据（上面 2 因子表）。
> - v2 的 Cython 产物是**纯 Python 产物 + 每个局部一行 `cdef object`**，语义**不变**。
>   `CONTRACT.md` §4.2 规则⑨要求「Cython 模式同源，不改语义」，实测前 2000 行与纯 Python 产物**逐行相等**。
> - 也就是说：v1 的"加 Cython 就快"是以**改变语义**为代价（还会静默丢数据），v2 的 Cython 只做类型标注。
>   两者不是同一件事，本基准因此**只给 v1 的纯 Python 路径计时**（`CYTHON_MODE=False`）。

**v2 的对等实现（两段式）**：
1. `docs` 组：`EX2_DOCS` 15 字段，产出与「v1 关掉 `CYTHON_MODE` 后」的 10 行**逐值相同**
   （测试 `test_ex2_v2_produces_the_rows_v1_lost` 拿 `variants["cython_off"].result` 当真值断言）。
2. 整篇文档：v2 **没有** `shape="document"`，所以整篇不是「一个 schema」而是一层**组装**：
   `bench/compose_v1_document.py` 把 11 个顶层段落拆成 **12 个 v2 产物 + 6 个常量段落**，
   逐段 generate + run 后拼回 v1 的文档形状，与冻结的 v1 产物（`cython_off_trye_off` 变体）
   **19/19 段逐值相等**（证据 `evidence/examples/v2_ex2_document.json`）。
   门禁：`tests/test_v1_ex2_document.py`（7 个测试，含「顶层段落全覆盖」「嵌套段真被组装」
   「单字段段落必须展平成标量列表」「常量段落是字面量」）。

**两处必须写明的语义差异**：
1. **重名字段**：v1 的 struct 允许 `hour` 出现两次（两次引用同一个生成局部变量，所以两个位置都发 list）；
   v2 在 validate 期拒绝重名，schema 里用 `hour2` 承载第二次声明 —— 值相同、位置相同、名字不同。
2. **`datas` 作用域**：v1 例2 的 struct 是一个 dict（`docs`/`docsx`/`xdocs`/… 多组并联输出），
   v2 一次只发一个 `out` 对象，所以迁移形态是**多产物 + 组装层**（生产迁移里的 `manual` 项就是这类）。
   结论仍然是「结果一致」，但**不能声称 v2 一个 schema 就能发出整篇文档**。

## 5. 例4 —— `protocol` 的真实语义是**行形状**，不是 `tuple(doc.items())`

实测 v1 例4 产物：
```
({'docs': [{'hour': [0,1,2], 'hourA': 'a0', 'hourB': 0, 'hourC': 0},
           {'hour': [0,1,2], 'hourA': 'a1', 'hourB': 2, 'hourC': -1}, ...]}, [])
```
例3 与例4 的生成码**唯一差别**就是 `append(temp)` vs `append(dict(zip(keys, temp)))`。
外层容器两例都是 `{'docs': [...]}`。`tuple(doc.items())` 出现在 `dcg.py:109-110`，
作用对象是**输入**行：`datas = {key: tuple(doc.items()) if isinstance(doc, dict) else tuple(doc) for ...}`。
→ 所以不能把「v1 `protocol=False` = `tuple(doc.items())` 输出」写进结论。

**v2 的对应（本轮已补齐）**：v1 用 `protocol` 切换的正是**行容器**。v2 原来只有 `list`/`dict`/`index`
三种 shape，其中 `dict` 是**列式**（字段名 → 每行的值），与 v1 的逐行 dict 不同构。
本轮新增第四种 `output.shape="rows"`：**每行一个 `{字段名: 值}` dict，键序 = 字段声明序**。
四种 shape 实测产物：
| shape | 产物 |
|---|---|
| `list` | `[[0]]`（行列表） |
| `dict` | `{'h': [0]}`（**列式**：字段名 → 每行的值） |
| `index` | `{0: [0]}`（int 行下标 → 行值） |
| `rows` | `[{'h': 0}]`（**行式**：逐行 dict，即 v1 `protocol: False`） |

`schemas.EX4` 用 `shape="rows"`，实测与 v1 的 `g.result['docs']`（10 个行 dict）
**逐 dict、逐键、逐值相等**；测试 `test_ex4_v2_rows_shape_is_exactly_equal` 断言这一点。

> **实现备注**：`rows` 只在 `dcg/codegen.py` 的 `finish()` 里换尾部返回表达式
> （`return [{k:r[i] for i,k in enumerate([...字段名...])} for r in _o]`），
> 行的字面量仍是 list —— 刻意不改 `body()`，因为 `_unbind_trivial` 会把 dict 键里
> 引号内的字段名一起替换坏。

## 6. 例5 —— 三个字段可直译，包括**运行期动态键**（本轮已补齐）

v1 例5 真跑产物：`([[14528086, 'W', None]], [])`。

> **修正 1（2026-10-17）**：本节初版写的是「`$.data.ID` 是深路径，v2 明令拒绝 ⇒ 整例无法表达」。
> 这个归因是**错的**，它把 `expr`（JSONata / 反引号）侧的限制当成了全局限制。实测：
> `parse_value_path("$.data.ID")` 通过，`dcg.validate()` 通过，generate + exec + run
> 得到的行与 v1 完全一致。契约 §6.1 只约束**表达式层**；`value` 侧任意深度都接受，
> 并且这是**已文档化的能力**（CONTRACT.md §8 O-4、README §3.3）。
>
> **修正 2（2026-10-17 同日）**：本节随后把第三字段判成「无法等价、只能用常量键复现」。
> 这也不对 —— 修掉 `pyseg` 的一个 bug 之后，动态键可以原样写进反引号段，例5 现已
> **逐行结果完全等价**（不是复现）。

| v1 写法 | `expr`（JSONata）侧 | `value`（路径）侧 |
|---|---|---|
| `$.data.ID`（两符号段） | ❌ `Unsupported: 第 2 列: 深路径 [data,ID] 不可逐行编译（契约 §6.1）` | ✅ 接受 → `R['data']['ID']` |
| `$.data.TASK_TYPE[0]` | ❌ 同上（`[data,TASK_TYPE,0]`） | ✅ 接受 → `R['data']['TASK_TYPE'][0]` |
| `` $.data.f'AIRPORTIATA_{len(IATA)}' `` | ❌ 语法错误（`多余内容: "'AIRPORTIATA_{len(IATA)}'"`） | ❌ 语法错误（f-string 不是路径语法） |
| `_field: {'IATAS': '$.data.AIRPORTIATA_1,$.data.AIRPORTIATA_2,...'}`（逗号多值） | ❌ 语法错误（`多余内容: ','`） | ❌ 语法错误（v2 无「一个别名多个路径」） |

**第三字段的求值时机**：v1 生成码里它是（冻结产物的 `generated_source` 第 8 行）：

```
VAR_1[f"AIRPORTIATA_{len(data['data']['AIRPORTIATA'])}"]
```

即**运行期按数据算出来的成员名**。v2 的反引号段可以做同一件事：

```python
"Planned_Arrival_Time": {
    "type": "str",
    "expr": "`$.data['AIRPORTIATA_' + str(len($.data.AIRPORTIATA))]`",
}
```

生成码是 `_data['AIRPORTIATA_' + str(len(_data['AIRPORTIATA']))]`，与 v1 同义。
`schemas.EX5` 就是它，实测与 v1 `[[14528086, 'W', None]]` 逐行相等。

> **踩到的实际 bug**：v2 原来把反引号里的**单段链** `` `$.data` `` 和**裸名**混为一谈
> （`dcg/jx/compiler.py::analyze_py_segment` 把它记进 `free_field_names`），
> `pyseg()` 于是报 `SchemaError: 裸名 'data' 不是本次 schema 声明的字段`。
> 修法是在 `pyseg()` 里跳过「已经作为链替换过的名字」。
> 顺带说明：`` `$.data.AIRPORTIATA` `` 这类**多段**链一直能过，只有单段链踩坑。

**规则等价而非数据复现的证据**：`test_ex5_dynamic_key_is_rule_equivalent_not_a_constant`
构造一行 `AIRPORTIATA == 'UR'`（长度 2）的合成数据，断言 v2 取到 `AIRPORTIATA_2`
而不是本份冻数据里的 `AIRPORTIATA_3` —— 换成常量键这个测试会立刻失败。

另有一处**语义差异（非缺陷）**：例5 的 `filter: 'Flight_ID'` 在 v1 里是**字段名**，
经 struct 规则解析成 `$.data.ID`，生成码 `datas = [data for data in datas if data['data']['ID']]`
即「按该字段的真值过滤行」；v2 的 `output.filter` 是**布尔**（是否整表过滤）。
名字相同、语义不同，迁移时必须逐个确认。本例只有 1 行且 `ID` 非空，过滤与否结果相同。

## 7. 例6 —— `show: False` 规格缺口（**v2 无对应物**）

例6 只 parse、不产出（该例没有 `datas`）。其中字段
`{'name': '_K_QV_1', 'show': False, 'ext': 'key'}` 的 v1 语义是：
**算出这个字段、但把它排除在输出行之外**（可用于给后续 `#.` 引用提供一个不对外暴露的中间量）。
实测 `_K_QV_1` 在例6 生成码里被 `key = _key` 之后就没有再被引用，所以此例中它是死计算；
但**机制本身**是「隐藏中间量」。

**v2 的实测行为**：
```
SchemaError: out.fields.hidden: 未知键: show
```
即 v2 **显式拒绝**（带 JSON 路径），不是静默忽略 —— 这点比静默降级好，仍然是**能力缺口**。

**归类（Lead 已裁决：算 v2 的显式能力缺口，不作为 v2 需求）：**
`show: False` 在 v2 里**没有等价机制** —— v2 目前**没有**"算出但不输出"的机制，
这是**刻意的取舍：v2 要求 schema 即输出契约**（`out.fields` 里的每个字段都是产物的一部分）。
v2 的选择是**显式拒绝**（`SchemaError: 未知键: show`，带 JSON 路径），
而不是像 v1 那样悄悄不输出 —— 这符合本项目「绝不静默降级」的原则。

> **v2 的立场（这一句是结论，不是猜测）**：v2 不是"忘了实现 `show`"，而是**取消了"算出但不输出"这个概念**：
> `out.fields` 就是产物契约，**出现在 schema 里的字段一定在产物里**。
> 因为 v1 的 `show:False` 语义是"这个字段有名字、可被其它字段引用、但不进输出"，
> 而 v2 里**任何字段都可以被其它字段引用**（裸标识符 = 字段，`CONTRACT.md` §1），
> 所以保留 `show` 只会多出一个不会被输出的字段定义 —— 那是**隐藏状态**，
> 也正是 v1 里最难迁移的东西（例6 的 `_K_QV_1` 就是一条死计算）。

**等价手段（迁移者最需要的一条）**：v1 里"算出中间值但不输出"最常见的真实动机是
**表达式太长、想拆开**（而不是真的需要一个隐藏名字）。v2 的做法是 **2 次 build** ——
第 1 个 build 把中间值作为正常字段输出，第 2 个 build 以第 1 个的产物为输入。
**不要**为此在 v2 里加隐藏字段：`out.fields` 是契约，加进去就等于改了产物。

**三条迁移路径**（都不改请求语义）：

| 路径 | 做法 | 适用 |
|---|---|---|
| (a) 内联到引用点 | 把该字段的表达式直接写进真正引用它的字段（`ext`/`expr` 里写表达式本身） | 该"隐藏字段"只被少数几处引用时**首选** |
| (b) 接受它成为真字段 | 直接放进 `out.fields`，让它出现在输出行里 | 只在下游确实需要这个值时可接受 —— 会**改变产物** |
| (c) 拆解复用 | 若是"算中间量给后面复用"，把表达式写进引用点，或**拆成两次 build** | 复杂表达式的复用 |

`_K_QV_1` 在例6 里 `key = _key` 之后**没有再被引用**，所以这一例是**死计算**，
迁移时直接删掉该字段即可（路径 (a) 的退化情形）；但**机制本身**在别的 schema 里可能真的被需要。

同一例还暴露两个 v2 的**语义差异**：
- v1 的 `'index': {'key': 'merge', 'number': ''}`（按 key 建索引并 merge 去重）在 v2 无对应键（见 §3 D-C）。
- 例6 的 `_field` 里 `_key → $.10`、`_xkey → $.11`：v2 无 `_field` 层，直接写 `$.10`。

## 8. 逐例「v1 期望输出 / v2 实际输出 / 是否对等 / 不对等原因」一览

| 例 | v1 期望输出（真跑冻结） | v2 实际输出 | 对等 | 不对等原因 |
|---|---|---|---|---|
| 1 | `{'doc': [[[0,1,2,[1,2,3]],1,2,0,'12',3,3,125,5], … 10 行]}` | 同样 10 行 × 9 字段（裸列表） | ✅ 值对等 | 仅容器不同（v1 帧 dict） |
| 2 | `{'docs': [], …}` + 10 行进错误桶 | `docs` 组 10 行 × 15 字段；整篇 11 个顶层段落 / 19 个段落由多产物组装 | ✅ **结果一致（两段式）** | v1 `CYTHON_MODE` 缺陷导致 as-written 丢数据；与 v1 关 `CYTHON_MODE` 后的冻结构建逐值相等（`docs` 组 1 个 schema，整篇 12 产物 + 6 常量 + 组装层） |
| 3 | `{'docs': [[0,0,0],[2,-1,1],[2,0,2],[3,3,3],[4,-4,4],[5,5,5],[6,-6,6],[7,7,7],[8,8,8],[9,9,9]]}` | 同上 10 行 × 3 字段 | ✅ 值对等 | 仅容器不同；v1 侧需 `FREE_MODE=True` |
| 4 | `{'docs': [{'hour':[0,1,2],'hourA':'a0','hourB':0,'hourC':0}, … 10 个]}` | 同样 10 个逐行 dict（`shape="rows"`） | ✅ **完全等价** | —（本轮新增 `rows` 形状） |
| 5 | `[[14528086, 'W', None]]` | `[[14528086, 'W', None]]`（`EX5`，反引号动态键） | ✅ **完全等价（规则等价）** | —（本轮修复 `pyseg` 单段链） |
| 6 | 无产物（`NameError: name 'chain' is not defined`） | `shape="index"` 可用，但无 `key/merge` 语义、无 `show` 机制 | ❌ 无法对等 | v1 该形状从未能跑；v2 语义不同 |

**统计：6 例中 5 例结果完全一致（例1、例2、例3、例4、例5 —— 例2 走「多产物 + 组装层」，不是单个 schema），1 例无 v1 产物可对（例6）。**

## 9. 诚实度声明

- 六例的 v1 期望**全部**来自真跑 v1 引擎（`bench/_v1bridge.py` 是唯一入口，未复制 v1 源码），
  没有任何一条是手抄或推测的。
- 六例 as-written 都**没有抛异常**；`NameError`/`TypeError` 只在**指定配置**下复现，
  每条都附了配置与原文。凡"条件成立"的写法都在上面写明条件。
- 例2 走的是「12 个 v2 产物 + 6 个常量段落 + 一层组装」的两段式迁移，**结果**与 v1 逐值一致，
  但**不声称 v2 一个 schema 就能发出整篇异构文档**（v2 没有 `shape="document"`）；例6 没有 v1 产物可对。
  未对等的部分**不声称已支持**。
  （例4、例5 已从「部分」升级为**完全等价**：`shape="rows"` + `pyseg` 单段链修复。）
- 无法对等的地方没有用 `pytest.skip` 掩盖：15 个对等测试 + 4 个可追溯性测试**全部真跑**
  （`pytest tests/examples` = 17 passed，0 skipped、0 xfail），
  其中 `test_ex5_expr_side_rejects_the_deep_path_the_example_needs` 断言的正是**能力边界本身**
  （`expr` 侧不接受深路径），而 `test_ex4_v2_rows_shape_is_exactly_equal`、
  `test_ex5_value_side_accepts_the_deep_path_and_matches_v1`、
  `test_ex5_dynamic_key_is_rule_equivalent_not_a_constant` 断言的是**等价性本身**。
- **一处抓到过、但不可复现的观察**（原始脚本是一次性的，已删除，输出留在此处）：
  在 parser-dev 的编辑窗口内曾抓到一份自相矛盾的生成码 —— 签名是 `def run(_d):` / `_d=_d['index']`，
  行循环却写 `for R in _in:`，运行期 `TypeError: 'function' object is not iterable`
  （`_in` 撞上了 `dcg.runtime` 经 `import *` 导出的 `$in` 运算符）。
  随后 **3 个独立进程 × 每个 schema 生成 5 次**全部 `distinct_sources=1`、参数名与循环名一致 →
  判定为**编辑中途的瞬时快照**，不作为 v2 缺陷记录。
  留一条的原因：`_in` 撞 `import *` 是真实的命名陷阱，Lead 已把「生成器里行循环名与签名参数名必须同源」
  加进 parser-dev 的自检清单。

## 10. 性能：只有**方向**与**归因**是结论（绝对数字与 5% 阈值判定都不可用）

性能数字与上面的迁移对等结论**解耦**：对等结论是"产物对不对"，性能是"快不快"，
后者受本机并发负载影响巨大（同一份 v1 代码十几分钟内单例漂移 **+50%**：0.520 → 0.747 → 0.780 µs/行）。

### 10.1 契约 §5（`v2 ≤ 1.05× 手写参照`）：wall-clock = `INCONCLUSIVE`，CPU 口径 = **`MET`（余量仅 1–4%）**

静默窗口 W2 在**同一 revision `A40457511277`** 上跑了三次 `--final`，
`ratio_v2_over_ref` = **1.117 / 0.901 / 0.937**，其中 Run A 与 Run C **都自证进程表干净**却给出
**相反判定**。Lead 的 A/A 噪声底标定（把**同一个逐字相同**的函数放进两个 slot 交替测 15 轮：
单变体自身 spread 39.3% / 67.7%，两个 slot 的"比值"`ratio(med) 0.974 / ratio(min) 1.020 / ratio(max) 1.168`）
证明 **wall-clock 在本机没有 5% 分辨率**。

> **wall-clock 口径**记为 `INCONCLUSIVE (wall-clock has no 5% resolution; see noise-floor calibration)`。
> **既不能据此宣称达标，也不能据此宣称不达标。** 1.117 不是结论，0.937 也不是结论。

**W3 改用 CPU 时间口径后可以判定**（`time.process_time()`、K=8 摊销 Windows ~15.6 ms 粒度、
interleaved、reps=7 取 min、3 个 block、A/A 噪声底；计时器唯一实现 =
`bench/bench_row.py::measure_interleaved_cpu`）：

| 运行（同一 revision `A4045751`） | `x/hand` v2 per block | worst | A/A 噪声底 | 判决 |
|---|---|---|---|---|
| Lead 独立探针 | 0.965 / 0.991 / 0.965 | 0.991 | 0.0506 | `RESOLVED PASS` |
| 我的 `W3-…T01-36` | 1.008 / 1.026 / 0.991 | 1.026 | 0.0270 | `WITHIN_NOISE`（≠ 不达标） |
| 我的 `W3-…T01-41`（终值） | 1.000 / 1.000 / 1.013 | **1.013** | 0.0127 | **`RESOLVED` → `MET`** |

→ 诚实的说法只有一句：**v2 在这条负载上贴着 1.00×，最坏观测 1.026×，从未超过 1.05×，
但余量只有 1–4%、与 A/A 噪声底同量级** —— 报 `MET` 时必须同时给出 `margin` 与 `noise_floor`；
`WITHIN_NOISE` 是"这次判不出"，**不是**"没达标"。绝对 µs/行 两次运行就漂了 46%
（`hand` 1.113 → 0.762），跨会话更不可引用。

详见 `bench/bench_examples.md` §3.6 / §6.2.1 / §6.2.2。

### 10.2 唯一有判定力的性能信号：**样例负载上 v2 稳定慢 v1 的 1.25–1.34×**

三次 `--final` 的 `v1/v2` = **0.783 / 0.746 / 0.803**（三次同向），
**主因始终是例2 的等价 schema（`EX2_DOCS`，逐例比值 0.614–0.635）**。
归因超出噪声底两个数量级：每行 3×`_ad` + 4×`_ix_get` ≈ 1412 ns/行，v2−v1 差 ≈1640 ns/行
→ 差距基本来自 **runtime helper 调用本身**，不是 codegen 结构。

### 10.3 例2 上已确认的优化机会（**记录，不是缺陷，也不是"未达标"结论**）

`EX2_DOCS` 的 v2 生成码（329 字符）里仍有 **`_ix_get(hourX, 0)`**，对应字段
`hourY`（schema 里写 `"expr": "hourX[0]"`，v1 原文是 `#.hourX.0`）。
基址 `hourX` 的 FieldSpec 是 **`{"type": "list", "expr": "[1, 2, 3]"}`** ——
**它的取值表达式本身就是字面量数组**，所以 base 与下标**都静态可知**：
本可内联成裸下标（`[1,2,3][0]`，甚至常量折叠成 `1`），不需要运行期 helper。

> 口径校正（对 Lead m01011 措辞的精确化）：Lead 说这是"`const` 数组字面量"，
> 按 schema 实测它是 **`expr` 键**（值是字符串 `"[1, 2, 3]"`），**不是 `const` 键** ——
> `const` 在 v2 的 FieldSpec 里是另一个键（`EX2_DOCS` 的 `keyx` 才是 `"const": 100`）。
> 两者都是静态字面量，但对实现者的区别很重要：需要处理的是
> **`expr` 字段的表达式树是否被常量折叠**，而不是"`const` 字段怎么取下标"。

- **定性**：已确认的优化机会，**不在本轮修**（需解冻 `dcg/**`）；**不得**写成"未达标"或"缺陷"。
- **归谁**：`dcg/**` 侧，由 Lead 决定是否/何时解冻排期。

### 10.4 与已修那类的区别（避免后来人以为漏了一类）

| | 修好之前 | 现在 | 类别 |
|---|---|---|---|
| 例1（`EX1`） | `_ad(_ix_get(hour,2),123)`、`_ix_get(_ix_get(hour,3),2)` | `_a[2]`、`_a[3][2]`（**已修**） | base `hour` = `{"type":"list","value":"$.5.a"}`：**读输入列的字段** + 常量下标 → 静态可判定（来自**类型声明 + 列绑定**） |
| 例2（`EX2_DOCS`） | `_ix_get(hourX, 0)` | 仍是 `_ix_get(hourX, 0)`（§10.3） | base `hourX` = `{"type":"list","expr":"[1, 2, 3]"}`：**表达式即字面量** → 静态可判定性来自**表达式树本身**，需另一条常量折叠规则 |

两类的共同点：**下标是常量**；区别在于**静态可判定性来自哪里**
（字段的**列绑定 + 类型声明** vs **表达式树里的字面量**）。
`_ix_get` 原本只为**动态下标**和 `a[0] == a`（对标量取下标等于自身）的语义准备。
