# DCG v2 · JSONata 语法样例与实测行为（可执行文档）

> 任务：task-6（语法样例）＋ task-7 的 jx 侧（`py{}` 编译）｜ 生成源：`examples/jx/*.jx`（15 个文件、216 条例样） ｜ **复现日期：2026-10-17 01:04 (Asia/Shanghai)** ｜ 生成器：`tests/test_jx_examples.py`

这份文档的**每一条例样都被真实执行**：`dcg.generate(schema)` 生成 Python 源码 -> `exec` -> `run([row])`，断言的是运行期真实结果。文档由样例渲染而来，`tests/test_jx_examples.py` 断言磁盘文档与渲染结果**逐字节相等**，所以文档不可能与样例漂移。

重新生成：

```sh
python tests/test_jx_examples.py      # 重新渲染 docs/jsonata_syntax.md
python -m pytest tests/test_jx_examples.py -q   # 逐条例样执行 + 文档一致性
```

## 时效说明（必读）

以下分类是 **2026-10-17 01:04 (Asia/Shanghai)** 在 `dcg/` 当前实现上**实测**的结果，**不是**契约保证的语义。

**已修复（回归保护，`state: fixed`）** —— 2026-10-17 的引擎修复把原先 21 条缺陷里的 **17 条修好了**，它们已按**正确值**重新冻结在 `14_fixed_regressions.jx`，并与官方 `jsonata-python` **逐例交叉复核一致**。最重要的一条：内部 undefined 哨兵不再是字面量 `0`（因此不再被 CPython 小整数缓存骗到），**合法的 `0` 与布尔 `false` 不再被当成 undefined**；`null` 字面量、`? :` 的假分支、`$number`、`in` 运算符也都恢复正常。**这些条目断言的是正确值：将来再退化就会变红。**
- 布尔字面量现在是真正的 Python `bool`（`True`/`False`），不再退化成 int `1`/`0`：`$type(true)` = 'boolean'、`$string(true)` = 'true'（`01_literals.jx` 的两例带 `type: bool`）。

**仍未修复（`state: defect`）** —— 只剩 4 条，全部在 `13_known_defects.jx`，那里**故意断言当前的错误行为**，修好后就会变红：反引号段里的 `math.*`（生成码没有 import）、`null` 做下标、`$round` 的 banker's rounding、`$count` 对标量返回 undefined。

**未实现（`state: gap`）** —— jx 接受但 `dcg.generate` 抛 `Unsupported` 的，属于 codegen 端尚未实现（见附录 A），不是语法错。

## 两条最容易踩的坑

1. **下标里的裸名是字段引用，不是字符串键**：`$.a[b]` ≡ `$.a[$.b]`（`b` 需在 schema 中声明，否则 **build 期** `SchemaError` 并列出可用字段）；要按下标字符串取键请写 `$.a['b']`。同一条规则在顶层就成立：裸名 `hourB` = 已声明字段，`$.hourB` = 直接读行。
2. **`$` 变量不绑定**：jx 不实现 JSONata 的变量绑定，`$i` 退化为同名字段 `i`（M0/CAP 工作负载依赖这一点）。

## `py{}` 多语句代码块（task-7）

`py{ let t = $.a * 1.2; return int(t) }` 是**行内多语句 Python**，已**端到端可用**。`compile_expr` 产出 IR 节点 `("pyblock", stmts_source, free_field_names, loc)`：`let` 局部被改名为 `__jx_<块序号>_<名字>`（`__jx` 是保留前缀，用户不能自己取）；codegen 把每个块落成**模块级命名辅助函数** `def _pyN(f1, f2, ...)`（契约 §4.3 指定形状，**不是** lambda/IIFE，定义在 `run()` 之外、每行只调用一次）。

IR 层节点形状、位置与全部拒绝项（含列号）的断言见 `tests/test_jx_py_block.py`，可执行样例见 `15_py_block.jx`（复现日期 2026-10-17 01:04 (Asia/Shanghai)）。

块内规则（契约 §3.7）：只能 `let NAME = EXPR;` + 结尾唯一一条 `return EXPR`；不引入循环/条件/异常语句（条件请用 Python 条件表达式 `a if c else b` 或 JSONata 的 `? :`）；不能 `import`/`def`/`class`/`lambda`；**不暴露裸字段名**（字段写 `$.名称`，块内局部必须先 `let`）；块内 `$.` 只支持单层字段或常量下标（与 JSONata 侧同一判据，契约 §6.1）。全部边界与确切消息、列号见 `15_py_block.jx` 与附录 A。

**`py{}` 不是沙箱**：它执行任意 Python 语句，只做**语法**约束，不做安全隔离。它与反引号单表达式段互补：一条表达式用 `` `EXPR` ``（见 `10_backtick.jx`），需要中间变量时用 `py{ let ...; return ... }`。

## 覆盖统计

| 文件 | 主题 | 例数 |
|---|---|---|
| [01_literals.jx](../examples/jx/01_literals.jx) | 字面量与基础值 | 12 |
| [02_arithmetic.jx](../examples/jx/02_arithmetic.jx) | 算术运算 | 11 |
| [03_compare_logic.jx](../examples/jx/03_compare_logic.jx) | 比较、逻辑与真值 | 13 |
| [04_concat.jx](../examples/jx/04_concat.jx) | 字符串连接 & | 5 |
| [05_paths.jx](../examples/jx/05_paths.jx) | 路径、下标与行内取值 | 14 |
| [06_string_funcs.jx](../examples/jx/06_string_funcs.jx) | 字符串行函数 | 11 |
| [07_number_funcs.jx](../examples/jx/07_number_funcs.jx) | 数值函数、类型与存在性 | 18 |
| [08_array_object.jx](../examples/jx/08_array_object.jx) | 数组与对象构造 | 6 |
| [09_conditional.jx](../examples/jx/09_conditional.jx) | 条件表达式 ? : | 6 |
| [10_backtick.jx](../examples/jx/10_backtick.jx) | 反引号内联 Python | 8 |
| [11_rejected.jx](../examples/jx/11_rejected.jx) | 编译期拒绝清单（契约 §3.5 / §6.1） | 31 |
| [12_gaps.jx](../examples/jx/12_gaps.jx) | jx 接受、但 codegen 尚未实现（端到端拒绝） | 17 |
| [13_known_defects.jx](../examples/jx/13_known_defects.jx) | 仍然存在的缺陷（运行期错误结果 / 异常） | 4 |
| [14_fixed_regressions.jx](../examples/jx/14_fixed_regressions.jx) | 已修复（回归保护） | 29 |
| [15_py_block.jx](../examples/jx/15_py_block.jx) | py{} 多语句代码块（task-7） | 31 |
| **合计** | 正常 166 ｜ 未实现(gap) 17 ｜ 仍存缺陷(defect) 4 ｜ 已修复(fixed) 29 | **216** |

---

### 01_literals.jx — 字面量与基础值

字面量直接进入 IR 的 ("lit", value, loc)，在生成码里原样出现。`true`/`false` 编成 Python 的 `bool`（IR 里就是 `"lit", True/False`），与官方 JSONata 一致：`$type(true)` = 'boolean'。布尔曾经被编成 int 1/0，已修，回归保护见 14_fixed_regressions.jx。

```text
# file: 01_literals.jx
# title: 字面量与基础值
# desc: 字面量直接进入 IR 的 ("lit", value, loc)，在生成码里原样出现。`true`/`false` 编成 Python 的 `bool`（IR 里就是 `"lit", True/False`），与官方 JSONata 一致：`$type(true)` = 'boolean'。布尔曾经被编成 int 1/0，已修，回归保护见 14_fixed_regressions.jx。
#
# 每条例样由 tests/test_jx_examples.py 真实执行：row 里的每个键会自动声明为输入字段
# （`value: $.key`），表达式编译成输出字段 `v`，然后真的 exec+run 一遍再断言。

=== 整数
expr: 42
expect: 42

=== 小数
expr: 3.5
expect: 3.5

=== 单引号字符串
expr: 'hi'
expect: 'hi'

=== 双引号字符串（JSONata 里与单引号等价）
expr: "hi"
expect: 'hi'

=== 布尔真
expr: true
expect: True
type: bool
note: IR 是 ("lit", True, …)，生成码里原样是 `True`。**曾经是缺陷**（早期实现编成 int `1`）：这里的 `type: bool` 就是回归保护，详见 14_fixed_regressions.jx。

=== 布尔假
expr: false
expect: False
type: bool
note: **曾经是缺陷**：早期实现编成 int `0`，与内部 undefined 哨兵 `_U = 0` 是同一对象，被所有 `is _U` 判定当成 undefined。已修，详见 14_fixed_regressions.jx。

=== 数组字面量
expr: [1, 2, 3]
expect: [1, 2, 3]

=== 嵌套数组
expr: [[1,2],[3]]
expect: [[1,2],[3]]

=== 空数组
expr: []
expect: []

=== 对象字面量
expr: {'k': 1}
expect: {'k': 1}

=== 多键对象
expr: {'k': a, 'j': b}
row: {'a': 1, 'b': 2}
expect: {'k': 1, 'j': 2}

=== 空对象
expr: {}
expect: {}
```

| # | 表达式 | row | 实测 | 说明 |
|---|---|---|---|---|
| 1 | `42` | `{}` | 42 |  |
| 2 | `3.5` | `{}` | 3.5 |  |
| 3 | `'hi'` | `{}` | 'hi' |  |
| 4 | `"hi"` | `{}` | 'hi' |  |
| 5 | `true` | `{}` | True | IR 是 ("lit", True, …)，生成码里原样是 `True`。**曾经是缺陷**（早期实现编成 int `1`）：这里的 `type: bool` 就是回归保护，详见 14_fixed_regressions.jx。 |
| 6 | `false` | `{}` | False | **曾经是缺陷**：早期实现编成 int `0`，与内部 undefined 哨兵 `_U = 0` 是同一对象，被所有 `is _U` 判定当成 undefined。已修，详见 14_fixed_regressions.jx。 |
| 7 | `[1, 2, 3]` | `{}` | [1, 2, 3] |  |
| 8 | `[[1,2],[3]]` | `{}` | [[1, 2], [3]] |  |
| 9 | `[]` | `{}` | [] |  |
| 10 | `{'k': 1}` | `{}` | {'k': 1} |  |
| 11 | `{'k': a, 'j': b}` | `{'a': 1, 'b': 2}` | {'k': 1, 'j': 2} |  |
| 12 | `{}` | `{}` | {} |  |

### 02_arithmetic.jx — 算术运算

二元算术在本行内求值（契约 §4.4 的专用 helper，无分派表）。任一侧 undefined 则传播 undefined；`null` 参与算术是**错误**而不是 undefined。

```text
# file: 02_arithmetic.jx
# title: 算术运算
# desc: 二元算术在本行内求值（契约 §4.4 的专用 helper，无分派表）。任一侧 undefined 则传播 undefined；`null` 参与算术是**错误**而不是 undefined。

=== 加法
expr: a + b
row: {'a': 1, 'b': 2}
expect: 3

=== 减法
expr: a - b
row: {'a': 7, 'b': 2}
expect: 5

=== 乘法
expr: a * b
row: {'a': 3, 'b': 4}
expect: 12

=== 除法
expr: a / b
row: {'a': 7, 'b': 2}
expect: 3.5

=== 取模
expr: a % b
row: {'a': 7, 'b': 2}
expect: 1

=== 结合力：乘法先于加法
expr: a + b * 2
row: {'a': 1, 'b': 2}
expect: 5

=== 括号
expr: (a + b) * 2
row: {'a': 1, 'b': 2}
expect: 6

=== 一元负号
expr: -a
row: {'a': 5}
expect: -5

=== 一元正号
expr: +a
row: {'a': 5}
expect: 5

=== 常量折叠（字面量算术）
expr: 2 + 3 * 4
expect: 14

=== null 参与算术是错误
expr: $.m + 1
row: {'m': None}
expect: raise:TypeError:$add
note: 与 undefined 不同：null 是**值**，参与算术必须报错（契约 §4.4 第 7 条）。
```

| # | 表达式 | row | 实测 | 说明 |
|---|---|---|---|---|
| 1 | `a + b` | `{'a': 1, 'b': 2}` | 3 |  |
| 2 | `a - b` | `{'a': 7, 'b': 2}` | 5 |  |
| 3 | `a * b` | `{'a': 3, 'b': 4}` | 12 |  |
| 4 | `a / b` | `{'a': 7, 'b': 2}` | 3.5 |  |
| 5 | `a % b` | `{'a': 7, 'b': 2}` | 1 |  |
| 6 | `a + b * 2` | `{'a': 1, 'b': 2}` | 5 |  |
| 7 | `(a + b) * 2` | `{'a': 1, 'b': 2}` | 6 |  |
| 8 | `-a` | `{'a': 5}` | -5 |  |
| 9 | `+a` | `{'a': 5}` | 5 |  |
| 10 | `2 + 3 * 4` | `{}` | 14 |  |
| 11 | `$.m + 1` | `{'m': None}` | TypeError: $add: 操作数必须是数值 | 与 undefined 不同：null 是**值**，参与算术必须报错（契约 §4.4 第 7 条）。 |

### 03_compare_logic.jx — 比较、逻辑与真值

比较与逻辑运算符走 runtime 的专用 helper。契约 §4.4：`=`/`!=` 遇 undefined 返回 False，大小比较遇 undefined 返回 undefined，`and`/`or` 按 JSONata 真值。

