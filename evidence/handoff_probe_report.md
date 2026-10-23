> （历史记录，按用户 2026-10-23 决定保留）

# v2.1 变更语义边界验证报告（P1 handoff 独立复核）

- 验证者：edge-prober（独立复核，不改 `dcg/` 源码）
- 被测对象：工作区未提交的 v2.1 改动
  - `dcg/codegen.py`：`_never_undef` / `_bare_name_never_undef` / `_flat_row`
  - `dcg/schema.py`：位置行语法 `"row":"list"` → `type:"list"` + `fields`；移除 `items`
- 探针脚本：`bench/probe_v21_handoff.py`（`dcg.generate(schema)` → `exec(compile(...))` → `ns["run"](rows)` → 值断言）
- 原始输出：`evidence/v21_handoff_probe.txt`
- 环境：Windows / Python 3.13.3 / 沙箱禁删目录（故探针不用 `tempfile`）

## 结论

**v2.1 三组改动本身通过验收：22 条边界用例中 v2.1 验收项（A/B/C/D/E/F 共 20 条）20 PASS、0 FAIL。**

FAIL 的 2 条（G1a/G1b）是**既有的、与 v2.1 无关的缺陷**（v2.1 之前 `optional` 的守卫就只发在顶层），
本次一并记录，不作为 v2.1 的阻塞项 —— 除非 Lead 认为应把"嵌套 optional"并入 v2.1 范围。

```
合计 22 条，PASS 20，FAIL 2
FAIL：G1a、G1b（G 组 = 既有缺陷探针，不在 v2.1 改动范围内）
```

## 各组覆盖与结论

### A. 位置行 `type:"list"` + `fields`（`_rl` 保位）

| 用例 | 输入 | 期望 | 实际 | 结果 |
|---|---|---|---|---|
| A1 | 位置行内 `optional:true, type:"any"` 字段，行里**真的缺**该键 | `[[None, 's']]` | `[[[None, 's']]]`（外层为 `shape=list` 的行） | PASS |
| A2 | `second` 的 `expr` 前向引用同层 `first` | `[[[1, 101, 2], 1]]` | 同左 | PASS |
| A3 | 两个 required `int64` 字段（类型可证） | 发列表字面量 `[R[0],R[1]]` | 源含列表字面量、无 `_rl` | PASS |
| A4 | 位置行里再嵌位置行，内层 optional 缺键 | `[[[1, [None, 3]]]]` | 同左（`_rl` 递归保位） | PASS |

要点：`_rl` 只在"位置行里存在可能 undefined 的分量"时才发；全可证时直接发 Python 列表字面量（热路径收益），
两者结构完全一致 —— 这正是 `_never_undef` 的 `all(...)` 判定的正确性依据。

### B. 具名对象 `type:"object"`（`_ob` 丢键）

| 用例 | 输入 | 期望 | 实际 | 结果 |
|---|---|---|---|---|
| B1 | 行里缺 `a` | `[[{'b': 's'}]]`（键被丢弃，**不是** `{'a': None}`） | 同左，源含 `_ob` | PASS |

A1 与 B1 是同一份"缺键数据"喂给两种行类型，产出分别是"保位 None"与"丢键"，
证明 `_rl` / `_ob` 的分派点在 `_emit_object` 里正确按 `type` 分开。

### C. schema 层：`items` 移除 / `row` 兼容别名

| 用例 | 输入 | 期望 | 实际 | 结果 |
|---|---|---|---|---|
| C1a | `out.fields.a.items` | `SchemaError`，path `out.fields.a.items` | 同左 | PASS |
| C1b | `items` + `value` 并存 | 同上（不因 `value` 存在而漏检） | 同左 | PASS |
| C1c | 嵌套 `fields` 内的 `items` | path `out.fields.a.fields.x.items` | 同左 | PASS |
| C2 | `row:"list"`/`row:"object"` 别名；只给 `row` 不给 `fields` | 别名归一成 `type` 且不残留 `row`；旧写法报明确 `SchemaError` | 同左 | PASS |

### D. `_never_undef` / `_bare_name_never_undef`（py 段裸名）

| 用例 | 输入 | 期望 | 实际 | 结果 |
|---|---|---|---|---|
| D1 | 位置行内 `b: {"expr": "`str(a)`"}` 引用同层 `optional:true, type:"any"` 的 `a`，行里缺该键 | `a` 可能 undefined → 该行必须走 `_rl`，裸名读到 None，**不得**漏哨兵 | 走 `_rl`；`b='1'` 那行；缺键行无哨兵 | PASS |
| D2 | 同结构但 `a` 是 required `int64` | 可证非 undefined → 裸字面量，不发 `_rl` | 源无 `_rl`，`out=[[[5,'5']]]` | PASS |
| D3 | 裸名引用带 `default` 的兄弟 | `_df` 兜底，缺键给 7 | `[[{'a':7,'b':'7'}]]` | PASS |
| D4 | 裸名引用**外层**（顶层）optional 字段 | 顶层有 `None if _t is _U` 守卫，读到 None | 缺键行 `'None'` | PASS |

要点：D1 与 D2 的产物形态差异（`_rl` vs 裸列表字面量）证明 `_bare_name_never_undef` 的
`optional 且无 default → 可能 undefined` 判定确实驱动了 `_rl` 的选择 —— 不是"永远保守套 `_rl`"的假通过。

### E. `_flat_row`（单键 dict/rows 裸存）