```text
# file: 03_compare_logic.jx
# title: 比较、逻辑与真值
# desc: 比较与逻辑运算符走 runtime 的专用 helper。契约 §4.4：`=`/`!=` 遇 undefined 返回 False，大小比较遇 undefined 返回 undefined，`and`/`or` 按 JSONata 真值。

=== 相等
expr: a = b
row: {'a': 1, 'b': 1}
expect: True

=== 不等
expr: a != b
row: {'a': 1, 'b': 2}
expect: True

=== 小于
expr: a < b
row: {'a': 1, 'b': 2}
expect: True

=== 小于等于
expr: a <= b
row: {'a': 2, 'b': 2}
expect: True

=== 大于
expr: a > b
row: {'a': 3, 'b': 2}
expect: True

=== 大于等于
expr: a >= b
row: {'a': 1, 'b': 2}
expect: False

=== 字符串比较
expr: 'a' < 'b'
expect: True

=== 字符串比较（反例）
expr: 'b' < 'a'
expect: False

=== 逻辑与
expr: a and b
row: {'a': True, 'b': False}
expect: False

=== 逻辑或
expr: a or b
row: {'a': False, 'b': True}
expect: True

=== 逻辑非
expr: not a
row: {'a': False}
expect: True

=== 逻辑非（真值取反）
expr: not a
row: {'a': True}
expect: False

=== 嵌套逻辑
expr: (a or b) and not c
row: {'a': False, 'b': True, 'c': False}
expect: True
```

| # | 表达式 | row | 实测 | 说明 |
|---|---|---|---|---|
| 1 | `a = b` | `{'a': 1, 'b': 1}` | True |  |
| 2 | `a != b` | `{'a': 1, 'b': 2}` | True |  |
| 3 | `a < b` | `{'a': 1, 'b': 2}` | True |  |
| 4 | `a <= b` | `{'a': 2, 'b': 2}` | True |  |
| 5 | `a > b` | `{'a': 3, 'b': 2}` | True |  |
| 6 | `a >= b` | `{'a': 1, 'b': 2}` | False |  |
| 7 | `'a' < 'b'` | `{}` | True |  |
| 8 | `'b' < 'a'` | `{}` | False |  |
| 9 | `a and b` | `{'a': True, 'b': False}` | False |  |
| 10 | `a or b` | `{'a': False, 'b': True}` | True |  |
| 11 | `not a` | `{'a': False}` | True |  |
| 12 | `not a` | `{'a': True}` | False |  |
| 13 | `(a or b) and not c` | `{'a': False, 'b': True, 'c': False}` | True |  |

### 04_concat.jx — 字符串连接 &

`&` 是"连接"而不是加法：两侧都会被转成字符串，undefined 与 `null` 都当空串（契约 §4.4 第 1 条）。

```text
# file: 04_concat.jx
# title: 字符串连接 &
# desc: `&` 是"连接"而不是加法：两侧都会被转成字符串，undefined 与 `null` 都当空串（契约 §4.4 第 1 条）。

=== 字面量连接
expr: 'a' & 'b'
expect: 'ab'

=== 数字自动转字符串
expr: 'n=' & a
row: {'a': 5}
expect: 'n=5'

=== 三目连接
expr: 'y' & a & 'z'
row: {'a': 1}
expect: 'y1z'

=== null 值当空串
expr: 'x' & $.m
row: {'m': None}
expect: 'x'
note: 契约 §4.4 第 1 条：undefined 不传播，当空串；生成码里 `_cc` 同时把 None 当空串。

=== null 在左侧
expr: $.m & 'x'
row: {'m': None}
expect: 'x'
```

| # | 表达式 | row | 实测 | 说明 |
|---|---|---|---|---|
| 1 | `'a' & 'b'` | `{}` | 'ab' |  |
| 2 | `'n=' & a` | `{'a': 5}` | 'n=5' |  |
| 3 | `'y' & a & 'z'` | `{'a': 1}` | 'y1z' |  |
| 4 | `'x' & $.m` | `{'m': None}` | 'x' | 契约 §4.4 第 1 条：undefined 不传播，当空串；生成码里 `_cc` 同时把 None 当空串。 |
| 5 | `$.m & 'x'` | `{'m': None}` | 'x' |  |

### 05_paths.jx — 路径、下标与行内取值

字段访问只支持单层 `$.name` 与数值下标链（`$.5.a.1`，M0/CAP 工作负载的实际形状）。从 root 起出现 ≥2 个符号段（`$.a.b`、`$.a.b.c`、`$.5.a.1.b`）按契约 §6.1 拒绝，要求改用嵌套 fields。

**下标里的裸名是字段引用，不是字符串键**：`$.a[b]` ≡ `$.a[$.b]`（`b` 需在 schema 中声明，否则 **build 期**报 `SchemaError` 并列出可用字段）；要按下标字符串取键请写 `$.a['b']`。这条与顶层规则一致——裸名 `hourB` = 已声明字段，`$.hourB` = 直接读行，**同一 token 的语义与它出现的语法位置无关**。

```text
# file: 05_paths.jx
# title: 路径、下标与行内取值
# desc: 字段访问只支持单层 `$.name` 与数值下标链（`$.5.a.1`，M0/CAP 工作负载的实际形状）。从 root 起出现 ≥2 个符号段（`$.a.b`、`$.a.b.c`、`$.5.a.1.b`）按契约 §6.1 拒绝，要求改用嵌套 fields。
# desc: **下标里的裸名是字段引用，不是字符串键**：`$.a[b]` ≡ `$.a[$.b]`（`b` 需在 schema 中声明，否则 **build 期**报 `SchemaError` 并列出可用字段）；要按下标字符串取键请写 `$.a['b']`。这条与顶层规则一致——裸名 `hourB` = 已声明字段，`$.hourB` = 直接读行，**同一 token 的语义与它出现的语法位置无关**。

=== 根路径字段
expr: $.name
row: {'name': 'n'}
expect: 'n'

=== 裸名即本行已声明字段
expr: name
row: {'name': 'n'}
expect: 'n'
note: 契约 §1 曾写"裸标识符不是字段"，Lead 已裁决以实测为准：裸名 = 本 schema 已声明字段（M0/CAP 的 `hourA + hourB` 依赖它）。

=== 数值段链（CAP 字段）
expr: $.5.a.1
row: {5: {'a': [10, 20]}}
expect: 20

=== 数值段 + 单个符号段
expr: $.5.a
row: {5: {'a': [10, 20]}}
expect: [10, 20]

=== 纯数值段
expr: $.5
row: {5: {'a': [10, 20]}}
expect: {'a': [10, 20]}

=== 常量下标
expr: $.a[0]
row: {'a': [7, 8]}
expect: 7

=== 裸名 + 常量下标
expr: a[0]
row: {'a': [7, 8]}
expect: 7

=== 变量作下标
expr: $.a[$i]
row: {'a': [10, 20], 'i': 1}
expect: 20
note: `$i` 在本行内退化为同名字段 i（jx 不实现真正的变量绑定）。

=== 路径作下标
expr: $.a[$.b]
row: {'a': [10, 20], 'b': 0}
expect: 10

=== 整行（根）
expr: $
row: {'a': 1}
expect: {'a': 1}

=== 变量写法取本行字段
expr: $i + 1
row: {'i': 1}
expect: 2

=== 下标里的裸名是字段引用
expr: $.a[b]
row: {'a': [10, 20], 'b': 1}
expect: 20
note: 等价于 `$.a[$.b]`。修复前这里被误判成深路径 `$.a.b` 而报 `Unsupported: 深路径 [a,b]`；`b` 未声明时现在是 **build 期** `SchemaError`（并列可用字段），不再是运行期静默 undefined。

=== 非 $ 基座的裸名下标同样是字段引用
expr: a[b]
row: {'a': [10, 20], 'b': 0}
expect: 10
note: 修复前被静默编成字符串键 `a["b"]`（`("index", a, ("lit","b"))`），拿到的是字典键而非字段值——契约 §3.6 禁止的静默降级。

=== 要按字符串键取下标请写引号
expr: $.a['b']
row: {'a': {'b': 7}}
expect: 7
note: 带引号的下标是**常量键**（`("index", _, ("lit","b"))`），与裸名字段引用分离——这正是 JSONata 的语义。
```

| # | 表达式 | row | 实测 | 说明 |
|---|---|---|---|---|
| 1 | `$.name` | `{'name': 'n'}` | 'n' |  |
| 2 | `name` | `{'name': 'n'}` | 'n' | 契约 §1 曾写"裸标识符不是字段"，Lead 已裁决以实测为准：裸名 = 本 schema 已声明字段（M0/CAP 的 `hourA + hourB` 依赖它）。 |
| 3 | `$.5.a.1` | `{5: {'a': [10, 20]}}` | 20 |  |
| 4 | `$.5.a` | `{5: {'a': [10, 20]}}` | [10, 20] |  |
| 5 | `$.5` | `{5: {'a': [10, 20]}}` | {'a': [10, 20]} |  |
| 6 | `$.a[0]` | `{'a': [7, 8]}` | 7 |  |
| 7 | `a[0]` | `{'a': [7, 8]}` | 7 |  |
| 8 | `$.a[$i]` | `{'a': [10, 20], 'i': 1}` | 20 | `$i` 在本行内退化为同名字段 i（jx 不实现真正的变量绑定）。 |
| 9 | `$.a[$.b]` | `{'a': [10, 20], 'b': 0}` | 10 |  |
| 10 | `$` | `{'a': 1}` | {'a': 1} |  |
| 11 | `$i + 1` | `{'i': 1}` | 2 |  |
| 12 | `$.a[b]` | `{'a': [10, 20], 'b': 1}` | 20 | 等价于 `$.a[$.b]`。修复前这里被误判成深路径 `$.a.b` 而报 `Unsupported: 深路径 [a,b]`；`b` 未声明时现在是 **build 期** `SchemaError`（并列可用字段），不再是运行期静默 undefined。 |
| 13 | `a[b]` | `{'a': [10, 20], 'b': 0}` | 10 | 修复前被静默编成字符串键 `a["b"]`（`("index", a, ("lit","b"))`），拿到的是字典键而非字段值——契约 §3.6 禁止的静默降级。 |
| 14 | `$.a['b']` | `{'a': {'b': 7}}` | 7 | 带引号的下标是**常量键**（`("index", _, ("lit","b"))`），与裸名字段引用分离——这正是 JSONata 的语义。 |

### 06_string_funcs.jx — 字符串行函数

契约 §3.4 白名单里的逐行函数（ROW_FUNCS）。每个函数在 runtime 里都有专用 helper，不做运行期分派。

```text
# file: 06_string_funcs.jx
# title: 字符串行函数
# desc: 契约 §3.4 白名单里的逐行函数（ROW_FUNCS）。每个函数在 runtime 里都有专用 helper，不做运行期分派。

=== 大写
expr: $uppercase(s)
row: {'s': 'ab'}
expect: 'AB'

=== 小写
expr: $lowercase(s)
row: {'s': 'AB'}
expect: 'ab'

=== 去首尾空白
expr: $trim(s)
row: {'s': '  x  '}
expect: 'x'

=== 长度
expr: $length(s)
row: {'s': 'abc'}
expect: 3

=== 子串（起点 + 长度）
expr: $substring(s, 1, 2)
row: {'s': 'abcde'}
expect: 'bc'

=== 子串（只有起点，取到末尾）
expr: $substring(s, 2)
row: {'s': 'abcde'}
expect: 'cde'

=== 切分
expr: $split(s, ',')
row: {'s': 'a,b,c'}
expect: ['a', 'b', 'c']

=== 连接
expr: $join(xs, '-')
row: {'xs': ['a', 'b']}
expect: 'a-b'

=== 包含（真）
expr: $contains(s, 'b')
row: {'s': 'abc'}
expect: True

=== 包含（假）
expr: $contains(s, 'z')
row: {'s': 'abc'}
expect: False

=== 数字转字符串
expr: $string(a)
row: {'a': 5}
expect: '5'
```

| # | 表达式 | row | 实测 | 说明 |
|---|---|---|---|---|
| 1 | `$uppercase(s)` | `{'s': 'ab'}` | 'AB' |  |
| 2 | `$lowercase(s)` | `{'s': 'AB'}` | 'ab' |  |
| 3 | `$trim(s)` | `{'s': '  x  '}` | 'x' |  |
| 4 | `$length(s)` | `{'s': 'abc'}` | 3 |  |
| 5 | `$substring(s, 1, 2)` | `{'s': 'abcde'}` | 'bc' |  |
| 6 | `$substring(s, 2)` | `{'s': 'abcde'}` | 'cde' |  |
| 7 | `$split(s, ',')` | `{'s': 'a,b,c'}` | ['a', 'b', 'c'] |  |
| 8 | `$join(xs, '-')` | `{'xs': ['a', 'b']}` | 'a-b' |  |
| 9 | `$contains(s, 'b')` | `{'s': 'abc'}` | True |  |
| 10 | `$contains(s, 'z')` | `{'s': 'abc'}` | False |  |
| 11 | `$string(a)` | `{'a': 5}` | '5' |  |

### 07_number_funcs.jx — 数值函数、类型与存在性

数值/类型函数的逐行形态。**注意**：早期实现里整数 0 与布尔 false 会与内部 undefined 哨兵 `_U = 0` 撞车（CPython 小整数缓存使 `0 is _U` 为真），**已修**（哨兵改为唯一对象），回归保护见 14_fixed_regressions.jx；本文件因此可以放 `0` 相关例。

```text
# file: 07_number_funcs.jx
# title: 数值函数、类型与存在性
# desc: 数值/类型函数的逐行形态。**注意**：早期实现里整数 0 与布尔 false 会与内部 undefined 哨兵 `_U = 0` 撞车（CPython 小整数缓存使 `0 is _U` 为真），**已修**（哨兵改为唯一对象），回归保护见 14_fixed_regressions.jx；本文件因此可以放 `0` 相关例。

=== 绝对值
expr: $abs(n)
row: {'n': -3}
expect: 3

=== 下取整
expr: $floor(n)
row: {'n': 3.7}
expect: 3

=== 上取整
expr: $ceil(n)
row: {'n': 3.2}
expect: 4

=== 四舍五入
expr: $round(n)
row: {'n': 3.5}
expect: 4
note: 本例恰好正确；`$round(2.5)` 会暴露 Python 的 banker's rounding（见 13_known_defects.jx）。

=== 平方根
expr: $sqrt(n)
row: {'n': 9}
expect: 3.0

=== 幂
expr: $power(a, b)
row: {'a': 2, 'b': 10}
expect: 1024

=== 类型：数字
expr: $type(a)
row: {'a': 1}
expect: 'number'

=== 类型：字符串
expr: $type(a)
row: {'a': 's'}
expect: 'string'

=== 类型：数组
expr: $type(a)
row: {'a': [1]}
expect: 'array'

=== 类型：对象
expr: $type(a)
row: {'a': {'k': 1}}
expect: 'object'

=== 类型：null
expr: $type($.m)
row: {'m': None}
expect: 'null'

=== 布尔化
expr: $boolean(a)
row: {'a': 1}
expect: True

=== 逻辑非
expr: $not(a)
row: {'a': 1}
expect: False

=== 存在性
expr: $exists(a)
row: {'a': 1}
expect: True

=== 计数（字面量数组）
expr: $count([1, 2, 3])
expect: 3

=== 计数（字段数组）
expr: $count(xs)
row: {'xs': [1, 2, 3]}
expect: 3

=== 类型：零（哨兵回归保护）
expr: $type(a)
row: {'a': 0}
expect: 'number'

=== 布尔化：零
expr: $boolean(a)
row: {'a': 0}
expect: False
```

| # | 表达式 | row | 实测 | 说明 |
|---|---|---|---|---|
| 1 | `$abs(n)` | `{'n': -3}` | 3 |  |
| 2 | `$floor(n)` | `{'n': 3.7}` | 3 |  |
| 3 | `$ceil(n)` | `{'n': 3.2}` | 4 |  |
| 4 | `$round(n)` | `{'n': 3.5}` | 4 | 本例恰好正确；`$round(2.5)` 会暴露 Python 的 banker's rounding（见 13_known_defects.jx）。 |
| 5 | `$sqrt(n)` | `{'n': 9}` | 3.0 |  |
| 6 | `$power(a, b)` | `{'a': 2, 'b': 10}` | 1024 |  |
| 7 | `$type(a)` | `{'a': 1}` | 'number' |  |
| 8 | `$type(a)` | `{'a': 's'}` | 'string' |  |
| 9 | `$type(a)` | `{'a': [1]}` | 'array' |  |
| 10 | `$type(a)` | `{'a': {'k': 1}}` | 'object' |  |
| 11 | `$type($.m)` | `{'m': None}` | 'null' |  |
| 12 | `$boolean(a)` | `{'a': 1}` | True |  |
| 13 | `$not(a)` | `{'a': 1}` | False |  |
| 14 | `$exists(a)` | `{'a': 1}` | True |  |
| 15 | `$count([1, 2, 3])` | `{}` | 3 |  |
| 16 | `$count(xs)` | `{'xs': [1, 2, 3]}` | 3 |  |
| 17 | `$type(a)` | `{'a': 0}` | 'number' |  |
| 18 | `$boolean(a)` | `{'a': 0}` | False |  |

### 08_array_object.jx — 数组与对象构造

数组/对象字面量内的子表达式逐行求值。契约 §4.4 第 5/6 条只跳过 undefined，**null 是值、必须保留**。

```text
# file: 08_array_object.jx
# title: 数组与对象构造
# desc: 数组/对象字面量内的子表达式逐行求值。契约 §4.4 第 5/6 条只跳过 undefined，**null 是值、必须保留**。

=== 引用字段构造数组
expr: [a, b]
row: {'a': 1, 'b': 2}
expect: [1, 2]

=== 数组内的复合表达式
expr: [a, b, a + b]
row: {'a': 1, 'b': 2}
expect: [1, 2, 3]

=== 对象字段
expr: {'k': a}
row: {'a': 1}
expect: {'k': 1}

=== 对象多键
expr: {'k': a, 'j': b}
row: {'a': 1, 'b': 2}
expect: {'k': 1, 'j': 2}

=== null 值保留在数组里
expr: [$.m, a]
row: {'m': None, 'a': 1}
expect: [None, 1]
note: 只有 undefined 会被跳过；null 参与构造。

=== 对象值为表达式
expr: {'sum': a + b}
row: {'a': 1, 'b': 2}
expect: {'sum': 3}
```

| # | 表达式 | row | 实测 | 说明 |
|---|---|---|---|---|
| 1 | `[a, b]` | `{'a': 1, 'b': 2}` | [1, 2] |  |
| 2 | `[a, b, a + b]` | `{'a': 1, 'b': 2}` | [1, 2, 3] |  |
| 3 | `{'k': a}` | `{'a': 1}` | {'k': 1} |  |
| 4 | `{'k': a, 'j': b}` | `{'a': 1, 'b': 2}` | {'k': 1, 'j': 2} |  |
| 5 | `[$.m, a]` | `{'m': None, 'a': 1}` | [None, 1] | 只有 undefined 会被跳过；null 参与构造。 |
| 6 | `{'sum': a + b}` | `{'a': 1, 'b': 2}` | {'sum': 3} |  |

### 09_conditional.jx — 条件表达式 ? :

条件表达式在 jx 里编成 ("cond", test, then, orelse, loc)。两个分支现在都能走通：早期实现的假分支生成码是 `v=(_test and (_t1:=then) or _t1)`，走假分支必然 `UnboundLocalError: _t1`，**已修**（回归保护见 14_fixed_regressions.jx）。下面真、假分支各列几例。

```text
# file: 09_conditional.jx
# title: 条件表达式 ? :
# desc: 条件表达式在 jx 里编成 ("cond", test, then, orelse, loc)。两个分支现在都能走通：早期实现的假分支生成码是 `v=(_test and (_t1:=then) or _t1)`，走假分支必然 `UnboundLocalError: _t1`，**已修**（回归保护见 14_fixed_regressions.jx）。下面真、假分支各列几例。


=== 比较条件（真分支）
expr: a > 1 ? 'big' : 'small'
row: {'a': 5}
expect: 'big'

=== 比较条件（假分支）
expr: a > 1 ? 'big' : 'small'
row: {'a': 0}
expect: 'small'
type: str

=== 真值条件（真分支）
expr: a ? 'y' : 'n'
row: {'a': 5}
expect: 'y'

=== 真值条件（假分支）
expr: a ? 'y' : 'n'
row: {'a': 0}
expect: 'n'
type: str

=== 嵌套在连接里（真分支）
expr: (a > 1 ? 'big' : 'small') & '!'
row: {'a': 5}
expect: 'big!'

=== 嵌套在连接里（假分支）
expr: (a > 1 ? 'big' : 'small') & '!'
row: {'a': 1}
expect: 'small!'
type: str
```

| # | 表达式 | row | 实测 | 说明 |
|---|---|---|---|---|
| 1 | `a > 1 ? 'big' : 'small'` | `{'a': 5}` | 'big' |  |
| 2 | `a > 1 ? 'big' : 'small'` | `{'a': 0}` | 'small' |  |
| 3 | `a ? 'y' : 'n'` | `{'a': 5}` | 'y' |  |
| 4 | `a ? 'y' : 'n'` | `{'a': 0}` | 'n' |  |
| 5 | `(a > 1 ? 'big' : 'small') & '!'` | `{'a': 5}` | 'big!' |  |
| 6 | `(a > 1 ? 'big' : 'small') & '!'` | `{'a': 1}` | 'small!' |  |

### 10_backtick.jx — 反引号内联 Python

反引号段是**词法边界**：段内是逐字嵌入的 Python 单表达式（build 期用 `ast.parse(mode="eval")` 校验），段外仍是 JSONata。段内的 `$.链` 与裸名都是**字段引用**（契约 §3.7），会映射成生成码里的局部名。需要多语句（中间变量）时用代码块 `py{ let NAME = EXPR; return EXPR }` —— 见 `15_py_block.jx`。

```text
# file: 10_backtick.jx
# title: 反引号内联 Python
# desc: 反引号段是**词法边界**：段内是逐字嵌入的 Python 单表达式（build 期用 `ast.parse(mode="eval")` 校验），段外仍是 JSONata。段内的 `$.链` 与裸名都是**字段引用**（契约 §3.7），会映射成生成码里的局部名。需要多语句（中间变量）时用代码块 `py{ let NAME = EXPR; return EXPR }` —— 见 `15_py_block.jx`。

=== 内联 Python 算术
expr: `int($.a) + 1`
row: {'a': '3'}
expect: 4

=== 内联 Python 原样表达式
expr: `$.a * 2`
row: {'a': 4}
expect: 8

=== CAP 工作负载的 ext 原形
expr: `str($.5.a.1) + str(hourB)`
row: {5: {'a': [10, 20]}, 'hourB': 'H'}
expect: '20H'
note: 段内 `$.5.a.1`（完整链）与裸名 `hourB` 都是字段引用（契约 §3.7）；hourB 必须已声明。

=== 段外 JSONata + 段内 Python 混用
expr: 'p' & str($.a)
row: {'a': 7}
expect: 'p7'

=== 段内用 Python 内置函数
expr: `len($.a)`
row: {'a': [1, 2, 3]}
expect: 3

=== 两个反引号段参与同一表达式
expr: `$.a` + `$.b`
row: {'a': 1, 'b': 2}
expect: 3

=== 反引号段里也可以用引号字符串
expr: `$.a + '_s'`
row: {'a': '1'}
expect: '1_s'

=== 段内是纯 Python：不做 JSONata 的宽容拼接
expr: `$.a + '_s'`
row: {'a': 1}
expect: raise:TypeError:unsupported operand
note: 反引号段内是**逐字 Python**：`int + str` 直接抛 TypeError，不做 JSONata 的 `&` 语义。字符串拼接请用段外的 `$.a & '_s'`。
```

| # | 表达式 | row | 实测 | 说明 |
|---|---|---|---|---|
| 1 | `` `int($.a) + 1` `` | `{'a': '3'}` | 4 |  |
| 2 | `` `$.a * 2` `` | `{'a': 4}` | 8 |  |
| 3 | `` `str($.5.a.1) + str(hourB)` `` | `{5: {'a': [10, 20]}, 'hourB': 'H'}` | '20H' | 段内 `$.5.a.1`（完整链）与裸名 `hourB` 都是字段引用（契约 §3.7）；hourB 必须已声明。 |
| 4 | `'p' & str($.a)` | `{'a': 7}` | 'p7' |  |
| 5 | `` `len($.a)` `` | `{'a': [1, 2, 3]}` | 3 |  |
| 6 | `` `$.a` + `$.b` `` | `{'a': 1, 'b': 2}` | 3 |  |
| 7 | `` `$.a + '_s'` `` | `{'a': '1'}` | '1_s' |  |
| 8 | `` `$.a + '_s'` `` | `{'a': 1}` | TypeError: unsupported operand type(s) for +: 'int' and 'str' | 反引号段内是**逐字 Python**：`int + str` 直接抛 TypeError，不做 JSONata 的 `&` 语义。字符串拼接请用段外的 `$.a & '_s'`。 |

### 11_rejected.jx — 编译期拒绝清单（契约 §3.5 / §6.1）

这些表达式在 `compile_expr` 阶段就抛 `Unsupported`，**带列号**，绝不静默降级。`expect: reject` 表示"必须被拒绝"，文档里记录的是**实测的**消息与列号。

```text
# file: 11_rejected.jx
# title: 编译期拒绝清单（契约 §3.5 / §6.1）
# desc: 这些表达式在 `compile_expr` 阶段就抛 `Unsupported`，**带列号**，绝不静默降级。`expect: reject` 表示"必须被拒绝"，文档里记录的是**实测的**消息与列号。

=== 批量函数（需要整批数据）
expr: $sum(xs)
expect: reject

=== 批量函数：$average
expr: $average(xs)
expect: reject

=== 批量函数：$map（工具坑：必须先判归属再 emit 实参）
expr: $map(xs, $string)
expect: reject

=== 批量函数：$filter
expr: $filter(xs, 1)
expect: reject

=== 批量函数：$reduce
expr: $reduce(xs, 1)
expect: reject

=== 批量函数：$keys
expr: $keys(x)
expect: reject

=== 批量函数：$lookup
expr: $lookup(x, 'a')
expect: reject

=== 批量函数：$merge
expr: $merge(x)
expect: reject

=== 批量函数：$shuffle
expr: $shuffle(x)
expect: reject

=== 非确定函数：$now
expr: $now()
expect: reject

=== 非确定函数：$millis
expr: $millis()
expect: reject

=== 非确定函数：$random
expr: $random()
expect: reject

=== 非确定函数：$eval
expr: $eval(x)
expect: reject

=== 区间
expr: 1..5
expect: reject

=== 正则字面量
expr: /ab/
expect: reject

=== 正则字面量（带 flags）
expr: /ab/i
expect: reject

=== function(){} 高阶字面量
expr: function($a){$a}
expect: reject

=== 位置绑定 #
expr: a#$i
expect: reject

=== join 绑定 @
expr: a@$j
expect: reject

=== 变换操作符 ~>
expr: a ~> b
expect: reject

=== 变换操作符 |...|
expr: a | b | c
expect: reject

=== 排序 ^
expr: a^.b
expect: reject

=== 分组/聚合 {..}
expr: a{ 'k': v }
expect: reject

=== py{} 里只有表达式（缺少 return）
expr: py{ $.a }
expect: reject
note: 这里拒绝**不是**因为 `py{}` 不存在 —— 多语句块已在 jx 侧实现（契约 §3.7，见 `15_py_block.jx` 与文档「`py{}` 多语句代码块」一节）。被拒的原因是块只能由 `let NAME = EXPR;` 与结尾唯一一条 `return EXPR` 组成，`$.a` 两者都不是。

=== 深路径（两个符号段）
expr: $.a.b
expect: reject

=== 深路径（三个符号段）
expr: $.a.b.c
expect: reject

=== 深路径（数值段之间夹两个符号段）
expr: $.5.a.1.b
expect: reject

=== 反引号段含赋值语句
expr: `x = 1`
expect: reject

=== 反引号段含 import
expr: `import os`
expect: reject

=== 反引号段语法不完整
expr: `1 +`
expect: reject

=== 单独的 $$（当前行）
expr: $$
expect: reject
```