| 用例 | 输入 | 期望 | 实际 | 结果 |
|---|---|---|---|---|
| E1 | `shape:"dict"`，单字段 `a` | 与旧转置式 `{'a': [r[0] for r in rows]}` 逐值相等 | `[]`→`{'a':[]}`；`[[0]]`→`{'a':[0]}`；`[[1],[2],[3]]`→`{'a':[1,2,3]}`；`[[0],[5],[None]]`→`{'a':[0,5,None]}` | PASS |
| E2 | `shape:"rows"`（含 `single:true`） | 与旧式 `[{'a': r[0]} for r in rows]` 相等；`single` 时 `_o and {'a':_o[0]}` | `[]`→`[]`；`[[0]]`→`[{'a':0}]`；`single []`→`[]`；`single [[0]]`→`{'a':0}` | PASS |
| E3 | `shape:"dict"` + 单字段 optional 缺键 | 保位 `None`，不丢行 | `{'a': [1, None, 3]}` | PASS |
| E4 | `shape:"list"` 单字段 | **不**走 `_flat_row`，行必须仍包一层 list | `[[1],[2]]`，源含 `_o.append([R[0]])` | PASS |

E1 的 `[[0], [5], [None]]` 覆盖了"假值不能当缺失"（0 必须保留）；E4 覆盖了"list 形状行本身是 list，
裸存会改结构"的边界 —— 这正是 `_flat_row()` 只对 `("dict","rows")` 生效的原因。

### F. 行过滤与守卫并存

`[[1], [None], [0]]` 三行全保留：可证为真、None、以及 **falsy 的 0**（`0` 是已存在的值，不是缺失）。

## 已发现的缺陷（既有，非 v2.1 引入）

### G1 嵌套 `fields` 内的 `optional` 字段缺键 → `KeyError`（应为 `None`）

最小复现：

```python
schema = {"name": "t", "input": {"source": "rows"},
          "output": {"shape": "list", "filter": False},
          "out": {"type": "object", "fields": {
              "q": {"type": "list", "fields": {           # 换成 "object" 同样复现
                  "a": {"type": "int64", "optional": True, "value": "$.x"},
                  "b": {"type": "str", "value": "$.y"}}}}}}
run([{"y": "s"}])        # 行里真的没有 'x'
```

- 期望（README §2.3 / `JSONata 语法.md`）：`optional` 缺值 → `null`，不抛异常 → `[[[None, 's']]]`
- 实际：`KeyError: 'x'`
- 产物：`_o.append([_rl([R['x'],R['y']])])` —— 读了裸键 `R['x']`

成因：`_never_undef` 的 `value` 分支是 `t is not None and t != "any"`，
**没把 `optional` 纳入考虑**，于是声明了具体类型的 optional 字段被判"必然存在"→ 裸读。
而"给 None 的守卫"只在 `body()` 里对**顶层**字段生成（codegen.py:1397-1413），
`_emit_object` 内的 optional 字段没有等价守卫。

对照（G2，PASS）：同一位置把 `type` 换成 `"any"`，生成 `_rl([_ix_get(R,'x'),...])`，缺键行正常输出 `None` ——
证明缺陷只出在"声明了具体类型 + optional"这一组合，不是 `_rl`/`_ob` 的问题。
若 v2.1 的 `_never_undef` 被期望"顺带修复 optional 语义"，则此处是它没覆盖到的角落；
若 v2.1 只承诺`_rl` 不丢位，则这条属于既有缺陷，单独开单。

### H1 顶层 `optional:true` + 嵌套 `fields` 的守卫是死代码（哨兵不泄漏，但语义可疑）

```python
out.fields.a = {"type": "object", "optional": True, "fields": {"u": {"type": "any", "optional": True, "value": "$.u"}}}
```

产物 `_t1={'u':_ix_get(R,'u')}` / `a=None if _t1 is _U else _t1`：`_t1` 是 dict 字面量，
**永远不是 `_U`**，守卫恒假。实测缺 `u` 的行输出 `[[{}, 9]]`（`_ob` 把哨兵键丢了，所以没有外泄），
但"optional 的 None 语义"在这种结构上并不生效。当前不产生错误值，记录备查。

## 探针自身的判定口径（避免假 FAIL）

1. `shape="list"` 的输出外层已经有「每行一个 list」，单字段行是 `[v]`，不是 `[[v]]`；
   期望值需按 shape 正确折算（E4 首轮就是这里写错了期望）。
2. `_emit_object` 的 `_flat_row` 判定只作用于 `objects`（顶层单键），不应期待嵌套行被摊平。
3. 裸名**前向引用**同层字段：`second` 引用声明在后的 `third` 会报 `SchemaError: 裸名 'second' ...`，
   这属于设计如此（兄弟字段拓扑序求值），A2 因此改成"引用声明在前的兄弟"。
4. 缺键输入必须真的从行数据里缺键（`{"y":"s"}`），而不是给错类型的值（类型错是另一类真实异常）。
5. 缺陷判定不写死异常类型：只断言"缺键行必须产出结果且该位置为 None"，抛任何异常即 FAIL ——
   这样修复方既可以用 `_ix_get + 守卫`、也可以改 `_never_undef`，都不会被探针的形状绑死。

## 回归

`python -m pytest -q -k "not TestBuild and not TestCli and not EfficiencyHarness"`：
**828 passed / 4 skipped / 1 error / 14 deselected**。
唯一的 error 是 `tests/test_migrate.py::TestSingleMode::test_cli_single_true_runs`（PermissionError，
沙箱禁止写临时目录，与代码无关；`TestBuild`/`TestCli`/`EfficiencyHarness` 因同样的沙箱限制被排除）。

复现命令：

```powershell
cd C:\Users\black\Desktop\DCG
python -X utf8 bench\probe_v21_handoff.py
```