| # | 表达式 | row | 实测 | 说明 |
|---|---|---|---|---|
| 1 | `$sum(xs)` | `{}` | Unsupported · 第 5 列: $sum 不可逐行编译（仅支持字面量数组参数，其余需整批数据求值） |  |
| 2 | `$average(xs)` | `{}` | Unsupported · 第 9 列: $average 不可逐行编译（需整批数据求值） |  |
| 3 | `$map(xs, $string)` | `{}` | Unsupported · 第 5 列: $map 不可逐行编译（需整批数据求值） |  |
| 4 | `$filter(xs, 1)` | `{}` | Unsupported · 第 8 列: $filter 不可逐行编译（需整批数据求值） |  |
| 5 | `$reduce(xs, 1)` | `{}` | Unsupported · 第 8 列: $reduce 不可逐行编译（需整批数据求值） |  |
| 6 | `$keys(x)` | `{}` | Unsupported · 第 6 列: $keys 不可逐行编译（需整批数据求值） |  |
| 7 | `$lookup(x, 'a')` | `{}` | Unsupported · 第 8 列: $lookup 不可逐行编译（需整批数据求值） |  |
| 8 | `$merge(x)` | `{}` | Unsupported · 第 7 列: $merge 不可逐行编译（需整批数据求值） |  |
| 9 | `$shuffle(x)` | `{}` | Unsupported · 第 9 列: $shuffle 不可逐行编译（需整批数据求值） |  |
| 10 | `$now()` | `{}` | Unsupported · 第 5 列: $now 不可逐行编译（非确定求值） |  |
| 11 | `$millis()` | `{}` | Unsupported · 第 8 列: $millis 不可逐行编译（非确定求值） |  |
| 12 | `$random()` | `{}` | Unsupported · 第 8 列: $random 不可逐行编译（非确定求值） |  |
| 13 | `$eval(x)` | `{}` | Unsupported · 第 6 列: $eval 不可逐行编译（非确定求值） |  |
| 14 | `1..5` | `{}` | Unsupported · 第 2 列: 区间 a..b 不可逐行编译（需要序列语义） |  |
| 15 | `/ab/` | `{}` | Unsupported · 第 1 列: 正则字面量 不可逐行编译（契约 §3.5 要求拒绝） |  |
| 16 | `/ab/i` | `{}` | Unsupported · 第 1 列: 正则字面量 不可逐行编译（契约 §3.5 要求拒绝） |  |
| 17 | `function($a){$a}` | `{}` | Unsupported · 第 1 列: function(){} 高阶字面量 不可逐行编译（需整体求值） |  |
| 18 | `a#$i` | `{}` | Unsupported · 第 2 列: 位置绑定 # 不可逐行编译（契约 §3.5 要求拒绝） |  |
| 19 | `a@$j` | `{}` | Unsupported · 第 2 列: join 绑定 @ 不可逐行编译（契约 §3.5 要求拒绝） |  |
| 20 | `a ~> b` | `{}` | Unsupported · 第 3 列: 变换操作符 ~> / |...| 不可逐行编译（契约 §3.5 要求拒绝） |  |
| 21 | `a \| b \| c` | `{}` | Unsupported · 第 3 列: 变换操作符 ~> / |...| 不可逐行编译（契约 §3.5 要求拒绝） |  |
| 22 | `a^.b` | `{}` | Unsupported · 第 2 列: 排序 ^ 不可逐行编译（契约 §3.5 要求拒绝） |  |
| 23 | `a{ 'k': v }` | `{}` | Unsupported · 第 2 列: 分组/聚合 {..} 不可逐行编译（契约 §3.5 要求拒绝） |  |
| 24 | `py{ $.a }` | `{}` | Unsupported · 第 5 列: 块内不是合法语句 不可逐行编译（块只能由 `let NAME = EXPR;` 与结尾唯一一条 `return EXPR` 组成（契约 §3.7）） | 这里拒绝**不是**因为 `py{}` 不存在 —— 多语句块已在 jx 侧实现（契约 §3.7，见 `15_py_block.jx` 与文档「`py{}` 多语句代码块」一节）。被拒的原因是块只能由 `let NAME = EXPR;` 与结尾唯一一条 `return EXPR` 组成，`$.a` 两者都不是。 |
| 25 | `$.a.b` | `{}` | Unsupported · 第 2 列: 深路径 [a,b] 不可逐行编译（v2 不支持 JSONata 逐行走访（契约 §6.1），请用嵌套 fields） |  |
| 26 | `$.a.b.c` | `{}` | Unsupported · 第 2 列: 深路径 [a,b,c] 不可逐行编译（v2 不支持 JSONata 逐行走访（契约 §6.1），请用嵌套 fields） |  |
| 27 | `$.5.a.1.b` | `{}` | Unsupported · 第 2 列: 深路径 [5,a,1,b] 不可逐行编译（v2 不支持 JSONata 逐行走访（契约 §6.1），请用嵌套 fields） |  |
| 28 | `` `x = 1` `` | `{}` | Unsupported · 第 1 列: 反引号段 Python 语法错误 不可逐行编译（invalid syntax（段内偏移 3）—— 必须是单个 Python 表达式） |  |
| 29 | `` `import os` `` | `{}` | Unsupported · 第 1 列: 反引号段 Python 语法错误 不可逐行编译（invalid syntax（段内偏移 1）—— 必须是单个 Python 表达式） |  |
| 30 | `` `1 +` `` | `{}` | Unsupported · 第 1 列: 反引号段 Python 语法错误 不可逐行编译（invalid syntax（段内偏移 0）—— 必须是单个 Python 表达式） |  |
| 31 | `$$` | `{}` | Unsupported · 第 1 列: $$ 文档根 不可逐行编译（行模式没有整篇文档，只有当前行） |  |

### 12_gaps.jx — jx 接受、但 codegen 尚未实现（端到端拒绝）

这一批**能通过 jx 编译**（IR 合法、在契约 §3.4 白名单里），但在 `dcg.generate()` 阶段被 codegen 拒绝。属于**未实现**（gap），不是语法错误：用户写出来会拿到 `Unsupported`，所以不会静默出错值。`expect: codegen-reject`。

```text
# file: 12_gaps.jx
# title: jx 接受、但 codegen 尚未实现（端到端拒绝）
# desc: 这一批**能通过 jx 编译**（IR 合法、在契约 §3.4 白名单里），但在 `dcg.generate()` 阶段被 codegen 拒绝。属于**未实现**（gap），不是语法错误：用户写出来会拿到 `Unsupported`，所以不会静默出错值。`expect: codegen-reject`。

=== 字面量数组 $sum（codegen 明确拒绝）
expr: $sum([1, 2, 3])
expect: codegen-reject

=== 字面量数组 $min
expr: $min([3, 1, 2])
expect: codegen-reject

=== 字面量数组 $max
expr: $max([3, 1, 2])
expect: codegen-reject

=== 字面量数组 $reverse
expr: $reverse([1, 2, 3])
expect: codegen-reject

=== 字面量数组 $sort
expr: $sort([3, 1, 2])
expect: codegen-reject

=== 字面量数组 $distinct
expr: $distinct([1, 1, 2])
expect: codegen-reject

=== $append（codegen 不认识的列函数）
expr: $append([1], [2])
expect: codegen-reject

=== $replace（codegen 不认识的列函数）
expr: $replace(s, 'a', 'X')
row: {'s': 'a-b'}
expect: codegen-reject

=== $substringBefore
expr: $substringBefore(s, '-')
row: {'s': 'a-b'}
expect: codegen-reject

=== $substringAfter
expr: $substringAfter(s, '-')
row: {'s': 'a-b'}
expect: codegen-reject

=== $pad
expr: $pad(s, 5)
row: {'s': 'a-b'}
expect: codegen-reject

=== $base64encode
expr: $base64encode(s)
row: {'s': 'a-b'}
expect: codegen-reject

=== $base64decode
expr: $base64decode(s)
row: {'s': 'a-b'}
expect: codegen-reject

=== $encodeUrlComponent
expr: $encodeUrlComponent(s)
row: {'s': 'a-b'}
expect: codegen-reject

=== $decodeUrlComponent
expr: $decodeUrlComponent(s)
row: {'s': 'a-b'}
expect: codegen-reject

=== $formatNumber
expr: $formatNumber(n, '0.00')
row: {'n': 5.0}
expect: codegen-reject

=== $formatBase
expr: $formatBase(n, 2)
row: {'n': 5.0}
expect: codegen-reject
```

| # | 表达式 | row | 实测 | 说明 |
|---|---|---|---|---|
| 1 | `$sum([1, 2, 3])` | `{}` | Unsupported · codegen 拒绝 $sum（列 (1, 5)）：批处理/高阶/非确定函数本次不支持（列级语义见 CONTRACT §3.4） 不可逐行编译 | **未实现（codegen 拒绝）** |
| 2 | `$min([3, 1, 2])` | `{}` | Unsupported · codegen 拒绝 $min（列 (1, 5)）：批处理/高阶/非确定函数本次不支持（列级语义见 CONTRACT §3.4） 不可逐行编译 | **未实现（codegen 拒绝）** |
| 3 | `$max([3, 1, 2])` | `{}` | Unsupported · codegen 拒绝 $max（列 (1, 5)）：批处理/高阶/非确定函数本次不支持（列级语义见 CONTRACT §3.4） 不可逐行编译 | **未实现（codegen 拒绝）** |
| 4 | `$reverse([1, 2, 3])` | `{}` | Unsupported · codegen 拒绝 $reverse（列 (1, 9)）：批处理/高阶/非确定函数本次不支持（列级语义见 CONTRACT §3.4） 不可逐行编译 | **未实现（codegen 拒绝）** |
| 5 | `$sort([3, 1, 2])` | `{}` | Unsupported · codegen 拒绝 $sort（列 (1, 6)）：批处理/高阶/非确定函数本次不支持（列级语义见 CONTRACT §3.4） 不可逐行编译 | **未实现（codegen 拒绝）** |
| 6 | `$distinct([1, 1, 2])` | `{}` | Unsupported · codegen 拒绝 $distinct（列 (1, 10)）：批处理/高阶/非确定函数本次不支持（列级语义见 CONTRACT §3.4） 不可逐行编译 | **未实现（codegen 拒绝）** |
| 7 | `$append([1], [2])` | `{}` | Unsupported · codegen 不认识的列函数 $append（列 (1, 8)） 不可逐行编译 | **未实现（codegen 拒绝）** |
| 8 | `$replace(s, 'a', 'X')` | `{'s': 'a-b'}` | Unsupported · codegen 不认识的列函数 $replace（列 (1, 9)） 不可逐行编译 | **未实现（codegen 拒绝）** |
| 9 | `$substringBefore(s, '-')` | `{'s': 'a-b'}` | Unsupported · codegen 不认识的列函数 $substringBefore（列 (1, 17)） 不可逐行编译 | **未实现（codegen 拒绝）** |
| 10 | `$substringAfter(s, '-')` | `{'s': 'a-b'}` | Unsupported · codegen 不认识的列函数 $substringAfter（列 (1, 16)） 不可逐行编译 | **未实现（codegen 拒绝）** |
| 11 | `$pad(s, 5)` | `{'s': 'a-b'}` | Unsupported · codegen 不认识的列函数 $pad（列 (1, 5)） 不可逐行编译 | **未实现（codegen 拒绝）** |
| 12 | `$base64encode(s)` | `{'s': 'a-b'}` | Unsupported · codegen 不认识的列函数 $base64encode（列 (1, 14)） 不可逐行编译 | **未实现（codegen 拒绝）** |
| 13 | `$base64decode(s)` | `{'s': 'a-b'}` | Unsupported · codegen 不认识的列函数 $base64decode（列 (1, 14)） 不可逐行编译 | **未实现（codegen 拒绝）** |
| 14 | `$encodeUrlComponent(s)` | `{'s': 'a-b'}` | Unsupported · codegen 不认识的列函数 $encodeUrlComponent（列 (1, 20)） 不可逐行编译 | **未实现（codegen 拒绝）** |
| 15 | `$decodeUrlComponent(s)` | `{'s': 'a-b'}` | Unsupported · codegen 不认识的列函数 $decodeUrlComponent（列 (1, 20)） 不可逐行编译 | **未实现（codegen 拒绝）** |
| 16 | `$formatNumber(n, '0.00')` | `{'n': 5.0}` | Unsupported · codegen 不认识的列函数 $formatNumber（列 (1, 14)） 不可逐行编译 | **未实现（codegen 拒绝）** |
| 17 | `$formatBase(n, 2)` | `{'n': 5.0}` | Unsupported · codegen 不认识的列函数 $formatBase（列 (1, 12)） 不可逐行编译 | **未实现（codegen 拒绝）** |

### 13_known_defects.jx — 仍然存在的缺陷（运行期错误结果 / 异常）

**这一节记录的是当前实现的错误行为，不是正确语义。** 每条的 note 写了"应当是什么"。这些断言是**故意**钉住现状的：codegen/runtime 修好之后它们会变红，作为复核触发器。

**复现日期：2026-10-17 00:51 (Asia/Shanghai)**（实测引擎：`dcg/codegen.py` sha1 `c675d863666f`、`dcg/runtime.py` sha1 `cd760a6fba57`）。2026-10-17 的引擎修复把原先 21 条缺陷里的 **17 条修好了**，那 17 条已迁到 `14_fixed_regressions.jx` 并改为断言**正确值**（回归保护）；这里只剩**仍未修复**的 4 条。

```text
# file: 13_known_defects.jx
# title: 仍然存在的缺陷（运行期错误结果 / 异常）
# desc: **这一节记录的是当前实现的错误行为，不是正确语义。** 每条的 note 写了"应当是什么"。这些断言是**故意**钉住现状的：codegen/runtime 修好之后它们会变红，作为复核触发器。
# desc: **复现日期：2026-10-17 00:51 (Asia/Shanghai)**（实测引擎：`dcg/codegen.py` sha1 `c675d863666f`、`dcg/runtime.py` sha1 `cd760a6fba57`）。2026-10-17 的引擎修复把原先 21 条缺陷里的 **17 条修好了**，那 17 条已迁到 `14_fixed_regressions.jx` 并改为断言**正确值**（回归保护）；这里只剩**仍未修复**的 4 条。

=== 反引号段内的 math 模块
expr: `math.floor($.a * 1.2)`
row: {'a': 3}
expect: raise:NameError:math
state: defect
note: 应当 3。jx 把 `math` 当**安全模块**放行（`PY_SAFE_MODULES`），但生成码只 `from dcg.runtime import *`，没有 `import math`，于是原样嵌入的段在运行期 NameError。

=== null 做下标
expr: $.m[0]
row: {'m': None}
expect: raise:TypeError:not subscriptable
state: defect
note: 应当得到 None（契约 §4.4 第 7 条：`a[0]` 对标量成立，返回 a 本身）。当前对 None 直接抛 TypeError；对比 `$.a[0]`（数组）正常。

=== $round 使用银行家舍入
expr: $round(n)
row: {'n': 2.5}
expect: 2
state: defect
note: 应当 3。JSONata 是四舍五入（round half away from zero），Python 的 `round()` 是 banker's rounding。官方 jsonata-python 的 `$round(2.5)` = 3。

=== $count 对标量
expr: $count('abc')
expect: undefined
state: defect
note: 应当 1。JSONata 把非数组当单个元素；当前返回 undefined（而 `$count({'k':1})` 却返回 1，自相矛盾）。
```

| # | 表达式 | row | 实测 | 说明 |
|---|---|---|---|---|
| 1 | `` `math.floor($.a * 1.2)` `` | `{'a': 3}` | NameError: name 'math' is not defined | **已知缺陷（当前错误行为）** 应当 3。jx 把 `math` 当**安全模块**放行（`PY_SAFE_MODULES`），但生成码只 `from dcg.runtime import *`，没有 `import math`，于是原样嵌入的段在运行期 NameError。 |
| 2 | `$.m[0]` | `{'m': None}` | TypeError: 'NoneType' object is not subscriptable | **已知缺陷（当前错误行为）** 应当得到 None（契约 §4.4 第 7 条：`a[0]` 对标量成立，返回 a 本身）。当前对 None 直接抛 TypeError；对比 `$.a[0]`（数组）正常。 |
| 3 | `$round(n)` | `{'n': 2.5}` | 2 | **已知缺陷（当前错误行为）** 应当 3。JSONata 是四舍五入（round half away from zero），Python 的 `round()` 是 banker's rounding。官方 jsonata-python 的 `$round(2.5)` = 3。 |
| 4 | `$count('abc')` | `{}` | undefined（模块内 _U，其值 0） | **已知缺陷（当前错误行为）** 应当 1。JSONata 把非数组当单个元素；当前返回 undefined（而 `$count({'k':1})` 却返回 1，自相矛盾）。 |

### 14_fixed_regressions.jx — 已修复（回归保护）

这里每一条都**曾经**是错误行为 —— 原 `13_known_defects.jx` 里的 **17 条**（16 条来自 2026-10-17 的引擎修复批次：内部 undefined 哨兵从字面量 `0` 改为唯一对象、null 字面量、`? :` 假分支、`$number`、常量 0 与布尔 `false` 的处理；`in` 运算符随后也修复），加上布尔类型的一组补充样例。修复之后它们被**重新冻结为正确值**，并与官方 `jsonata-python` **逐例交叉复核一致**（复现日期 2026-10-17 00:51 (Asia/Shanghai)）。

用途是**回归保护**：断言的是**正确值**，将来再退化就会变红。**禁止**把这些条目改回"断言错误行为"，也**禁止**用 `pytest.skip` / `xfail` 盖过去（本项目硬规矩：通过 ≠ 跳过）。仍未修好的 4 条留在 `13_known_defects.jx`。

```text
# file: 14_fixed_regressions.jx
# title: 已修复（回归保护）
# desc: 这里每一条都**曾经**是错误行为 —— 原 `13_known_defects.jx` 里的 **17 条**（16 条来自 2026-10-17 的引擎修复批次：内部 undefined 哨兵从字面量 `0` 改为唯一对象、null 字面量、`? :` 假分支、`$number`、常量 0 与布尔 `false` 的处理；`in` 运算符随后也修复），加上布尔类型的一组补充样例。修复之后它们被**重新冻结为正确值**，并与官方 `jsonata-python` **逐例交叉复核一致**（复现日期 2026-10-17 00:51 (Asia/Shanghai)）。
# desc: 用途是**回归保护**：断言的是**正确值**，将来再退化就会变红。**禁止**把这些条目改回"断言错误行为"，也**禁止**用 `pytest.skip` / `xfail` 盖过去（本项目硬规矩：通过 ≠ 跳过）。仍未修好的 4 条留在 `13_known_defects.jx`。

=== in 运算符
expr: a in [1, 2, 3]
row: {'a': 2}
expect: True
type: bool
state: fixed
note: 原缺陷：runtime 里 `_in` 的名字与参数撞车，对不可调用对象发起调用（`'list' object is not callable`）。修复后得到 True，与官方一致。

=== null 字面量
expr: null
expect: None
type: NoneType
state: fixed
note: 原缺陷：生成码引用未定义名 `u`（NameError）。修复后得到 JSONata 的 null，与官方一致。

=== null 参与算术
expr: null + 1
expect: raise:TypeError:操作数
state: fixed
note: 契约 §4.4 第 7 条：null 参与算术是**错误**（不是 undefined）。原缺陷：NameError `u`；修复后是明确的 TypeError。官方 jsonata 同样报错（JException），措辞不同但语义一致：都是"错误"。

=== 条件表达式假分支
expr: a > 1 ? 'big' : 'small'
row: {'a': 0}
expect: 'small'
state: fixed
note: 原缺陷：生成码 `v=(_gt(a,1) and (_t1:='big') or _t1)`，走假分支时 `UnboundLocalError: _t1`。

=== 条件表达式假分支（真值条件）
expr: a ? 'y' : 'n'
row: {'a': 0}
expect: 'n'
state: fixed
note: 同上（`a` 为 0 时取假分支）。

=== $number
expr: $number(s)
row: {'s': '42'}
expect: 42
type: int
state: fixed
note: 原缺陷：生成码引用未定义名 `_NNN`（NameError）。修复后得 42，与官方一致。

=== 常量 0 被当成 undefined：相等比较
expr: z = 0
row: {'z': 0}
expect: True
type: bool
state: fixed
note: 原缺陷：`_U = 0` 且 CPython 小整数缓存使 `0 is _U` 为真 ⇒ 任何取值为 0 的字段都被 `_eq` 当成 undefined（契约 §4.4 第 3 条：遇 undefined 返回 False）。修复后哨兵是唯一对象，0 就是 0。

=== 常量 0 被当成 undefined：不等比较
expr: z != 0
row: {'z': 0}
expect: False
type: bool
state: fixed
note: 同上一类。

=== 常量 0 被当成 undefined：大小比较
expr: z < 5
row: {'z': 0}
expect: True
type: bool
state: fixed
note: 同上一类（曾被当成 undefined，比较返回 undefined）。

=== 常量 0 被当成 undefined：算术传播
expr: z + 1
row: {'z': 0}
expect: 1
type: int
state: fixed
note: 同上一类（曾被当成 undefined 而传播 undefined）。

=== 常量 0 被当成 undefined：$not
expr: $not(z)
row: {'z': 0}
expect: True
type: bool
state: fixed
note: JSONata `$not(0)` = true。

=== 常量 0 被当成 undefined：$exists
expr: $exists(z)
row: {'z': 0}
expect: True
type: bool
state: fixed
note: 0 是存在的值（`$exists` 只判断是否 undefined）。

=== 常量 0 被当成 undefined：$type
expr: $type(z)
row: {'z': 0}
expect: 'number'
state: fixed
note: 修复后 0 的类型是 'number'，与官方一致。

=== 常量 0 被当成 undefined：$boolean
expr: $boolean(a)
row: {'a': 0}
expect: False
type: bool
state: fixed
note: JSONata `$boolean(0)` = false（与官方一致）。

=== 布尔 false 与哨兵撞车：相等比较
expr: false = false
expect: True
type: bool
state: fixed
note: 原缺陷：生成码里 `false` 是 int `0`，与哨兵同一对象 ⇒ False。修复后 `false` 是真正的 `False`。

=== 布尔 false 与哨兵撞车：数组字面量丢元素
expr: [false]
expect: [False]
state: fixed
note: 原缺陷：返回 `[]`（契约 §4.4 第 5 条把 0 当 undefined 跳过）。修复后 `[False]`。

=== 布尔 false 与哨兵撞车：数组混合
expr: [false, true]
expect: [False, True]
state: fixed
note: 原缺陷：返回 `[1]`（`false` 被丢掉、`true` 退化成 int 1）。

=== 布尔类型：$type(true)
expr: $type(true)
expect: 'boolean'
state: fixed
note: 这一组是 01_literals.jx 布尔两例的回归保护：布尔字面量必须编成 Python `bool`，`$type`/`$string`/`$boolean` 与数组构造都依赖它。

=== 布尔类型：$type(false)
expr: $type(false)
expect: 'boolean'
state: fixed

=== 布尔类型：$string(true)
expr: $string(true)
expect: 'true'
state: fixed
note: 若 `true` 被编成 int 1，这里会变成 '1' —— 这正是要防住的退化。

=== 布尔类型：$string(false)
expr: $string(false)
expect: 'false'
state: fixed

=== 布尔运算 and
expr: true and false
expect: False
type: bool
state: fixed

=== 布尔运算 or
expr: true or false
expect: True
type: bool
state: fixed

=== $not(true)
expr: $not(true)
expect: False
type: bool
state: fixed

=== $not(false)
expr: $not(false)
expect: True
type: bool
state: fixed

=== 布尔与数值不是同一个值
expr: 1 = true
expect: False
type: bool
state: fixed
note: 官方 jsonata 同样是 False：数字与布尔是不同类型，不能相等。

=== 常量 0 的类型
expr: $type(0)
expect: 'number'
state: fixed

=== 常量 0 的相等
expr: 0 = 0
expect: True
type: bool
state: fixed
note: 字面量 0 与字段取值 0 现在行为一致（原缺陷只影响取值路径）。

=== 0 在条件表达式里是假（但不是 undefined）
expr: z ? 'y' : 'n'
row: {'z': 0}
expect: 'n'
state: fixed
note: JSONata 真值规则：0 为假。注意区分：0 **不是** undefined（`$exists(0)` 仍为 true）。
```

| # | 表达式 | row | 实测 | 说明 |
|---|---|---|---|---|
| 1 | `a in [1, 2, 3]` | `{'a': 2}` | True | **已修复（回归保护）** 原缺陷：runtime 里 `_in` 的名字与参数撞车，对不可调用对象发起调用（`'list' object is not callable`）。修复后得到 True，与官方一致。 |
| 2 | `null` | `{}` | None | **已修复（回归保护）** 原缺陷：生成码引用未定义名 `u`（NameError）。修复后得到 JSONata 的 null，与官方一致。 |
| 3 | `null + 1` | `{}` | TypeError: $add: 操作数必须是数值 | **已修复（回归保护）** 契约 §4.4 第 7 条：null 参与算术是**错误**（不是 undefined）。原缺陷：NameError `u`；修复后是明确的 TypeError。官方 jsonata 同样报错（JException），措辞不同但语义一致：都是"错误"。 |
| 4 | `a > 1 ? 'big' : 'small'` | `{'a': 0}` | 'small' | **已修复（回归保护）** 原缺陷：生成码 `v=(_gt(a,1) and (_t1:='big') or _t1)`，走假分支时 `UnboundLocalError: _t1`。 |
| 5 | `a ? 'y' : 'n'` | `{'a': 0}` | 'n' | **已修复（回归保护）** 同上（`a` 为 0 时取假分支）。 |
| 6 | `$number(s)` | `{'s': '42'}` | 42 | **已修复（回归保护）** 原缺陷：生成码引用未定义名 `_NNN`（NameError）。修复后得 42，与官方一致。 |
| 7 | `z = 0` | `{'z': 0}` | True | **已修复（回归保护）** 原缺陷：`_U = 0` 且 CPython 小整数缓存使 `0 is _U` 为真 ⇒ 任何取值为 0 的字段都被 `_eq` 当成 undefined（契约 §4.4 第 3 条：遇 undefined 返回 False）。修复后哨兵是唯一对象，0 就是 0。 |
| 8 | `z != 0` | `{'z': 0}` | False | **已修复（回归保护）** 同上一类。 |
| 9 | `z < 5` | `{'z': 0}` | True | **已修复（回归保护）** 同上一类（曾被当成 undefined，比较返回 undefined）。 |
| 10 | `z + 1` | `{'z': 0}` | 1 | **已修复（回归保护）** 同上一类（曾被当成 undefined 而传播 undefined）。 |
| 11 | `$not(z)` | `{'z': 0}` | True | **已修复（回归保护）** JSONata `$not(0)` = true。 |
| 12 | `$exists(z)` | `{'z': 0}` | True | **已修复（回归保护）** 0 是存在的值（`$exists` 只判断是否 undefined）。 |
| 13 | `$type(z)` | `{'z': 0}` | 'number' | **已修复（回归保护）** 修复后 0 的类型是 'number'，与官方一致。 |
| 14 | `$boolean(a)` | `{'a': 0}` | False | **已修复（回归保护）** JSONata `$boolean(0)` = false（与官方一致）。 |
| 15 | `false = false` | `{}` | True | **已修复（回归保护）** 原缺陷：生成码里 `false` 是 int `0`，与哨兵同一对象 ⇒ False。修复后 `false` 是真正的 `False`。 |
| 16 | `[false]` | `{}` | [False] | **已修复（回归保护）** 原缺陷：返回 `[]`（契约 §4.4 第 5 条把 0 当 undefined 跳过）。修复后 `[False]`。 |
| 17 | `[false, true]` | `{}` | [False, True] | **已修复（回归保护）** 原缺陷：返回 `[1]`（`false` 被丢掉、`true` 退化成 int 1）。 |
| 18 | `$type(true)` | `{}` | 'boolean' | **已修复（回归保护）** 这一组是 01_literals.jx 布尔两例的回归保护：布尔字面量必须编成 Python `bool`，`$type`/`$string`/`$boolean` 与数组构造都依赖它。 |
| 19 | `$type(false)` | `{}` | 'boolean' | **已修复（回归保护）** |
| 20 | `$string(true)` | `{}` | 'true' | **已修复（回归保护）** 若 `true` 被编成 int 1，这里会变成 '1' —— 这正是要防住的退化。 |
| 21 | `$string(false)` | `{}` | 'false' | **已修复（回归保护）** |
| 22 | `true and false` | `{}` | False | **已修复（回归保护）** |
| 23 | `true or false` | `{}` | True | **已修复（回归保护）** |
| 24 | `$not(true)` | `{}` | False | **已修复（回归保护）** |
| 25 | `$not(false)` | `{}` | True | **已修复（回归保护）** |
| 26 | `1 = true` | `{}` | False | **已修复（回归保护）** 官方 jsonata 同样是 False：数字与布尔是不同类型，不能相等。 |
| 27 | `$type(0)` | `{}` | 'number' | **已修复（回归保护）** |
| 28 | `0 = 0` | `{}` | True | **已修复（回归保护）** 字面量 0 与字段取值 0 现在行为一致（原缺陷只影响取值路径）。 |
| 29 | `z ? 'y' : 'n'` | `{'z': 0}` | 'n' | **已修复（回归保护）** JSONata 真值规则：0 为假。注意区分：0 **不是** undefined（`$exists(0)` 仍为 true）。 |

### 15_py_block.jx — py{} 多语句代码块（task-7）

契约 §3.7 的 `py{ let NAME = EXPR; ...; return EXPR }`。**已端到端可用**：jx 把块编译成 IR 节点 `("pyblock", stmts_source, free_field_names, loc)`（`stmts_source` 里 `let` 局部已改名成 `__jx_<块序号>_<名字>`），codegen 把每个块落成**模块级命名辅助函数** `def _pyN(f1, f2, ...)`（契约 §4.3 指定形状，**不是** lambda/IIFE）。节点形状与全部拒绝项的 IR 层断言见 `tests/test_jx_py_block.py`。

块内规则（§3.7）：只能 `let NAME = EXPR;` + 结尾唯一一条 `return EXPR`；不引入循环/异常/条件语句（条件请用 Python 条件表达式 `a if c else b` 或 JSONata 的 `? :`）；不能 `import`（模块请在生成码模块级导入）；不能 `def`/`class`/`lambda`；**不暴露裸字段名**（字段写 `$.名称`，块内局部必须先 `let`）；块内 `$.` 只支持单层字段或常量下标（契约 §6.1，与 JSONata 侧同一判据）。

反引号单表达式段见 `10_backtick.jx`；本节只讲多语句块。`py{}` 里**没有** JSONata 变量绑定：块内标识符是 Python 局部名，只有 `$.链` 才是行数据。**`py{}` 不是沙箱**：它是任意 Python，只做语法约束、不做安全隔离。

```text
# file: 15_py_block.jx
# title: py{} 多语句代码块（task-7）
# desc: 契约 §3.7 的 `py{ let NAME = EXPR; ...; return EXPR }`。**已端到端可用**：jx 把块编译成 IR 节点 `("pyblock", stmts_source, free_field_names, loc)`（`stmts_source` 里 `let` 局部已改名成 `__jx_<块序号>_<名字>`），codegen 把每个块落成**模块级命名辅助函数** `def _pyN(f1, f2, ...)`（契约 §4.3 指定形状，**不是** lambda/IIFE）。节点形状与全部拒绝项的 IR 层断言见 `tests/test_jx_py_block.py`。
# desc: 块内规则（§3.7）：只能 `let NAME = EXPR;` + 结尾唯一一条 `return EXPR`；不引入循环/异常/条件语句（条件请用 Python 条件表达式 `a if c else b` 或 JSONata 的 `? :`）；不能 `import`（模块请在生成码模块级导入）；不能 `def`/`class`/`lambda`；**不暴露裸字段名**（字段写 `$.名称`，块内局部必须先 `let`）；块内 `$.` 只支持单层字段或常量下标（契约 §6.1，与 JSONata 侧同一判据）。
# desc: 反引号单表达式段见 `10_backtick.jx`；本节只讲多语句块。`py{}` 里**没有** JSONata 变量绑定：块内标识符是 Python 局部名，只有 `$.链` 才是行数据。**`py{}` 不是沙箱**：它是任意 Python，只做语法约束、不做安全隔离。

=== 块的最小形态：一条 return
expr: py{ return int($.a) }
row: {'a': '7'}
expect: 7
type: int
note: jx 产出 `("pyblock", "return int($.a)", ["a"], (1, 4))`；codegen 落成模块级 `def _py0(_p0): return int(_p0)`（命名辅助函数，**不是** lambda/IIFE）。

=== let 绑定 + return
expr: py{ let t = $.a * 1.2; return int(t) }
row: {'a': 10}
expect: 12
type: int
note: 局部被改名为 `__jx_0_t`（生成码里就是这个名字）；`__jx` 是 jx/codegen 保留前缀，用户不能自己取（用了会被拒绝）。

=== 多条 let 链式依赖
expr: py{ let s = $.a; let t = s * 2; return t + 1 }
row: {'a': 5}
expect: 11
type: int
note: 块的返回值就是最后那条 `return EXPR` 的 EXPR；`let` 之间是普通 Python 顺序赋值。

=== 块内可以正常使用 Python 字面量与字符串
expr: py{ return '}#;' }
row: {}
expect: '}#;'
type: str
note: 块扫描是"字符串/注释感知"的：`}`、`;`、`#` 出现在字符串里不会误判块边界。

=== 块内调用函数（内置可用）
expr: py{ return len($.a) }
row: {'a': [1, 2, 3]}
expect: 3
type: int

=== 推导式
expr: py{ return [x * 2 for x in $.a] }
row: {'a': [1, 2]}
expect: [2, 4]
type: list
note: 推导式是**一条表达式**，不是循环语句，因此允许（循环语句 `for`/`while` 被拒绝）。

=== 对字段值调用方法
expr: py{ let t = $.a; return t.upper() }
row: {'a': 'ab'}
expect: 'AB'
type: str
note: 块内局部 `t` 是普通 Python 对象，可以随便调方法 —— 这是 `py{}` 比 JSONata 行函数更灵活的地方。

=== 块嵌在更大的表达式里
expr: py{ return $.a + 1 } / 2
row: {'a': 5}
expect: 3.0
type: float
note: 块本身是一个**表达式**（值 = `return` 的 EXPR），可以参与外层 JSONata 运算。

=== 块里只有表达式、没有 return
expr: py{ $.a }
row: {}
expect: reject
note: 块只能由 `let NAME = EXPR;` 与结尾唯一一条 `return EXPR` 组成。

=== 空块
expr: py{}
row: {}
expect: reject

=== 块没有 return
expr: py{ let t = 1 }
row: {}
expect: reject

=== 多个 return
expr: py{ return 1; return 2 }
row: {}
expect: reject

=== return 之后还有语句
expr: py{ return 1; let t = 2 }
row: {}
expect: reject

=== 赋值给非 let 变量
expr: py{ x = 1; return x }
row: {}
expect: reject
note: 块内局部必须先 `let NAME = EXPR;` 声明；普通赋值与 `+=` 会被拒绝。

=== 增强赋值
expr: py{ let t = 1; t += 1; return t }
row: {}
expect: reject

=== import
expr: py{ import os; return 1 }
row: {}
expect: reject
note: 需要模块请在生成码模块级 import（`dcg/runtime.py` 已导入常用模块），块内不引入 import。

=== from import
expr: py{ from os import path; return 1 }
row: {}
expect: reject

=== for 循环
expr: py{ for x in $.a: return x }
row: {'a': [1]}
expect: reject
note: 逐行编译器不引入循环语义；需要遍历请先取出数组再用 Python 推导式（在一条 `return` 的表达式里）。

=== while 循环
expr: py{ while True: return 1 }
row: {}
expect: reject

=== if 语句
expr: py{ if $.a: return 1 }
row: {'a': 1}
expect: reject
note: 条件请写 Python 条件表达式 `a if c else b`，或直接用 JSONata 三元 `c ? a : b`。

=== try/except
expr: py{ try: return 1 }
row: {}
expect: reject
note: 块内不做异常处理。

=== def
expr: py{ def f(): return 1 }
row: {}
expect: reject

=== class
expr: py{ class C: pass }
row: {}
expect: reject

=== lambda
expr: py{ return lambda: 1 }
row: {}
expect: reject
note: 复用请用 `let NAME = ...` + `return`。

=== 裸字段名（未声明的名字）
expr: py{ return hourB }
row: {'hourB': 1}
expect: reject
note: 块内**不暴露**已声明字段的裸名（与顶层、反引号段一致）：字段必须写 `$.hourB`。行里确实声明了 `hourB` 也会被拒绝 —— 这是有意的：块内标识符只能是 Python 局部名或 `$.链`。

=== 危险内置（显式拒绝）
expr: py{ return __import__('os') }
row: {}
expect: reject
note: `__import__` 是 Python 内置，但在块内被**显式**拒绝（禁止绕过模块级 import 的纪律）。

=== `$` 引用
expr: py{ return $x }
row: {}
expect: reject
note: 块内只有 `$.字段链`；`$$`（整行）与裸 `$` 在行模式下没有意义。

=== `$$` 引用
expr: py{ return $$ }
row: {}
expect: reject

=== 深路径
expr: py{ return $.a.b.c }
row: {'a': {'b': {'c': 1}}}
expect: reject
note: 契约 §6.1：字段访问只支持单层或常量下标链。要访问属性请先 `let x = $.a`，再写 `x.b`。

=== 保留前缀
expr: py{ return __jx_p0 }
row: {}
expect: reject
note: `__jx` 前缀是 jx/codegen 的占位符命名空间。

=== 未闭合的块
expr: py{ let t = 1; return t
row: {}
expect: reject
note: 缺少配对的 `}`，列号指向块起点（列 1）。
```

| # | 表达式 | row | 实测 | 说明 |
|---|---|---|---|---|
| 1 | `py{ return int($.a) }` | `{'a': '7'}` | 7 | jx 产出 `("pyblock", "return int($.a)", ["a"], (1, 4))`；codegen 落成模块级 `def _py0(_p0): return int(_p0)`（命名辅助函数，**不是** lambda/IIFE）。 |
| 2 | `py{ let t = $.a * 1.2; return int(t) }` | `{'a': 10}` | 12 | 局部被改名为 `__jx_0_t`（生成码里就是这个名字）；`__jx` 是 jx/codegen 保留前缀，用户不能自己取（用了会被拒绝）。 |
| 3 | `py{ let s = $.a; let t = s * 2; return t + 1 }` | `{'a': 5}` | 11 | 块的返回值就是最后那条 `return EXPR` 的 EXPR；`let` 之间是普通 Python 顺序赋值。 |
| 4 | `py{ return '}#;' }` | `{}` | '}#;' | 块扫描是"字符串/注释感知"的：`}`、`;`、`#` 出现在字符串里不会误判块边界。 |
| 5 | `py{ return len($.a) }` | `{'a': [1, 2, 3]}` | 3 |  |
| 6 | `py{ return [x * 2 for x in $.a] }` | `{'a': [1, 2]}` | [2, 4] | 推导式是**一条表达式**，不是循环语句，因此允许（循环语句 `for`/`while` 被拒绝）。 |
| 7 | `py{ let t = $.a; return t.upper() }` | `{'a': 'ab'}` | 'AB' | 块内局部 `t` 是普通 Python 对象，可以随便调方法 —— 这是 `py{}` 比 JSONata 行函数更灵活的地方。 |
| 8 | `py{ return $.a + 1 } / 2` | `{'a': 5}` | 3.0 | 块本身是一个**表达式**（值 = `return` 的 EXPR），可以参与外层 JSONata 运算。 |
| 9 | `py{ $.a }` | `{}` | Unsupported · 第 5 列: 块内不是合法语句 不可逐行编译（块只能由 `let NAME = EXPR;` 与结尾唯一一条 `return EXPR` 组成（契约 §3.7）） | 块只能由 `let NAME = EXPR;` 与结尾唯一一条 `return EXPR` 组成。 |
| 10 | `py{}` | `{}` | Unsupported · 第 4 列: 代码块缺少 return 不可逐行编译（`py{ ... }` 的值就是 `return EXPR` 的 EXPR（契约 §3.7 第 5 条）） |  |
| 11 | `py{ let t = 1 }` | `{}` | Unsupported · 第 15 列: 代码块缺少 return 不可逐行编译（`py{ ... }` 的值就是 `return EXPR` 的 EXPR（契约 §3.7 第 5 条）） |  |
| 12 | `py{ return 1; return 2 }` | `{}` | Unsupported · 第 15 列: 代码块有多个 return 不可逐行编译（块的返回值只能有一处（契约 §3.7 第 5 条）） |  |
| 13 | `py{ return 1; let t = 2 }` | `{}` | Unsupported · 第 15 列: return 之后还有语句 不可逐行编译（`return EXPR` 必须是块的最后一条语句） |  |
| 14 | `py{ x = 1; return x }` | `{}` | Unsupported · 第 5 列: 赋值给非 let 变量 'x' 不可逐行编译（块内局部必须写 `let NAME = EXPR;`（契约 §3.7 第 4 条）） | 块内局部必须先 `let NAME = EXPR;` 声明；普通赋值与 `+=` 会被拒绝。 |
| 15 | `py{ let t = 1; t += 1; return t }` | `{}` | Unsupported · 第 16 列: 赋值给非 let 变量 't' 不可逐行编译（块内局部必须写 `let NAME = EXPR;`（契约 §3.7 第 4 条）） |  |
| 16 | `py{ import os; return 1 }` | `{}` | Unsupported · 第 5 列: 块内禁止 import 不可逐行编译（需要模块请在生成码模块级 import（契约 §3.7 第 3 条）） | 需要模块请在生成码模块级 import（`dcg/runtime.py` 已导入常用模块），块内不引入 import。 |
| 17 | `py{ from os import path; return 1 }` | `{}` | Unsupported · 第 5 列: 块内禁止 import 不可逐行编译（需要模块请在生成码模块级 import（契约 §3.7 第 3 条）） |  |
| 18 | `py{ for x in $.a: return x }` | `{'a': [1]}` | Unsupported · 第 5 列: 块内不支持 for 循环 不可逐行编译（逐行编译器不引入循环语义（契约 §3.7 能力边界）） | 逐行编译器不引入循环语义；需要遍历请先取出数组再用 Python 推导式（在一条 `return` 的表达式里）。 |
| 19 | `py{ while True: return 1 }` | `{}` | Unsupported · 第 5 列: 块内不支持 while 循环 不可逐行编译（逐行编译器不引入循环语义（契约 §3.7 能力边界）） |  |
| 20 | `py{ if $.a: return 1 }` | `{'a': 1}` | Unsupported · 第 5 列: 块内不支持 if 语句 不可逐行编译（请用 JSONata 三元 `?:` 或 Python 条件表达式 `a if c else b`） | 条件请写 Python 条件表达式 `a if c else b`，或直接用 JSONata 三元 `c ? a : b`。 |
| 21 | `py{ try: return 1 }` | `{}` | Unsupported · 第 5 列: 块内不支持 try/except 不可逐行编译（块内不做异常处理（契约 §3.7 能力边界）） | 块内不做异常处理。 |
| 22 | `py{ def f(): return 1 }` | `{}` | Unsupported · 第 5 列: 块内不支持 def 不可逐行编译（块内不能定义函数；要复用请用 `let NAME = ...`（契约 §3.7 能力边界）） |  |
| 23 | `py{ class C: pass }` | `{}` | Unsupported · 第 5 列: 块内不支持 class 不可逐行编译（块内不能定义类（契约 §3.7 能力边界）） |  |
| 24 | `py{ return lambda: 1 }` | `{}` | Unsupported · 第 12 列: 块内不支持 lambda 不可逐行编译（请用 `let NAME = ...` + `return`（契约 §3.7 能力边界）） | 复用请用 `let NAME = ...` + `return`。 |
| 25 | `py{ return hourB }` | `{'hourB': 1}` | Unsupported · 第 12 列: 代码块里引用了未声明的名字 'hourB' 不可逐行编译（块内不暴露裸字段名：字段写 `$.名称`，块内局部先用 `let NAME = ...` 声明（契约 §3.7 第 1 条）） | 块内**不暴露**已声明字段的裸名（与顶层、反引号段一致）：字段必须写 `$.hourB`。行里确实声明了 `hourB` 也会被拒绝 —— 这是有意的：块内标识符只能是 Python 局部名或 `$.链`。 |
| 26 | `py{ return __import__('os') }` | `{}` | Unsupported · 第 12 列: 代码块里引用了未声明的名字 '__import__' 不可逐行编译（块内不暴露裸字段名：字段写 `$.名称`，块内局部先用 `let NAME = ...` 声明（契约 §3.7 第 1 条）） | `__import__` 是 Python 内置，但在块内被**显式**拒绝（禁止绕过模块级 import 的纪律）。 |
| 27 | `py{ return $x }` | `{}` | Unsupported · 第 12 列: 代码块里的 $ 引用 不可逐行编译（块内只有 `$.字段链`（契约 §3.7 第 1 条）；`$$` 与裸 `$` 在行模式下没有意义） | 块内只有 `$.字段链`；`$$`（整行）与裸 `$` 在行模式下没有意义。 |
| 28 | `py{ return $$ }` | `{}` | Unsupported · 第 12 列: 代码块里的 $ 引用 不可逐行编译（块内只有 `$.字段链`（契约 §3.7 第 1 条）；`$$` 与裸 `$` 在行模式下没有意义） |  |
| 29 | `py{ return $.a.b.c }` | `{'a': {'b': {'c': 1}}}` | Unsupported · 第 12 列: 块内深路径 $.a.b.c 不可逐行编译（块内 `$.` 只支持单层字段或常量下标（契约 §6.1）：要访问属性请先 `let x = $.名称`，再写 `x.属性`） | 契约 §6.1：字段访问只支持单层或常量下标链。要访问属性请先 `let x = $.a`，再写 `x.b`。 |
| 30 | `py{ return __jx_p0 }` | `{}` | Unsupported · 第 4 列: 代码块含保留前缀 '__jx' 不可逐行编译（请改名（jx/codegen 占位符命名空间）） | `__jx` 前缀是 jx/codegen 的占位符命名空间。 |
| 31 | `py{ let t = 1; return t` | `{}` | Unsupported · 第 1 列: 语法错误 不可逐行编译（py{} 代码块未闭合（缺少配对的 }）） | 缺少配对的 `}`，列号指向块起点（列 1）。 |

---

## 附录 A：不支持项汇总（jx 编译期与 codegen 端到端）

用户可用这一节判断「我的表达式能不能用」。`jx 拒绝` = `compile_expr` 抛 `Unsupported`（带列号）；`codegen 拒绝` = jx 接受但 `dcg.generate` 抛 `Unsupported`（**未实现**，不是语法错）。

| 表达式 | 阶段 | 实测消息 |
|---|---|---|
| `$sum(xs)` | jx 拒绝 | Unsupported · 第 5 列: $sum 不可逐行编译（仅支持字面量数组参数，其余需整批数据求值） |
| `$average(xs)` | jx 拒绝 | Unsupported · 第 9 列: $average 不可逐行编译（需整批数据求值） |
| `$map(xs, $string)` | jx 拒绝 | Unsupported · 第 5 列: $map 不可逐行编译（需整批数据求值） |
| `$filter(xs, 1)` | jx 拒绝 | Unsupported · 第 8 列: $filter 不可逐行编译（需整批数据求值） |
| `$reduce(xs, 1)` | jx 拒绝 | Unsupported · 第 8 列: $reduce 不可逐行编译（需整批数据求值） |
| `$keys(x)` | jx 拒绝 | Unsupported · 第 6 列: $keys 不可逐行编译（需整批数据求值） |
| `$lookup(x, 'a')` | jx 拒绝 | Unsupported · 第 8 列: $lookup 不可逐行编译（需整批数据求值） |
| `$merge(x)` | jx 拒绝 | Unsupported · 第 7 列: $merge 不可逐行编译（需整批数据求值） |
| `$shuffle(x)` | jx 拒绝 | Unsupported · 第 9 列: $shuffle 不可逐行编译（需整批数据求值） |
| `$now()` | jx 拒绝 | Unsupported · 第 5 列: $now 不可逐行编译（非确定求值） |
| `$millis()` | jx 拒绝 | Unsupported · 第 8 列: $millis 不可逐行编译（非确定求值） |
| `$random()` | jx 拒绝 | Unsupported · 第 8 列: $random 不可逐行编译（非确定求值） |
| `$eval(x)` | jx 拒绝 | Unsupported · 第 6 列: $eval 不可逐行编译（非确定求值） |
| `1..5` | jx 拒绝 | Unsupported · 第 2 列: 区间 a..b 不可逐行编译（需要序列语义） |
| `/ab/` | jx 拒绝 | Unsupported · 第 1 列: 正则字面量 不可逐行编译（契约 §3.5 要求拒绝） |
| `/ab/i` | jx 拒绝 | Unsupported · 第 1 列: 正则字面量 不可逐行编译（契约 §3.5 要求拒绝） |
| `function($a){$a}` | jx 拒绝 | Unsupported · 第 1 列: function(){} 高阶字面量 不可逐行编译（需整体求值） |
| `a#$i` | jx 拒绝 | Unsupported · 第 2 列: 位置绑定 # 不可逐行编译（契约 §3.5 要求拒绝） |
| `a@$j` | jx 拒绝 | Unsupported · 第 2 列: join 绑定 @ 不可逐行编译（契约 §3.5 要求拒绝） |
| `a ~> b` | jx 拒绝 | Unsupported · 第 3 列: 变换操作符 ~> / |...| 不可逐行编译（契约 §3.5 要求拒绝） |
| `a \| b \| c` | jx 拒绝 | Unsupported · 第 3 列: 变换操作符 ~> / |...| 不可逐行编译（契约 §3.5 要求拒绝） |
| `a^.b` | jx 拒绝 | Unsupported · 第 2 列: 排序 ^ 不可逐行编译（契约 §3.5 要求拒绝） |
| `a{ 'k': v }` | jx 拒绝 | Unsupported · 第 2 列: 分组/聚合 {..} 不可逐行编译（契约 §3.5 要求拒绝） |
| `py{ $.a }` | jx 拒绝 | Unsupported · 第 5 列: 块内不是合法语句 不可逐行编译（块只能由 `let NAME = EXPR;` 与结尾唯一一条 `return EXPR` 组成（契约 §3.7）） |
| `$.a.b` | jx 拒绝 | Unsupported · 第 2 列: 深路径 [a,b] 不可逐行编译（v2 不支持 JSONata 逐行走访（契约 §6.1），请用嵌套 fields） |
| `$.a.b.c` | jx 拒绝 | Unsupported · 第 2 列: 深路径 [a,b,c] 不可逐行编译（v2 不支持 JSONata 逐行走访（契约 §6.1），请用嵌套 fields） |
| `$.5.a.1.b` | jx 拒绝 | Unsupported · 第 2 列: 深路径 [5,a,1,b] 不可逐行编译（v2 不支持 JSONata 逐行走访（契约 §6.1），请用嵌套 fields） |
| `` `x = 1` `` | jx 拒绝 | Unsupported · 第 1 列: 反引号段 Python 语法错误 不可逐行编译（invalid syntax（段内偏移 3）—— 必须是单个 Python 表达式） |
| `` `import os` `` | jx 拒绝 | Unsupported · 第 1 列: 反引号段 Python 语法错误 不可逐行编译（invalid syntax（段内偏移 1）—— 必须是单个 Python 表达式） |
| `` `1 +` `` | jx 拒绝 | Unsupported · 第 1 列: 反引号段 Python 语法错误 不可逐行编译（invalid syntax（段内偏移 0）—— 必须是单个 Python 表达式） |
| `$$` | jx 拒绝 | Unsupported · 第 1 列: $$ 文档根 不可逐行编译（行模式没有整篇文档，只有当前行） |
| `$sum([1, 2, 3])` | codegen 拒绝 | Unsupported · codegen 拒绝 $sum（列 (1, 5)）：批处理/高阶/非确定函数本次不支持（列级语义见 CONTRACT §3.4） 不可逐行编译 |
| `$min([3, 1, 2])` | codegen 拒绝 | Unsupported · codegen 拒绝 $min（列 (1, 5)）：批处理/高阶/非确定函数本次不支持（列级语义见 CONTRACT §3.4） 不可逐行编译 |
| `$max([3, 1, 2])` | codegen 拒绝 | Unsupported · codegen 拒绝 $max（列 (1, 5)）：批处理/高阶/非确定函数本次不支持（列级语义见 CONTRACT §3.4） 不可逐行编译 |
| `$reverse([1, 2, 3])` | codegen 拒绝 | Unsupported · codegen 拒绝 $reverse（列 (1, 9)）：批处理/高阶/非确定函数本次不支持（列级语义见 CONTRACT §3.4） 不可逐行编译 |
| `$sort([3, 1, 2])` | codegen 拒绝 | Unsupported · codegen 拒绝 $sort（列 (1, 6)）：批处理/高阶/非确定函数本次不支持（列级语义见 CONTRACT §3.4） 不可逐行编译 |
| `$distinct([1, 1, 2])` | codegen 拒绝 | Unsupported · codegen 拒绝 $distinct（列 (1, 10)）：批处理/高阶/非确定函数本次不支持（列级语义见 CONTRACT §3.4） 不可逐行编译 |
| `$append([1], [2])` | codegen 拒绝 | Unsupported · codegen 不认识的列函数 $append（列 (1, 8)） 不可逐行编译 |
| `$replace(s, 'a', 'X')` | codegen 拒绝 | Unsupported · codegen 不认识的列函数 $replace（列 (1, 9)） 不可逐行编译 |
| `$substringBefore(s, '-')` | codegen 拒绝 | Unsupported · codegen 不认识的列函数 $substringBefore（列 (1, 17)） 不可逐行编译 |
| `$substringAfter(s, '-')` | codegen 拒绝 | Unsupported · codegen 不认识的列函数 $substringAfter（列 (1, 16)） 不可逐行编译 |
| `$pad(s, 5)` | codegen 拒绝 | Unsupported · codegen 不认识的列函数 $pad（列 (1, 5)） 不可逐行编译 |
| `$base64encode(s)` | codegen 拒绝 | Unsupported · codegen 不认识的列函数 $base64encode（列 (1, 14)） 不可逐行编译 |
| `$base64decode(s)` | codegen 拒绝 | Unsupported · codegen 不认识的列函数 $base64decode（列 (1, 14)） 不可逐行编译 |
| `$encodeUrlComponent(s)` | codegen 拒绝 | Unsupported · codegen 不认识的列函数 $encodeUrlComponent（列 (1, 20)） 不可逐行编译 |
| `$decodeUrlComponent(s)` | codegen 拒绝 | Unsupported · codegen 不认识的列函数 $decodeUrlComponent（列 (1, 20)） 不可逐行编译 |
| `$formatNumber(n, '0.00')` | codegen 拒绝 | Unsupported · codegen 不认识的列函数 $formatNumber（列 (1, 14)） 不可逐行编译 |
| `$formatBase(n, 2)` | codegen 拒绝 | Unsupported · codegen 不认识的列函数 $formatBase（列 (1, 12)） 不可逐行编译 |
| `py{ $.a }` | jx 拒绝 | Unsupported · 第 5 列: 块内不是合法语句 不可逐行编译（块只能由 `let NAME = EXPR;` 与结尾唯一一条 `return EXPR` 组成（契约 §3.7）） |
| `py{}` | jx 拒绝 | Unsupported · 第 4 列: 代码块缺少 return 不可逐行编译（`py{ ... }` 的值就是 `return EXPR` 的 EXPR（契约 §3.7 第 5 条）） |
| `py{ let t = 1 }` | jx 拒绝 | Unsupported · 第 15 列: 代码块缺少 return 不可逐行编译（`py{ ... }` 的值就是 `return EXPR` 的 EXPR（契约 §3.7 第 5 条）） |
| `py{ return 1; return 2 }` | jx 拒绝 | Unsupported · 第 15 列: 代码块有多个 return 不可逐行编译（块的返回值只能有一处（契约 §3.7 第 5 条）） |
| `py{ return 1; let t = 2 }` | jx 拒绝 | Unsupported · 第 15 列: return 之后还有语句 不可逐行编译（`return EXPR` 必须是块的最后一条语句） |
| `py{ x = 1; return x }` | jx 拒绝 | Unsupported · 第 5 列: 赋值给非 let 变量 'x' 不可逐行编译（块内局部必须写 `let NAME = EXPR;`（契约 §3.7 第 4 条）） |
| `py{ let t = 1; t += 1; return t }` | jx 拒绝 | Unsupported · 第 16 列: 赋值给非 let 变量 't' 不可逐行编译（块内局部必须写 `let NAME = EXPR;`（契约 §3.7 第 4 条）） |
| `py{ import os; return 1 }` | jx 拒绝 | Unsupported · 第 5 列: 块内禁止 import 不可逐行编译（需要模块请在生成码模块级 import（契约 §3.7 第 3 条）） |
| `py{ from os import path; return 1 }` | jx 拒绝 | Unsupported · 第 5 列: 块内禁止 import 不可逐行编译（需要模块请在生成码模块级 import（契约 §3.7 第 3 条）） |
| `py{ for x in $.a: return x }` | jx 拒绝 | Unsupported · 第 5 列: 块内不支持 for 循环 不可逐行编译（逐行编译器不引入循环语义（契约 §3.7 能力边界）） |
| `py{ while True: return 1 }` | jx 拒绝 | Unsupported · 第 5 列: 块内不支持 while 循环 不可逐行编译（逐行编译器不引入循环语义（契约 §3.7 能力边界）） |
| `py{ if $.a: return 1 }` | jx 拒绝 | Unsupported · 第 5 列: 块内不支持 if 语句 不可逐行编译（请用 JSONata 三元 `?:` 或 Python 条件表达式 `a if c else b`） |
| `py{ try: return 1 }` | jx 拒绝 | Unsupported · 第 5 列: 块内不支持 try/except 不可逐行编译（块内不做异常处理（契约 §3.7 能力边界）） |
| `py{ def f(): return 1 }` | jx 拒绝 | Unsupported · 第 5 列: 块内不支持 def 不可逐行编译（块内不能定义函数；要复用请用 `let NAME = ...`（契约 §3.7 能力边界）） |
| `py{ class C: pass }` | jx 拒绝 | Unsupported · 第 5 列: 块内不支持 class 不可逐行编译（块内不能定义类（契约 §3.7 能力边界）） |
| `py{ return lambda: 1 }` | jx 拒绝 | Unsupported · 第 12 列: 块内不支持 lambda 不可逐行编译（请用 `let NAME = ...` + `return`（契约 §3.7 能力边界）） |
| `py{ return hourB }` | jx 拒绝 | Unsupported · 第 12 列: 代码块里引用了未声明的名字 'hourB' 不可逐行编译（块内不暴露裸字段名：字段写 `$.名称`，块内局部先用 `let NAME = ...` 声明（契约 §3.7 第 1 条）） |
| `py{ return __import__('os') }` | jx 拒绝 | Unsupported · 第 12 列: 代码块里引用了未声明的名字 '__import__' 不可逐行编译（块内不暴露裸字段名：字段写 `$.名称`，块内局部先用 `let NAME = ...` 声明（契约 §3.7 第 1 条）） |
| `py{ return $x }` | jx 拒绝 | Unsupported · 第 12 列: 代码块里的 $ 引用 不可逐行编译（块内只有 `$.字段链`（契约 §3.7 第 1 条）；`$$` 与裸 `$` 在行模式下没有意义） |
| `py{ return $$ }` | jx 拒绝 | Unsupported · 第 12 列: 代码块里的 $ 引用 不可逐行编译（块内只有 `$.字段链`（契约 §3.7 第 1 条）；`$$` 与裸 `$` 在行模式下没有意义） |
| `py{ return $.a.b.c }` | jx 拒绝 | Unsupported · 第 12 列: 块内深路径 $.a.b.c 不可逐行编译（块内 `$.` 只支持单层字段或常量下标（契约 §6.1）：要访问属性请先 `let x = $.名称`，再写 `x.属性`） |
| `py{ return __jx_p0 }` | jx 拒绝 | Unsupported · 第 4 列: 代码块含保留前缀 '__jx' 不可逐行编译（请改名（jx/codegen 占位符命名空间）） |
| `py{ let t = 1; return t` | jx 拒绝 | Unsupported · 第 1 列: 语法错误 不可逐行编译（py{} 代码块未闭合（缺少配对的 }）） |

## 附录 B：仍未修复的缺陷汇总（运行期）

| 表达式 | row | 实测（错误行为） | 正确语义 |
|---|---|---|---|
| `` `math.floor($.a * 1.2)` `` | `{'a': 3}` | NameError: name 'math' is not defined | 应当 3。jx 把 `math` 当**安全模块**放行（`PY_SAFE_MODULES`），但生成码只 `from dcg.runtime import *`，没有 `import math`，于是原样嵌入的段在运行期 NameError。 |
| `$.m[0]` | `{'m': None}` | TypeError: 'NoneType' object is not subscriptable | 应当得到 None（契约 §4.4 第 7 条：`a[0]` 对标量成立，返回 a 本身）。当前对 None 直接抛 TypeError；对比 `$.a[0]`（数组）正常。 |
| `$round(n)` | `{'n': 2.5}` | 2 | 应当 3。JSONata 是四舍五入（round half away from zero），Python 的 `round()` 是 banker's rounding。官方 jsonata-python 的 `$round(2.5)` = 3。 |
| `$count('abc')` | `{}` | undefined（模块内 _U，其值 0） | 应当 1。JSONata 把非数组当单个元素；当前返回 undefined（而 `$count({'k':1})` 却返回 1，自相矛盾）。 |

## 附录 C：已修复（回归保护）

这些条目断言的是**正确值**（源：`14_fixed_regressions.jx`）。它们曾经是错误行为，在 2026-10-17 的引擎修复之后重新冻结，并与官方 `jsonata-python` 逐例交叉复核一致（复现日期 2026-10-17 01:04 (Asia/Shanghai)）。**再次退化就会变红。**

| 表达式 | row | 实测（正确值） | 修复前 / 说明 |
|---|---|---|---|
| `a in [1, 2, 3]` | `{'a': 2}` | True | runtime 里 `_in` 的名字与参数撞车，对不可调用对象发起调用（`'list' object is not callable`）。修复后得到 True，与官方一致。 |
| `null` | `{}` | None | 生成码引用未定义名 `u`（NameError）。修复后得到 JSONata 的 null，与官方一致。 |
| `null + 1` | `{}` | TypeError: $add: 操作数必须是数值 | 契约 §4.4 第 7 条：null 参与算术是**错误**（不是 undefined）。NameError `u`；修复后是明确的 TypeError。官方 jsonata 同样报错（JException），措辞不同但语义一致：都是"错误"。 |
| `a > 1 ? 'big' : 'small'` | `{'a': 0}` | 'small' | 生成码 `v=(_gt(a,1) and (_t1:='big') or _t1)`，走假分支时 `UnboundLocalError: _t1`。 |
| `a ? 'y' : 'n'` | `{'a': 0}` | 'n' | 同上（`a` 为 0 时取假分支）。 |
| `$number(s)` | `{'s': '42'}` | 42 | 生成码引用未定义名 `_NNN`（NameError）。修复后得 42，与官方一致。 |
| `z = 0` | `{'z': 0}` | True | `_U = 0` 且 CPython 小整数缓存使 `0 is _U` 为真 ⇒ 任何取值为 0 的字段都被 `_eq` 当成 undefined（契约 §4.4 第 3 条：遇 undefined 返回 False）。修复后哨兵是唯一对象，0 就是 0。 |
| `z != 0` | `{'z': 0}` | False | 同上一类。 |
| `z < 5` | `{'z': 0}` | True | 同上一类（曾被当成 undefined，比较返回 undefined）。 |
| `z + 1` | `{'z': 0}` | 1 | 同上一类（曾被当成 undefined 而传播 undefined）。 |
| `$not(z)` | `{'z': 0}` | True | JSONata `$not(0)` = true。 |
| `$exists(z)` | `{'z': 0}` | True | 0 是存在的值（`$exists` 只判断是否 undefined）。 |
| `$type(z)` | `{'z': 0}` | 'number' | 修复后 0 的类型是 'number'，与官方一致。 |
| `$boolean(a)` | `{'a': 0}` | False | JSONata `$boolean(0)` = false（与官方一致）。 |
| `false = false` | `{}` | True | 生成码里 `false` 是 int `0`，与哨兵同一对象 ⇒ False。修复后 `false` 是真正的 `False`。 |
| `[false]` | `{}` | [False] | 返回 `[]`（契约 §4.4 第 5 条把 0 当 undefined 跳过）。修复后 `[False]`。 |
| `[false, true]` | `{}` | [False, True] | 返回 `[1]`（`false` 被丢掉、`true` 退化成 int 1）。 |
| `$type(true)` | `{}` | 'boolean' | 这一组是 01_literals.jx 布尔两例的回归保护：布尔字面量必须编成 Python `bool`，`$type`/`$string`/`$boolean` 与数组构造都依赖它。 |
| `$type(false)` | `{}` | 'boolean' |  |
| `$string(true)` | `{}` | 'true' | 若 `true` 被编成 int 1，这里会变成 '1' —— 这正是要防住的退化。 |
| `$string(false)` | `{}` | 'false' |  |
| `true and false` | `{}` | False |  |
| `true or false` | `{}` | True |  |
| `$not(true)` | `{}` | False |  |
| `$not(false)` | `{}` | True |  |
| `1 = true` | `{}` | False | 官方 jsonata 同样是 False：数字与布尔是不同类型，不能相等。 |
| `$type(0)` | `{}` | 'number' |  |
| `0 = 0` | `{}` | True | 字面量 0 与字段取值 0 现在行为一致（原缺陷只影响取值路径）。 |
| `z ? 'y' : 'n'` | `{'z': 0}` | 'n' | JSONata 真值规则：0 为假。注意区分：0 **不是** undefined（`$exists(0)` 仍为 true）。 |

