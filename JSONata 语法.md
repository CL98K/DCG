# JSONata 语法（DCG 支持范围与示例）

> **读者**：使用 DCG 编写 schema 的开发者
> **定位**：本文是 DCG 支持的 JSONata 子集**唯一权威清单**：核心分类给出写法/输入/结果；完整 55 函数清单见 §12.1，逐函数实测见验收报告 §4.2 与 examples/jx/12_funcs.jx；实现必须与本文一致。
> **范围**：只讲表达式层的支持范围与示例；schema 结构、产物形态、字符预算见 `DCG 整体设计方案与架构文档.md`，接口细节见 `DCG 接口实现细节文档.md`。

## 0. 一页速览

| 问题 | 答案 |
|---|---|
| 本文是什么 | DCG 支持的 JSONata 子集：核心分类给出写法/输入/结果；完整 55 函数清单见 §12.1，逐函数实测见验收报告 §4.2 与 examples/jx/12_funcs.jx |
| 支持范围 | 55 个函数：A 类行内 46 / B 类高阶 6 / C 类非确定 4（`$sort` 同属 A/B）；永久不支持的构造见 §12.5；想扩展内置算子见 §13，想在不改引擎的前提下接入自己的函数见架构文档 §7.6 |
| 用在哪里 | schema 的 `expr` 表达式、`value` 取值路径、反引号段，以及 `py{}` 块内部的表达式层 |
| 求值模型 | 按行求值：表达式在 build 期编译进生成码，运行期无解释器；字段按依赖关系求值、前向引用合法 |
| 怎么验证 | 语料 242 条例样逐条 `dcg.generate` → `exec` → `run`；正文手写维护（不做逐字节比对）；语料计数以 §14 覆盖统计的合计行为权威 |
| 从哪看起 | §1 阅读前须知（1.1 undefined 与 null / 1.2 `$name` 与读值规则 / 1.3 裸名与产物形态 / 1.4 与 JSONata 1.8 的显式偏离）→ §2–§12 逐类语法与函数能力 → §13 怎么扩展内置算子 → §14 覆盖统计 → §15 缺陷与回归 |

> **导读**：§2–§11 是语法分类，§12–§13 是能力与扩展。

## 1. 阅读前须知

### 1.1 undefined 与 null

**undefined 与 null 不同**：
- `null` 是一个值：可以输出、可以放进数组；参与算术会报错。
- undefined 表示「没有值」，是表达式层的哨兵：例如 `$not($.m)` 在 `$.m` 走安全取值（声明了 `optional`/`default`）且缺键时得到 undefined；若 `$.m` 是 required 且无 `default`，取键本身会先抛 `KeyError`。undefined 出现在输出里时原样显示为 `undefined`，`&` 连接时当空串，比较规则见 §5。
- 取值缺失不是 undefined：非 optional 字段直接按键取值，行里缺键会在运行期抛 `KeyError`（`list` 下标越界抛 `IndexError`）。只有声明 `optional: true`（或设了 `default`）的字段，缺值才不抛异常——表达式内部先看到 undefined（`$exists` 等据此判断），字段最终落值由字段形态决定：`list` 行 / `dict` 字段 / `rows` 字段 / `list` 位置行落 `null`（`None`），`object` 嵌套字段丢弃该键；`default` 优先落默认值。
- **`$foo` 形式的变量引用**（不是函数调用）**一律 build 期报 `SchemaError`**：逐行模式没有变量作用域，`$foo` 无处可绑；**没有同名降级**——即使 `out.fields` 里声明了同名字段，`$foo` 仍然报错。**`$string` / `$map` 这类是函数名调用，不适用本节规则**（函数能力见 §12.1 能力表）。数据键在 build 期一律不检查，运行期行为**只看字段是否声明 `optional` / `default`**（`type` 声明不改变「裸链还是安全链」的判定，只决定安全链的快慢形态与「类型不符」时的行为）：**未声明 `optional`** 的路径一律发裸链，缺键抛 `KeyError`、下标越界抛 `IndexError`、对 `null` 取下标抛 `TypeError`——因此 `$exists($.missing)` 探不到 required 字段的缺失键（取键本身就抛了，`$exists` 根本没机会执行）；**声明 `optional: true`** 的整条链走守卫取值，缺键得 undefined（不抛），再按有无 `default` 落 `null` 或默认值，`$exists($.missing)` **只有在字段走安全取值路径（声明了 `optional: true` 或 `default`）时才有机会看到 undefined 并返回 `false`**；`default` 只影响落值，不改变 `$exists` 的判定（带 `default` 的字段走 `_df(_ix_get(...), <default>)`，`_ix_get` 返回 undefined，所以它的 `$exists($.missing)` 同样返回 `false`）。要容错请声明 `optional: true`（或设 `default`），或在数据准备侧保证键存在。
- **唯一的例外是回调形参**：`$map(a, function($v){...})` 里的 `$v` 由函数字面量绑定，见 §12。

### 1.2 `$name` 与读值规则

**字段按依赖关系求值**：字段在 schema 里的声明顺序只决定输出结构；计算顺序由字段之间的引用关系决定，前向引用（引用后声明的字段）合法。依赖成环或引用不存在的字段在 build 期报 `SchemaError`。

**裸名即字段**：`hourB`、`$.hourB` 读的都是本行字段 `hourB`。**下标里的裸名同样是字段**：`$.a[b]` 等价于 `$.a[$.b]`；要按字符串键取值请写引号：`$.a['b']`。

**`$name` 只有一种命运：编成 `var`，生成期报 `SchemaError`（没有同名降级）**：
1. **`$name` 一律编成 `var` 节点**；它不在任何函数字面量的形参位置上 → **build 期 `SchemaError`**：逐行模式没有变量作用域，`$name` 无处可绑。
2. **声明了同名字段也不改判**：`$i` 不是 `i`——`$.a[$i]` 不因为 schema 里声明了字段 `i` 就变成 `$.a[i]`，两者是不同的 token 形态。要按下标取字段请写裸名 `$.a[i]` / `$.a[hour]`。

### 1.3 裸名与产物形态

**产物形态**：每条示例标注生成的 Python 产物形态——「内联（裸代码）」= 直接发 Python 表达式、不调 helper；「helper（`_ad`…）」= 调用 `dcg.runtime` 的专用函数（保留 undefined / 类型守卫）；「内联 Python（反引号段）」= 原样嵌入一个 Python 表达式；「`py{}`」= 发 `run()` 外的模块级辅助函数 `_pyN`（每次 build 只定义一次），调用处写作 `_pyN(...)`，不是内联。能否发裸代码只取决于操作数类型能否在 build 期证明：字面量、required 且已声明类型的字段可以裸；`any`、optional、未声明类型必须走 helper。比较 / 逻辑 / 条件同理：两侧类型同类可证时发裸表达式（`(a<b)`、`(a and b)`、`(x if c else y)`），只有 JSONata 特例才留 helper——混合类型相等（`1 = true` 为 `false`）、可能 `undefined` 的操作数（undefined 传播语义）。有语义一致的 Python 内建等价的列函数也发内建表达式（`len()`、`abs()`、`in`、`split()`、`partition()`、`replace()`、`ljust()`、切片、`sum/max/min`、`bool`、`not`、`math.floor/ceil`、`a**b`、`math.sqrt`）—— **`$round` 不在内联表内**（Python `round()` 是银行家舍入，与 JSONata 的 half-away-from-zero 不同，恒定走 helper `_ro`）；`$number`、`$join`、`$type`、`$exists` 等无逐项等价或语义有差异的仍走 helper；`$sqrt` 只在可证非负时内联（负数走 helper 的 undefined 语义）。

**裸代码 vs helper：判定顺序**（自上而下，命中即定）：

| # | 情形 | 产物 |
|---|---|---|
| 1 | 字面量、常量链（`$.5.a.1`）、常量运算 | 裸代码 |
| 2 | 路径读值（`value` 与 `expr` 同规则；**段数不限**，`$.a.b`、`$.5.a.1` 同属此条）：**未声明 `optional` 且无 `default`** | 裸链 `R['a']`、`R['a']['b']`、`R[5]['a'][1]`；缺键 = `KeyError`；同一路径被多处引用或作为前缀时行首绑定一次 |
| 3 | 路径读值：**声明了 `optional` 或设了 `default`**（`value` 与 `expr` 同规则） | 安全链（形态见下条）；缺键 = `undefined` |
| 4 | 运算/函数/字面量容器，操作数类型可证 | 裸运算符 / Python 内建 / 裸字面量 |
| 5 | 操作数类型不可证（`any`、路径值、函数结果、变量） | helper（`_ad`/`_co`/`_ar`…） |
| 6 | `optional` / `default` 字段的值 | `_ix_get` + 守卫 / `_df` |
| 7 | 动态下标 `base[idx]` | required → 裸下标；optional/default → `_ix_get` |
| 8 | 语义特例（`$number`、`$join`、`$type`、`$exists`、`$keys/$values/$lookup/$merge/$spread`、`$sqrt` 不可证非负、`$round`（**1 参/2 参一律**走 helper `_ro`；Python `round()` 是银行家舍入，不能内联）、混合类型相等、高阶/非确定函数） | helper |

**读值口径四档、自上而下命中即定**（完整表见接口文档 §4.4）：

1. **带 `default`**（无论有无 `optional`、无论 `type` 是什么）→ `_df(_ix_get(...), <default>)`，**`default` 优先于 `type` 分档**；
2. **无 `optional` 且无 `default`**（含 `type: "any"`）→ 裸链；
3. **`optional` + 具体 `type`，且路径是纯名字段** → `.get(k, _U)` 快链（含整数下标的路径整条走 `_ix_get`，见接口文档 §4.4）；
4. **`optional` + `type: "any"` 或未声明 `type`** → `_ix_get` 全安全链。

四档的读法差异只看 `optional` / `default`，**`type` 不改变「裸链还是安全链」的判定**——`{"type":"any","value":"$.zz"}` 与 `{"type":"int64","value":"$.zz"}` 在未声明 `optional` 时产物完全同形（都是裸链，缺键 `KeyError`）；`type` 只在第 3/4 档之间选快链形态，并影响后续运算走 helper 还是裸运算符（第 5 行）。

「操作数类型可证」的来源：字面量；`optional` 非真且 `type` 为具体类型的字段；上述值的算术/比较/逻辑/条件/内建函数结果；字面量容器。


### 1.4 与 JSONata 1.8 的显式偏离

下面 9 条是**有意偏离** 1.8 的地方，其余语义按 1.8 对照。这份表是语义基准的一部分；外部显式引用见《DCG 接口实现细节文档》§3.6 与《DCG 整体设计方案与架构文档》§6.5。

| # | 构造 | JSONata 1.8 | DCG 现行语义 |
|---|---|---|---|
| 1 | `$name` 与已声明字段同名 | `$i` 与字段 `i` 同名时按字段取值 | **没有同名降级**：`$name` 一律编成 `var`，生成器层报 `SchemaError`（见 §1.2） |
| 2 | 动态下标 `$.a[$i]` | 合法，按变量取值 | **build 期拒绝**（同上）；要按下标取字段写裸名 `$.a[i]` / `$.a[$.i]` |
| 3 | `$sum` 遇 `bool` | 视实现而定 | **不做语义检查**，`True` 按 `1` 计（`$sum([true, 1]) == 2`） |
| 4 | 算术/比较算子遇 `bool`、同型 `str`/`list` | 1.8 报类型错误 | **跟随 Python**：`true + 1 == 2`、`'a' + 'b' == 'ab'`、`[1] + [2] == [1, 2]`、`true < 2`；只有 Python 自己也抛 `TypeError` 的组合（`'a' + 1`、`[] + 5`）才报 `$add: 操作数必须是数值` |
| 5 | `$round` 舍入方向 | half away from zero | **同 1.8**（half away from zero，`$round(2.5)=3`、`$round(-2.5)=-3`），但 Python `round()` 是银行家舍入，所以 `$round` 永不内联、恒走 `_ro` |
| 6 | 缺键默认行为 | 缺键通常得 undefined | **未声明 `optional` 且无 `default` → 裸链抛 `KeyError`/`IndexError`**；声明了才得 undefined / 默认值（见 §1.3 的读值口径四档） |
| 7 | `$$`（整篇文档根） | 指向整个输入文档 | **不支持**：逐行模式没有整篇文档，jx 编译层就拒绝 |
| 8 | `(a, b)` 序列构造 | 构造序列 / 并行求值 | **不支持**（`(…)` 在 DCG 里只是分组）；语法层报 `Unsupported: 语法错误 不可逐行编译（期望 ')'，实际 ','）`，见 §12.5 表外说明「序列构造 `(a, b)`」 |
| 9 | 结构化构造（`a..b`、`/re/`、`#`、`@`、`^`、`~>`、`\|...\|`、`{k:v}`、`{...}`、`$eval`） | 全部支持 | **永久拒绝**：`policy.STRUCTURAL` 10 个键 / 展开 13 条现象，逐条见 §12.5 |

## 2. 字面量

| 写法 | 输入行 | 结果 | 产物形态 | 说明 |
|---|---|---|---|---|
| `42` | — | `42` | 内联（裸代码） | 整数 |
| `3.5` | — | `3.5` | 内联（裸代码） | 小数 |
| `'hi'` | — | `'hi'` | 内联（裸代码） | 单引号字符串 |
| `"hi"` | — | `'hi'` | 内联（裸代码） | 双引号字符串，与单引号等价 |
| `true` | — | `true` | 内联（裸代码） | 布尔真（不是 1） |
| `false` | — | `false` | 内联（裸代码） | 布尔假（不是 0） |
| `null` | — | `null` | 内联（裸代码） | 空值，是**值**，与「取不到」不同 |
| `[1, 2, 3]` | — | `[1, 2, 3]` | 内联（裸代码） | 数组字面量 |
| `[[1,2],[3]]` | — | `[[1, 2], [3]]` | 内联（裸代码） | 嵌套数组 |
| `[]` | — | `[]` | 内联（裸代码） | 空数组 |
| `{}` | — | `{}` | 内联（裸代码） | 空对象 |
| `{'k': 1}` | — | `{'k': 1}` | 内联（裸代码） | 对象字面量 |
| `{'k': a, 'j': b}` | `{'a': 1, 'b': 2}` | `{'k': 1, 'j': 2}` | 内联（裸代码） | 对象值可以是表达式 |

## 3. 路径与取值

路径段可以引用本行其它字段的值：`$.a[hour].v` 里的 `hour` 可以是另一个字段加工出的结果，用来做动态键/下标。字段之间按依赖关系求值，声明顺序只决定输出结构（前向引用合法）；依赖成环或引用不存在的字段在 build 期报 `SchemaError`。

| 写法 | 输入行 | 结果 | 产物形态 | 说明 |
|---|---|---|---|---|
| `$.name` | `{'name': 'n'}` | `'n'` | 内联（裸代码） | 根路径取字段 |
| `name` | `{'name': 'n'}` | `'n'` | 内联（裸代码） | 裸名 = 本行已声明字段 |
| `$.5` | `[10, 20, 30, 40, 50, 60]` | `60` | 内联（裸代码） | 数值下标（行是 list） |
| `$.5.a` | `[0, 0, 0, 0, 0, {'a': [10, 20]}]` | `[10, 20]` | 内联（裸代码） | 下标后面可以继续接键 |
| `$.5.a.1` | `[0, 0, 0, 0, 0, {'a': [10, 20]}]` | `20` | 内联（裸代码） | 数值段与符号段可混用，长度不限 |
| `$.5.a.1.b` | `[0, 0, 0, 0, 0, {'a': [10, {'b': 20}]}]` | `20` | 内联（裸代码） | 任意深度 |
| `$.a.b` | `{'a': {'b': 7}}` | `7` | 内联（裸代码） | 两层标识符路径 |
| `$.a.b.c` | `{'a': {'b': {'c': 'x'}}}` | `'x'` | 内联（裸代码） | 三层标识符路径 |
| `$.a[0]` | `{'a': [7, 8]}` | `7` | 内联（裸代码） | 常量下标 |
| `a[0]` | `{'a': [7, 8]}` | `7` | 内联（裸代码） | 裸名 + 常量下标 |
| `$.a[$i]` | `{'a': [10, 20], 'i': 1}` | `编译期拒绝` | —（build 期拒绝） | `$i` 是变量形态，**不会**因为 schema 里声明了字段 `i` 就降级成字段引用（见 §1）；生成器层 `SchemaError`，文案「行模式没有变量作用域：$i 不是任何函数字面量的形参」。要按下标取字段请写裸名：`$.a[i]`（`i` 已声明时取到 `20`） |
| `$.a[$.b]` | `{'a': [10, 20], 'b': 0}` | `10` | 内联（裸代码） | 路径作下标 |
| `$.a[b]` | `{'a': [10, 20], 'b': 1}` | `20` | 内联（裸代码） | 下标里的裸名也是字段引用 |
| `$.a[hour].v` | `{'a': {'x': {'v': 42}}, 'hour': 'x'}` | `42` | 内联（裸代码） | 跨字段取值：`hour`（`expr` 算出 `'x'`）作动态键。**前提：schema 声明了字段 `hour`**；输入行里的 `hour` 供该字段读值 |
| `$.a[$hour].v` | `{'a': {'x': {'v': 42}}}` | `编译期拒绝` | —（build 期拒绝） | `$hour` 是变量形态 → **与 `$x + 1` 是同一条规则、同一个报错**：行模式无变量作用域 → 生成器层 `SchemaError`（文案「行模式没有变量作用域：$hour 不是任何函数字面量的形参」）。**位置无关、也没有同名降级**——`$name` 在下标位置和算术位置走完全相同的判定；schema 里声明了 `hour` 字段也不改判（对比上一条：裸名 `hour` 才是字段引用）。写法建议：`$.a[hour].v`（裸名，编译为 `R['a'][R['hour']]['v']`）或 `$.a[$.hour].v`（路径作下标，同样编译为 `R['a'][R['hour']]['v']`）。不要写 `$.a.$.hour.v`——实测它被编译成 `R['a']['hour']['v']`（`$.` 夹在段中间被吞掉，`hour` 当成字面字段名），语义与预期不同。 |
| `$.a[hour]` | `{'a': [10, 20, 30], 'hour': 1}` | `20` | 内联（裸代码） | 整数下标：`hour` 是 `value: "$.hour"`、`type: "int64"` 的 required 字段（产物行首绑定 `hour=R['hour']`，再 `R['a'][hour]`）；`hour = 1` 时取第 2 个元素 |
| `$.a['b']` | `{'a': {'b': 7}}` | `7` | 内联（裸代码） | 带引号 = 常量键 |
| `$.a.{hour}.v` | `{'a': {'x': {'v': 42}}}` | `Unsupported` | —（拒绝） | `{...}` 是对象构造/分组语义，不支持；动态键请用 `[...]` |
| `$` | `{'a': 1}` | `{'a': 1}` | 内联（裸代码） | 整行（根） |
| `$i + 1` | `{'i': 1}` | `编译期拒绝` | —（build 期拒绝） | `$i` 是变量形态，**不会**因为声明了字段 `i` 就降级为字段引用（见 §1）；生成器层 `SchemaError`，与 `$x + 1` 同一条规则。要取本行字段写裸名 `i + 1` 或 `$.i + 1` |
| `$x + 1` | `{'i': 1}` | `编译期拒绝` | —（build 期拒绝） | `$x` 是变量形态，逐行模式没有变量作用域 → 生成器层 `SchemaError`，文案指引「写裸名 x 或 $.x」。**无论 `out.fields` 里是否声明了同名字段 `x`，`$x` 都报错**（没有同名降级）；`$i + 1` 在 `i` 已声明时同样报错，见 §12.5 |

## 4. 算术与字符串连接

| 写法 | 输入行 | 结果 | 产物形态 | 说明 |
|---|---|---|---|---|
| `a + b` | `{'a': 1, 'b': 2}` | `3` | 内联（裸代码） | 加 |
| `a - b` | `{'a': 7, 'b': 2}` | `5` | 内联（裸代码） | 减 |
| `a * b` | `{'a': 3, 'b': 4}` | `12` | 内联（裸代码） | 乘 |
| `a / b` | `{'a': 7, 'b': 2}` | `3.5` | 内联（裸代码） | 除（结果是浮点） |
| `a % b` | `{'a': 7, 'b': 2}` | `1` | 内联（裸代码） | 取模 |
| `-a` | `{'a': 5}` | `-5` | 内联（裸代码） | 一元负号 |
| `+a` | `{'a': 5}` | `5` | 内联（裸代码） | 一元正号 |
| `a + b * 2` | `{'a': 1, 'b': 2}` | `5` | 内联（裸代码） | 乘法优先于加法 |
| `(a + b) * 2` | `{'a': 1, 'b': 2}` | `6` | 内联（裸代码） | 括号 |
| `2 + 3 * 4` | — | `14` | 内联（裸代码） | 常量也按正常优先级求值，可发裸代码 |
| `'a' & 'b'` | — | `'ab'` | 内联（裸代码） | `&` 是字符串连接 |
| `'n=' & a` | `{'a': 5}` | `'n=5'` | helper（`_cc`） | 连接时数字自动转字符串 |
| `'y' & a & 'z'` | `{'a': 1}` | `'y1z'` | helper（`_cc`） | 三段连接 |
| `'x' & $.m` | `{'m': null}` | `'x'` | helper（`_cc`） | null 当空串 |
| `$.m & 'x'` | `{'m': null}` | `'x'` | helper（`_cc`） | 左侧 null 也当空串 |
| `$.m + 1` | `{'m': null}` | `TypeError` | helper（`_ad`）；运行期 `TypeError` | null 是值：参与算术报错（不是 undefined） |

## 5. 比较、逻辑与条件

| 写法 | 输入行 | 结果 | 产物形态 | 说明 |
|---|---|---|---|---|
| `a = b` | `{'a': 1, 'b': 1}` | `true` | 内联（裸代码） | 相等 |
| `a != b` | `{'a': 1, 'b': 2}` | `true` | 内联（裸代码） | 不等 |
| `a < b` | `{'a': 1, 'b': 2}` | `true` | 内联（裸代码） | 小于 |
| `a <= b` | `{'a': 2, 'b': 2}` | `true` | 内联（裸代码） | 小于等于 |
| `a > b` | `{'a': 3, 'b': 2}` | `true` | 内联（裸代码） | 大于 |
| `a >= b` | `{'a': 1, 'b': 2}` | `false` | 内联（裸代码） | 大于等于 |
| `'a' < 'b'` | — | `true` | 内联（裸代码） | 字符串按字典序比较 |
| `a in [1, 2, 3]` | `{'a': 2}` | `true` | 内联（裸代码） | `in`：元素是否在数组里 |
| `a in [1, 2, 3]` | `{'a': 9}` | `false` | 内联（裸代码） | `in` 反例 |
| `a and b` | `{'a': true, 'b': false}` | `false` | 内联（裸代码） | 逻辑与 |
| `a or b` | `{'a': false, 'b': true}` | `true` | 内联（裸代码） | 逻辑或 |
| `not a` | `{'a': false}` | `true` | 内联（裸代码） | 逻辑非 |
| `not a` | `{'a': true}` | `false` | 内联（裸代码） | 逻辑非（真） |
| `(a or b) and not c` | `{'a': false, 'b': true, 'c': false}` | `true` | 内联（裸代码） | 嵌套逻辑 |
| `a > 1 ? 'big' : 'small'` | `{'a': 5}` | `'big'` | 内联（裸代码） | 条件表达式，真分支 |
| `a > 1 ? 'big' : 'small'` | `{'a': 0}` | `'small'` | 内联（裸代码） | 条件表达式，假分支 |
| `a ? 'y' : 'n'` | `{'a': 5}` | `'y'` | 内联（裸代码） | 真值条件（真） |
| `a ? 'y' : 'n'` | `{'a': 0}` | `'n'` | 内联（裸代码） | 真值条件（假）：0 为假 |
| `(a > 1 ? 'big' : 'small') & '!'` | `{'a': 5}` | `'big!'` | 内联（裸代码） | 条件嵌在连接里 |
| `1 = true` | — | `false` | helper（`_eq`） | 布尔与数值不是同一个值 |

## 6. 字符串函数

> 下表默认字段为 required 且声明了可证明的具体类型（判据见 §1.3）；`any` / `optional` / 未声明类型一律走 helper。

| 写法 | 输入行 | 结果 | 产物形态 | 说明 |
|---|---|---|---|---|
| `$string(a)` | `{'a': 5}` | `'5'` | 内联（裸代码） | 数字转字符串 |
| `$string(true)` | — | `'true'` | helper（`_S`） | 布尔转字符串 |
| `$uppercase(s)` | `{'s': 'ab'}` | `'AB'` | 内联（裸代码） | 大写 |
| `$lowercase(s)` | `{'s': 'AB'}` | `'ab'` | 内联（裸代码） | 小写 |
| `$trim(s)` | `{'s': '  x  '}` | `'x'` | 内联（裸代码） | 去首尾空白 |
| `$length(s)` | `{'s': 'abc'}` | `3` | 内联（裸代码） | 长度 |
| `$substring(s, 1, 2)` | `{'s': 'abcde'}` | `'bc'` | 内联（裸代码） | 子串：起点 + 长度 |
| `$substring(s, 2)` | `{'s': 'abcde'}` | `'cde'` | 内联（裸代码） | 子串：只有起点，取到末尾 |
| `$split(s, ',')` | `{'s': 'a,b,c'}` | `['a', 'b', 'c']` | 内联（裸代码） | 切分 |
| `$join(xs, '-')` | `{'xs': ['a', 'b']}` | `'a-b'` | helper（`_jn`） | 连接数组 |
| `$contains(s, 'b')` | `{'s': 'abc'}` | `true` | 内联（裸代码） | 包含（真） |
| `$contains(s, 'z')` | `{'s': 'abc'}` | `false` | 内联（裸代码） | 包含（假） |
| `$indexOf('a234b', '23')` | — | `1` | helper（`_ix`） | 子串首次出现的位置（0 起）；找不到得 -1 |
| `$indexOf('a234b', 23)` | — | `1` | helper（`_ix`） | 子串按 `$string` 转换后再找 |
| `$indexOf(5, 'x')` | — | `undefined` | helper（`_ix`） | 左侧不是字符串得到 undefined |

## 7. 数值函数

> 下表默认字段为 required 且声明了可证明的具体类型（判据见 §1.3）；`any` / `optional` / 未声明类型一律走 helper。

| 写法 | 输入行 | 结果 | 产物形态 | 说明 |
|---|---|---|---|---|
| `$number(s)` | `{'s': '42'}` | `42` | helper（`_N`） | 字符串转数字 |
| `$abs(n)` | `{'n': -3}` | `3` | 内联（裸代码） | 绝对值 |
| `$floor(n)` | `{'n': 3.7}` | `3` | 内联（裸代码） | 下取整 |
| `$ceil(n)` | `{'n': 3.2}` | `4` | 内联（裸代码） | 上取整 |
| `$round(n)` | `{'n': 3.5}` | `4` | helper（`_ro`） | 四舍五入：half away from zero（半值一律远离零，故 `$round(2.5)` = 3） |
| `$sqrt(n)` | `{'n': 9}` | `3.0` | helper（`_sq`） | 平方根 |
| `$power(a, b)` | `{'a': 2, 'b': 10}` | `1024` | 内联（裸代码） | 幂 |

## 8. 类型、存在性与计数

> 下表默认字段为 required 且声明了可证明的具体类型（判据见 §1.3）；`any` / `optional` / 未声明类型一律走 helper。**`$count` 的内联前提是可证为数组（字面量数组，或 `type:"list"` 且 required 的裸名字段）。**

| 写法 | 输入行 | 结果 | 产物形态 | 说明 |
|---|---|---|---|---|
| `$type(a)` | `{'a': 1}` | `'number'` | helper（`_ty`） | 数字 |
| `$type(a)` | `{'a': 's'}` | `'string'` | helper（`_ty`） | 字符串 |
| `$type(a)` | `{'a': [1]}` | `'array'` | helper（`_ty`） | 数组 |
| `$type(a)` | `{'a': {'k': 1}}` | `'object'` | helper（`_ty`） | 对象 |
| `$type($.m)` | `{'m': null}` | `'null'` | helper（`_ty`） | null |
| `$type(true)` | — | `'boolean'` | helper（`_ty`） | 布尔 |
| `$boolean(a)` | `{'a': 1}` | `true` | 内联（裸代码） | 布尔化 |
| `$boolean(a)` | `{'a': 0}` | `false` | 内联（裸代码） | 布尔化：0 为假 |
| `$not(a)` | `{'a': 1}` | `false` | 内联（裸代码） | 逻辑非函数 |
| `$not(true)` | — | `false` | 内联（裸代码） | 逻辑非函数（布尔） |
| `$exists(a)` | `{'a': 1}` | `true` | helper（`_ex`） | 存在性：有值即真 |
| `$count([1, 2, 3])` | — | `3` | 内联（裸代码） | 计数（字面量数组） |
| `$count(xs)` | `{'xs': [1, 2, 3]}` | `3` | 内联（裸代码） | 计数（字段数组；**要求 `xs` 声明 `type:"list"` 且 required**） |
| `$count('abc')` | — | `1` | helper（`_ct`） | 计数（标量按 1 计；注意 `$length` 不同：对标量返回 undefined） |

**`$count` 的内联前提**：实参可证为数组——字面量数组，或声明为 `type:"list"` 且 **required** 的字段；类型不可证（`optional`/`any`/未声明/标量）时走 helper `_ct`。上表「产物形态」按此前提标注。

**`$boolean` 的内联前提**：实参真值域可证时内联 `bool(x)`；不可证（`any`/`optional`/路径值/未声明类型）时走 helper `_B`。`_B` 是 JSONata 1.8 语义：非空串为真（**不解析** `'true'`/`'false'`）、`null`/空容器/`0`/空串为假、`undefined` 原样传播；与内联 `bool()` 对可证类型结果一致。与 `$not`（`_nt`）共用同一 `_tru` 真值口径，两者对 `undefined` 都原样传播。完整口径见《DCG 接口实现细节文档》§4.4。

## 9. 数组与对象构造

| 写法 | 输入行 | 结果 | 产物形态 | 说明 |
|---|---|---|---|---|
| `[a, b]` | `{'a': 1, 'b': 2}` | `[1, 2]` | 内联（裸代码） | 引用字段构造数组 |
| `[a, b, a + b]` | `{'a': 1, 'b': 2}` | `[1, 2, 3]` | 内联（裸代码） | 数组内可写表达式 |
| `[$.m, a]` | `{'m': null, 'a': 1}` | `[null, 1]` | helper（`_ar`） | null 是值，保留在数组里 |
| `{'k': a}` | `{'a': 1}` | `{'k': 1}` | 内联（裸代码） | 对象字段 |
| `{'sum': a + b}` | `{'a': 1, 'b': 2}` | `{'sum': 3}` | 内联（裸代码） | 对象值可以是表达式 |

## 10. 反引号内联 Python

反引号段内是逐字嵌入的 Python **单表达式**（build 期只做语法校验，不执行）；段外仍是 JSONata。段内的 `$.路径` 读**原始行**（build 期只校验字段已声明，**不进入依赖图**）；段内的**裸名**才是字段引用（读该字段的计算结果，并参与依赖图）。二者不是同一种「字段引用」。

| 写法 | 输入行 | 结果 | 产物形态 | 说明 |
|---|---|---|---|---|
| `` `int($.a) + 1` `` | `{'a': '3'}` | `4` | 内联 Python | 段内 Python 算术 |
| `` `$.a * 2` `` | `{'a': 4}` | `8` | 内联 Python | 原样 Python 表达式 |
| `` `len($.a)` `` | `{'a': [1, 2, 3]}` | `3` | 内联 Python | 段内可用 Python 内置函数 |
| `` `$.a` + `$.b` `` | `{'a': 1, 'b': 2}` | `3` | 内联 Python + helper（`_ad`） | 两个反引号段参与同一表达式 |
| `'p' & str($.a)` | `{'a': 7}` | `'p7'` | helper（`_S`、`_cc`） | 段外 JSONata + 段内 Python 混用 |
| `` `$.a + '_s'` `` | `{'a': '1'}` | `'1_s'` | 内联 Python | 段内是纯 Python（字符串拼接用 +） |
| `` `$.a + '_s'` `` | `{'a': 1}` | `TypeError` | 内联 Python；运行期 `TypeError` | 段内不做 JSONata 的宽容拼接：数字 + 字符串报错 |

## 11. py{} 多语句代码块

块内规则：只能 `let NAME = EXPR;` 加结尾唯一一条 `return EXPR`，中间可用 Python 表达式（含推导式、方法调用、内置函数）；不能写循环/条件语句/`try`/`def`/`class`/`lambda`/`import`，也不能直接用裸字段名（字段写 `$.名称`）。`py{}` 不是沙箱，只做语法约束。`py{}` 也可以写在函数字面量体内（如 `$map([1, 2], function($x){ py{ return 7 } })`）：块每元素执行一次，但**块内不能引用回调参数**（`$x`），只能读原始行 `$.字段链`，详见 §12.3。

| 写法 | 输入行 | 结果 | 产物形态 | 说明 |
|---|---|---|---|---|
| `py{ return int($.a) }` | `{'a': '7'}` | `7` | 辅助函数 `_pyN`（模块级定义，调用处 `_pyN(...)`） | 最小形态：一条 return |
| `py{ let t = $.a * 1.2; return int(t) }` | `{'a': 10}` | `12` | 辅助函数 `_pyN`（模块级定义，调用处 `_pyN(...)`） | let 绑定 + return |
| `py{ let s = $.a; let t = s * 2; return t + 1 }` | `{'a': 5}` | `11` | 辅助函数 `_pyN`（模块级定义，调用处 `_pyN(...)`） | 多条 let 链式依赖 |
| `py{ return len($.a) }` | `{'a': [1, 2, 3]}` | `3` | 辅助函数 `_pyN`（模块级定义，调用处 `_pyN(...)`） | 块内调用内置函数 |
| `py{ return [x * 2 for x in $.a] }` | `{'a': [1, 2]}` | `[2, 4]` | 辅助函数 `_pyN`（模块级定义，调用处 `_pyN(...)`） | 推导式 |
| `py{ let t = $.a; return t.upper() }` | `{'a': 'ab'}` | `'AB'` | 辅助函数 `_pyN`（模块级定义，调用处 `_pyN(...)`） | 对字段值调用方法 |
| `py{ return $.a + 1 } / 2` | `{'a': 5}` | `3.0` | 辅助函数 `_pyN`（模块级定义，调用处 `_pyN(...)`） + helper（`_dv`） | 块可以嵌在更大的表达式里 |
| `$map([1, 2], function($x){ py{ return $.a + 1 } })` | `{'a': 10}` | `[11, 11]` | 辅助函数 `_pyN`（模块级定义，调用处 `_pyN(...)`） | 写在函数字面量体内：块内只读原始行 `$.a`，每元素执行一次 |

## 12. 函数能力总览与边界

### 12.1 支持的函数（共 55 个）

A 类在行内直接求值（可接收字面量数组或行内数组值）；B 类的回调写成 `function($v){...}`，函数体是单条表达式、可以内含 `py{}`；C 类结果不可复现，但产物代码仍可复现（见 §12.4）。

| 类别 | 函数 | 个数 |
|---|---|---|
| A 类｜字符串 | `$base64decode`、`$base64encode`、`$contains`、`$decodeUrlComponent`、`$encodeUrlComponent`、`$formatBase`、`$formatNumber`、`$indexOf`、`$join`、`$length`、`$lowercase`、`$pad`、`$replace`、`$split`、`$string`、`$substring`、`$substringAfter`、`$substringBefore`、`$trim`、`$uppercase` | 20 |
| A 类｜数值 | `$abs`、`$ceil`、`$floor`、`$max`、`$min`、`$number`、`$power`、`$round`、`$sqrt`、`$sum` | 10 |
| A 类｜布尔 | `$boolean`、`$exists`、`$not`、`$type` | 4 |
| A 类｜数组与对象 | `$append`、`$average`、`$count`、`$distinct`、`$keys`、`$lookup`、`$merge`、`$reverse`、`$single`、`$sort`、`$spread`、`$values` | 12 |
| B 类｜高阶 | `$each`、`$filter`、`$map`、`$reduce`、`$sift`、`$sort` | 6 |
| C 类｜非确定 | `$millis`、`$now`、`$random`、`$shuffle` | 4 |
| **合计** | — | **55** |

### 12.2 数组与对象函数

> `$indexOf` 的语义属字符串函数（子串定位），**三行实测样例在 §6「字符串函数」表尾**；§12.1 的能力表把它归在「A 类｜字符串」。

`$count($.xs)` 的实参是路径读值，**不参与类型证明**，故一律走 helper `_ct`（不内联；内联前提见 §8）。

> 下表默认字段为 required 且声明了可证明的具体类型（判据见 §1.3）；`any` / `optional` / 未声明类型一律走 helper。

| 写法 | 输入行 | 结果 | 产物形态 | 说明 |
|---|---|---|---|---|
| `$sum([1, 2, 3])` | — | `6` | 内联（裸代码） | 数组求和 |
| `$sum($.xs)` | `{'xs': [1, 2, 3]}` | `6` | helper（`_sm`） | 行内数组值（不要求字面量） |
| `$sum([])` | — | `0` | 内联（裸代码） | 空数组求和为 0 |
| `$sum([true, 1])` | — | `2` | helper（`_sm`） | **`$sum` 不对 `bool` 做语义检查**：成功路径就是一次 C 级 `sum()`，`True` 按 `1` 计；元素必须能被 `sum()` 累加，遇不可累加元素抛 `TypeError: $sum: 元素必须是数值`。`$sum([true,1]) == 2` 是 bool 为 int 子类的自然结果，不是特例；算术算子 `+` 同口径（`true + 1 == 2`） |
| `$average([1, 2, 3])` | — | `2.0` | 内联（裸代码） | 均值 |
| `$min([3, 1, 2])` | — | `1` | 内联（裸代码） | 最小值 |
| `$max([3, 1, 2])` | — | `3` | 内联（裸代码） | 最大值 |
| `$reverse([1, 2, 3])` | — | `[3, 2, 1]` | 内联（裸代码） | 反转 |
| `$sort([3, 1, 2])` | — | `[1, 2, 3]` | 内联（裸代码） | 升序排序 |
| `$sort(['b', 'a', 'c'])` | — | `['a', 'b', 'c']` | 内联（裸代码） | 字符串默认升序 |
| `$distinct([1, 1, 2])` | — | `[1, 2]` | helper（`_ds`） | 去重、保持首次出现顺序 |
| `$append([1], [2])` | — | `[1, 2]` | helper（`_ap`） | 拼接两个数组 |
| `$keys({'a': 1, 'b': 2})` | — | `['a', 'b']` | helper（`_ky`） | 对象键列表 |
| `$values({'a': 1})` | — | `[1]` | helper（`_vl`） | 对象值列表 |
| `$merge([{'a': 1}, {'b': 2}])` | — | `{'a': 1, 'b': 2}` | helper（`_mg`） | 合并对象 |
| `$lookup({'a': 1}, 'a')` | — | `1` | helper（`_lk`） | 按键取值；键不存在得到 undefined |
| `$spread({'a': [1, 2]})` | — | `[{'a': [1, 2]}]` | helper（`_sr`） | 对象拆成对象数组 |
| `$single([1])` | — | `1` | helper（`_sg`） | 取唯一元素；长度不为 1 得到 undefined |
| `$count($.xs)` | `{'xs': [1, 2, 3]}` | `3` | helper（`_ct`） | **路径读值不参与类型证明，故走 `_ct`** |

### 12.3 高阶函数

回调最多接收两个位置参数；`$position()` 只在函数体内可用，表示当前下标。`$sort` 带比较器时，比较器返回 `true` 表示第一个参数应排在第二个参数之后（即交换两者，与 JSONata 1.8 一致）。函数体内**可以写 `py{}`**：块编译成 run() 之外的辅助函数、每元素调用一次；块内**不能引用回调参数**（`$x`），只能读原始行的 `$.字段链`。

| 写法 | 输入行 | 结果 | 产物形态 | 说明 |
|---|---|---|---|---|
| `$map([1, 2, 3], function($x){ $x * 2 })` | — | `[2, 4, 6]` | 列表推导式（元素级 `_ml`）：`[_ml(x,2) for x in [1,2,3]]` | 逐元素映射（未用 `$position()`） |
| `$map($.xs, function($x){ $x + 1 })` | `{'xs': [1, 2]}` | `[2, 3]` | 列表推导式（元素级 `_ad`）：`[_ad(x,1) for x in R['xs']]` | 实参可以是路径读值（未用 `$position()`） |
| `$map([10, 20], function($x){ $position() })` | — | `[0, 1]` | helper（`_mp`） | `$position()` 是回调内的下标，只在函数体内可用 |
| `$map([1, 2], function($x, $i){ $x + $i })` | — | `[1, 3]` | helper（`_ad`、`_mp`） | 回调第二个参数就是下标 |
| `$filter([1, 2, 3], function($x){ $x > 1 })` | — | `[2, 3]` | 列表推导式（元素级 `_gt`）：`[x for x in [1,2,3] if _gt(x,1)]` | 保留函数体为真的元素（未用 `$position()`） |
| `$reduce([1, 2, 3], function($a, $b){ $a + $b }, 0)` | — | `6` | helper（`_ad`、`_rd`） | 带初值折叠 |
| `$each({'a': 1, 'b': 2}, function($v, $k){ $k & '=' & $v })` | — | `['a=1', 'b=2']` | helper（`_cc`、`_ea`） | 按键值对回调 |
| `$sift({'a': 1, 'b': 2}, function($v){ $v > 1 })` | — | `{'b': 2}` | helper（`_gt`、`_sf`） | 过滤对象键 |
| `$sort([3, 1, 2], function($a, $b){ $a < $b })` | — | `[3, 2, 1]` | helper（`_lt`、`_so`） | 比较器返回 true 表示交换：`$a < $b` 得到降序 |
| `$sort([3, 1, 2], function($a, $b){ $a > $b })` | — | `[1, 2, 3]` | helper（`_gt`、`_so`） | `$a > $b` 得到升序（与 JSONata 1.8 一致） |
| `$map([1, 2], function($x){ py{ return 7 } })` | — | `[7, 7]` | 辅助函数 `_pyN`（模块级定义，调用处 `_pyN(...)`） | 函数体内写 `py{}`：每元素执行一次 |
| `$map([10, 20], function($x){ py{ let t = 2; return t * 3 } })` | — | `[6, 6]` | 辅助函数 `_pyN`（模块级定义，调用处 `_pyN(...)`） | 块内可用 `let` 多语句（不能引用参数 `$x`） |
| `$map([10, 20], function($x){ py{ return $.base + 1 } })` | `{'base': 100}` | `[101, 101]` | 辅助函数 `_pyN`（模块级定义，调用处 `_pyN(...)`） | 块内读原始行：写 `$.base`（该字段须在 schema 声明），不是 `$x` |
| `$map([1], function($x){ py{ return $x } })` | — | `Unsupported` | —（拒绝） | 块内不能引用回调参数：`$x` 只存在于 JSONata 表达式层。**替代方案**：把元素预先拆成 `$.字段` —— 在 `py{}` 里写 `$.field` 直接读原始行（该字段须在 schema 声明），或用 JSONata 侧先 `$map` 出所需值再用 `py{}` 处理 |

### 12.4 非确定函数

C 类函数每次调用的结果不同：**产物代码字节可复现，运行结果不可复现**。复跑证据时把 `dcg.runtime` 的时钟 / 随机源换成固定实现即可得到确定值，不需要改产物、也不通过环境变量开关。

```python
import dcg.runtime as rt

class FixedClock:
    def now_ms(self):
        return 1700000000000

class FixedRandom:
    def random(self):
        return 0.25

    def shuffle(self, xs):
        return list(reversed(xs))

rt.CLOCK = FixedClock()
rt.RANDOM = FixedRandom()
```

| 写法 | 输入行 | 结果 | 产物形态 | 说明 |
|---|---|---|---|---|
| `$now()` | — | 每次不同 | helper（`_nw`） | 当前 UTC 时间（ISO 8601，毫秒精度） |
| `$millis()` | — | 每次不同 | helper（`_ms`） | 当前 epoch 毫秒（整数） |
| `$random()` | — | 每次不同 | helper（`_rn`） | `[0, 1)` 浮点 |
| `$shuffle($.xs)` | `{'xs': [1, 2, 3]}` | 某个排列 | helper（`_sh`） | 随机洗牌 |

### 12.5 永久不支持的语法构造

> **表中报错原文里的「契约 §3.5」是历史引用**：早期版本把「永久拒绝」规则记在契约文档 §3.5；现行唯一来源是《DCG 接口实现细节文档》§3.9 与 `dcg/jx/policy.py` 的 `STRUCTURAL` 元组。报错文案未改，仅作字面登记。

| 写法 | 输入行 | 结果 | 产物形态 | 说明 |
|---|---|---|---|---|
| `1..5` | `{}` | `Unsupported` | —（build 期拒绝） | 整批序列语义（区间） |
| `/ab/` | `{}` | `Unsupported` | —（build 期拒绝） | 正则需序列匹配（正则字面量） |
| `/ab/i` | `{}` | `Unsupported` | —（build 期拒绝） | 正则需序列匹配（正则字面量（带 flags）） |
| `a#$i` | `{}` | `Unsupported` | —（build 期拒绝） | 隐式整批序号（位置绑定 `#`） |
| `a@$j` | `{}` | `Unsupported` | —（build 期拒绝） | 整批序列语义（join 绑定 `@`） |
| `a ~> b` | `{}` | `Unsupported` | —（build 期拒绝） | 整批管道语义（变换操作符 `~>`） |
| `a \| b \| c` | `{}` | `Unsupported` | —（build 期拒绝） | 整批管道语义（变换操作符 `|...|`） |
| `a^.b` | `{}` | `Unsupported` | —（build 期拒绝） | 整批排序（排序 `^`） |
| `a{ 'k': v }` | `{}` | `Unsupported` | —（build 期拒绝） | 整批分组（分组/聚合 `{...}`） |
| `$.items.{ 'k': v }` | `{}` | `Unsupported` | —（build 期拒绝） | 整批分组：`{...}` 紧跟在 `.` 后就是**路径阶段的 Reduce**（与「独立表达式的分组/聚合」是同一语法的两个位置）——`第 1 列: Reduce 路径阶段 {k:v} 不可逐行编译（契约 §3.5 要求拒绝）`（Reduce 路径阶段 `{k:v}`） |
| `('s').x` | `{}` | `Unsupported` | —（build 期拒绝） | 路径必须**锚在 `$` 上**、段只能是标识符或常量下标：把分组/字面量/调用的结果再接路径段（`('s').x`、`($.a).b`）、或把 `$`/`$$` 当路径下标（`$.a[$$]`）都会被拒绝——`不支持的路径 $x 不可逐行编译（路径只支持 $ 起头、由标识符段与常量下标段组成（长度不限））`（不支持的路径形态） |
| `$$` | `{}` | `Unsupported` | —（build 期拒绝） | `$` 已表示当前行，没有整篇文档的作用域（`第 1 列: $$ 文档根 不可逐行编译`）；`$x` 的处置**不在此表**，见 §1.1「`$foo` 形式的变量引用」段与附录 A「`$name` 的唯一命运」；`$$` 在 jx 编译层报 `Unsupported` |
| `$eval(x)` | `{}` | `Unsupported` | —（build 期拒绝） | 运行期求值任意表达式 = 运行期解释器入口 + 注入面（`$eval`） |

**生成器层（`dcg.generate()`）再拒绝的几类**——`compile_expr` 已通过，build 期才报错（契约 §7.3：这类错误只在 build 期抛出）：变量引用（`$x + 1`）与非函数值冒充调用（下表后三行）。

| 写法 | 输入行 | 结果 | 产物形态 | 说明 |
|---|---|---|---|---|
| `$x + 1` | ``{'i': 1}`` | `编译期拒绝` | —（build 期报错） | `$x` 不在 `function($x){...}` 的形参位置上 → 生成器层 `SchemaError`。**与 `$.a[$hour]` 这条完全同源**：`$name` 一律编成 `var`，**不看 `$name` 出现在下标位置还是算术位置**。取本行字段写裸名 `i + 1` 或 `$.i + 1`。**注意**：`$i + 1` 在 `i` 已声明时**同样报错**（没有同名降级），见 §1 的「`$name` 只有一种命运」段 |
| `('s'.x)('a')` | ``{}`` | `编译期拒绝` | —（build 期报错） | `(…)` 只是分组（**不是**官方 JSONata 的序列/数组构造），分组结果不可调用——`第 8 列: lit[x]() 不可逐行编译（只有 $ 函数可以被调用）`；`(*.x)('a')` 这类官方序列写法在词法层就是 `不支持的起始记号 '*'`（非函数值冒充调用） |
| `($.a)($.b)` | ``{'a': 1, 'b': 2}`` | `编译期拒绝` | —（build 期报错） | 路径结果同样不可调用：`第 6 列: $[a]() 不可逐行编译（只有 $ 函数可以被调用）`（非函数值冒充调用（路径）） |
| `(function($a){$a})('a')` | ``{}`` | `编译期拒绝` | —（build 期报错） | lambda 不是可调用表达式的头：`第 19 列: lambda() 不可逐行编译（只有 $ 函数可以被调用）`；`function(...)` 只能作 `$map` 等高阶函数的实参（§9）（非函数值冒充调用（lambda）） |

> **表外说明：序列构造 `(a, b)`。** 它**不是** `STRUCTURAL` 永久拒绝项（上表 10 键 / 13 条现象不含它），而是语法层没有「序列/数组构造」这个概念：`(…)` 只作分组，遇到逗号直接报 `Unsupported: 第 3 列: 语法错误 不可逐行编译（期望 ')'，实际 ','）`。要构造数组请写 `[a, b]`。

### 12.6 反引号段的限制

反引号段内只能是**一个 Python 表达式**：不能出现语句、赋值、`import`，也不能多语句拼接。以下写法均被拒绝：

- `` `x = 1` ``
- `` `import os` ``
- `` `1 +` ``

### 12.7 py{} 块的限制

块内只允许 `let NAME = EXPR;` 与结尾唯一一条 `return EXPR`；以下写法均被拒绝：

- `py{ $.a }`
- `py{}`
- `py{ let t = 1 }`
- `py{ return 1; return 2 }`
- `py{ return 1; let t = 2 }`
- `py{ x = 1; return x }`
- `py{ let t = 1; t += 1; return t }`
- `py{ import os; return 1 }`
- `py{ from os import path; return 1 }`
- `py{ for x in $.a: return x }`
- `py{ while True: return 1 }`
- `py{ if $.a: return 1 }`
- `py{ try: return 1 }`
- `py{ def f(): return 1 }`
- `py{ class C: pass }`
- `py{ return lambda: 1 }`
- `py{ return hourB }`
- `py{ return __import__('os') }`
- `py{ return $x }`
- `py{ return $$ }`
- `py{ return __jx_p0 }`
- `py{ let t = 1; return t`

> 以上是常见形态；**完整拒绝清单以《DCG 接口实现细节文档》§3.8 为准**——该清单还含 `with`、`global`、`nonlocal`、`del`、`assert`、`raise`、`pass`、`yield`、`await`、`async`、`match` 等（按类别归纳），两处口径一致，以接口文档为准。

**已知宽松点**：同名 `let` 重复绑定不报错，后者覆盖前者——`py{ let t = 1; let t = 2; return t }` 返回 `2`（`tests/test_jx_py_block.py::test_let_shadowing_allowed` 钉住）。

**容易误读为宽松点的行为**：`lambda` 在任何位置都被拒——语句起始与表达式内都是 `块内不支持 lambda`（`tests/test_jx_py_block.py::test_lambda_is_rejected_even_inside_expr` 钉住），**它不是宽松点**；需要函数式回调请写反引号段或普通 JSONata 高阶函数。

该白名单只挡语法，不是安全沙箱（块内仍可调用 `open` 等内置）。

## 13. 怎么扩展内置算子

§2–§12 是 DCG 支持的 JSONata 子集**现状**。本章只回答一个问题：**想给所有 schema 都能用的 `$算子`（比如再要一个 `$median`），要动哪些地方、必须满足什么约束。** 这是「改语言」，不是「在某个 schema 里加东西」—— 后者属于 DCG 的三类扩展点（表达式层外部函数 / 数据级外部函数 / 内联 Python），与 JSONata 语义无关，规范在 `DCG 整体设计方案与架构文档.md` §7.6。

> **术语**：本文说的「内置算子」= 能力表里的一个 `$name` + `dcg/runtime.py` 里实现它语义的 helper（如 `_ad`）。`+` `-` 这类**运算符**与 `$map` 这类函数在内核里是同一件事：都编成一次 helper 调用或内联的裸 Python。只写 schema 的读者读完 §13.1 的结论表即可；要改引擎的读者读全章。

### 13.1 先分清：你想加的是哪种东西

| 你的需求 | 该动哪里 | 本章相关小节 |
|---|---|---|
| 逐行、每行算一个值的新 `$算子` | `dcg/runtime.py`（helper）+ `dcg/jx/policy.py`（能力表）+ 本文 §2–§12（文档） | §13.2 – §13.6 全流程 |
| 只在这一个 schema 里用一个逐行函数 | DCG 扩展点：`Registry`（见架构文档 §7.6.1） | —— |
| 整批数据级的加工（排序、汇总、读外部数据） | DCG 扩展点：`output.before` / `output.after`（见架构文档 §7.6.2） | —— |
| 一次性、只在一个表达式的某一处用到的任意 Python | DCG 扩展点：反引号段 / `py{}`（见架构文档 §7.6.3） | —— |

结论：**只有第一行属于本章**。本章讲的算子必须满足「逐行可求值 + 运行期无解释器 + 产物字节可复现」三条硬约束，这也是为什么不能靠 `Registry` 或内联 Python 去「假装」加一个内置算子（那样只有你自己那个 schema 能用，能力表与文档也会随之漂移）。

### 13.2 现行 15 个运算符级/单参 helper + 1 个旧/备用实现（_pg）：纯 EAFP 骨架

算子的语义实现在 `dcg/runtime.py`：现行 **15 个**被生成器真实产出，另有 1 个旧/备用实现 `_pg`（`$power` 的早期实现，当前生成器**不产出**；`$power` 的 helper 兜底是 `_pw`，`math.pow`，float）。本表只覆盖**运算符级/单参 helper**；函数级 helper（`_ro`/`_sq`/`_sm`/`_mn`/`_mx`/`_av` 等）不在本表，完整 86 项名单见《DCG 接口实现细节文档》§4.4。

| 族 | helper | 失败结局 |
|---|---|---|
| 算术（5 现行 + `_pg` 旧/备用，不产出） | `_ad` `_sb` `_ml` `_dv` `_md` `_pg`（旧/备用） | 含 undefined → undefined；除零、溢出、负底数非整数次幂 → undefined；其余类型错误 → 带算子名的 `TypeError` |
| 一元（2） | `_neg` `_pos` | 一律 undefined（无文案） |
| 比较（4） | `_lt` `_le` `_gt` `_ge` | 一律 undefined（无文案） |
| 数值单参（4） | `_ab` `_fl` `_ce` `_pw` | 一律 undefined（无文案）；`_pw` 额外吞 `OverflowError` |

**纯 EAFP 骨架**（`_ad` 为原型，算术族六个同构）：

```python
def _ad(a, b):
    try:
        return a + b                   # ① 唯一的一条语句：算出来就直接返回
    except TypeError:
        if a is UNDEF or b is UNDEF:
            return UNDEF               # ② 失败后才判 undefined 传播
        raise TypeError(_ADD_MSG) from None   # ③ 剩下的才是真正的类型错误
```

写新算子时必须照这个形态：**`try` 里只有一条计算**，类型判断、undefined 传播、除零处理**全部只出现在 `except` 分支里**。理由有三条：成功路径上没有前置判断（热路径最短）；「Python 算得出来就算成功」是显式语义（`True + 2 == 3`、`'a' + 'b' == 'ab'`、`[1] + [2] == [1, 2]` 一律照收）；异常分支慢是有意取舍，不进批量热路径。

失败文案必须是 `dcg/runtime.py` 的模块级失败文案常量：`_ADD_MSG = "$add: 操作数必须是数值"`、`_SUB_MSG = "$subtract: ..."`、`_MUL_MSG = "$multiply: ..."`、`_DIV_MSG = "$divide: 除数是 0"`、`_MOD_MSG = "$modulo: 除数是 0"`、`_POW_MSG = "$power: 操作数必须是数值"`。不要按 `op` 做 dict 查表 —— 查表本身要一次 hash（实测 5 ns/失败调用），而成功路径完全用不到它。

**分域判定例外**：比较四个算子虽然也是 `try: return a < b / except TypeError: return UNDEF`，但它**不能**靠 EAFP 表达 JSONata 的「同域才可比较」——`<` 对同类型（`'a' < 'b'`、`True < True`）不抛异常，混类型才抛。所以它们的语义等价性依赖「Python 的类型不可比才抛 `TypeError`」，`_cmp_ok` 只为对外语义文档与旧调用方保留，热路径不再经它中转。

### 13.3 新增一个算子的六处改动（缺一处就漂移）

| 步骤 | 位置 | 必须做的事 |
|---|---|---|
| 1 实现 | `dcg/runtime.py` 加 `def _xx(...)`，登记进 `HELPERS`（86 项 → 87；现有 86 项**含** `_pg`、`_b` 这两个不产出的旧/备用实现，新增后它们仍保留） | 纯 EAFP 骨架（§13.2）；满足 §12.3 的 undefined 硬约束；失败文案用模块级常量 |
| 2 能力表 | `dcg/jx/policy.py` 的 `ROW_FUNCS`（A 类）/ `HIGHER_ORDER`（B 类）/ `NONDETERMINISTIC`（C 类） | 归类见 §13.5；改完并集计数会变（现 55），文档与测试里的数字要同步 |
| 3 发射 | `dcg/codegen.py` 的 `_FN`（函数名 → helper）+ `_OPS`（运算符 → helper）/ `_HIGHER`（高阶 → (helper, 回调参数个数)） | 有内建等价且能证明时进 `_inline_ok` / `_inline_text`（发裸代码），否则发 helper；`_RESERVED` 会自动并入 `_FN` / `_OPS` 的值，无需另改 |
| 4 契约 | `tests/test_jx_policy.py` 的 `CONTRACT_3_4` 手抄清单 | 这份清单**刻意与 policy 分开手写**，用来发现两侧漂移；`test_whitelist_matches_contract_3_4` 断言相等 |
| 5 语料 | `examples/jx/*.jx` 加 `=== 标题` 记录（现 15 个文件、242 例） | 每条例样都会被真实执行；§14 覆盖统计的计数随之变 |
| 6 文档 | 本文 §2–§12（该算子所属类别）+ §12.1 的「共 55 个」+ README §3.4「支持度总览」 | 正文是**手写**的，直接改 `.md`；`tests/gen_jsonata_manual.py` 只是回填工具、不被任何测试调用，跑它会把手写正文覆盖回旧渲染结果 |

配套的自检（不算「改动点」，但会立即打红）：`tests/test_docs_consistency.py` 钉住 `len(HELPERS) == 86`、`len(runtime.__all__) == 96`、A 类 46 / B 类 6 / C 类 4 / `STRUCTURAL` 10 个键、三表去重并集 55、`SOURCE_NEEDS_MATH == {"_fl","_ce","_sq"}`；`tests/test_codegen_snapshot.py` 逐名对拍 `SOURCE` 与 `HELPERS`。加一个算子必须同步改这些数字，否则「能力表 / 产物 / 文档 / 语料」四者之间必然对不上。

### 13.4 `HELPERS` / `SOURCE` / `__all__` / `SOURCE_NEEDS_MATH` 四个登记表

新 helper 只写函数不够，还要在**四个派生面**上被看见（全在 `dcg/runtime.py`）：

| 名字 | 位置 | 是什么 | 新增 helper 时要做什么 |
|---|---|---|---|
| `HELPERS` | `dcg/runtime.py` 的 `HELPERS` 表 | 名字 → 真实 callable 的**唯一实现表**（86 项） | **必须加**：漏加则产物 `from dcg.runtime import *` 拿不到它 |
| `SOURCE` | `dcg/runtime.py` 的 `SOURCE` 派生视图 | `{名字: inspect.getsource(fn)}` 的源码视图 | 自动派生，**不用改**；`_helper_source()` 取不到源码会立刻抛 `RuntimeError("无法取得 helper <名> 的源码: ...")`，绝不静默留空 |
| `__all__` | `dcg/runtime.py` 的 `__all__` 派生面 | `import *` 的导出面（96 名） | 自动派生（`set(SOURCE) | set(HELPERS) | {"_cmp_ok"} | 常量名`），**不用改**；写 `import *` 时下划线名默认不导出，所以表必须显式列出 |
| `SOURCE_NEEDS_MATH` | `dcg/runtime.py` 的 `SOURCE_NEEDS_MATH` 常量 | 「会被内联、且源码依赖 `math`」的 helper 集合（`{"_fl","_ce","_sq"}`） | **不用改，也不许让生成器读它** —— 见下 |

两条必须记住的判定：

* **`SOURCE` 是派生的，不能手抄。** 历史负担：手抄一份源码文本曾产出大量漂移（`_cn`/`_ix_get`/`_jn` 语义相反、`_N` 引用不存在的 `_NNN`、`_fl`/`_ce`/`_sq` 缺 `_m`）。派生后这类漂移在构造上不可能发生。
* **产物 `import math` 的判据是现扫产物文本里的 `math.`，生成器不读 `SOURCE_NEEDS_MATH`。** 这个名字今天只有**测试自检不变量**一个身份（钉住「哪些 helper 会内联出 math 依赖」）。历史上 codegen 曾拿它当查表依据：`_fl`/`_ce` 改成模块级别名 `_M = math` 之后，源码里不再有 `"math"` 字面量，集合静默从 3 个缩成 `{"_sq"}`，内联后产物 `NameError: _M`（D-MATH-01）。所以派生式同时认 `"math"` 与 `"_M."` 两种拼法，并显式排除从不被内联的 `_pw`（`$power` 内联直接发 `a**b`）。

新增 helper 时的实操建议：如果它要 `math`，直接用模块级别名 `_M.xxx`（`_M` 是 `dcg/runtime.py` 的内部模块级别名；不要每次执行 `import math as _m`，实测约 40 ns 模块查找开销）。**这不影响产物 `import math` 判据**：helper 源码（`SOURCE`）只用于判定哪些 helper 名被引用、生成器指纹与测试自检，**从不内联进产物**；真正会内联出 `math.` 的是代码生成器的表达式模板（`$floor`/`$ceil`/`$sqrt` 可证时发 `math.floor(...)` 等），判据仍是现扫产物文本里的 `math.` 字面量。所以：新增 helper 用 `_M.` 是安全的；但**不要给产物补 `import math as _M`**（产物侧只认 `math.`）。

### 13.5 三类函数的定义差异（不能随便换类）

| 类别 | 白名单 | 回调 | 确定性 | 产物形态 | 例 |
|---|---|---|---|---|---|
| A 类行内 | `ROW_FUNCS` 46 个（字符串 20 / 数值 10 / 布尔其他 4 / 数组与对象 12，`$sort` 同属 A/B） | 无 | 确定 | 可证时发裸代码（`len()`、`in`、切片），否则发 helper（`_FN` 映射） | `$length` `$sum` `$sort` |
| B 类高阶 | `HIGHER_ORDER` 6 个 | **必须**函数字面量，最多 2 个位置参数 | 确定 | `$map`/`$filter` **未使用 `$position()`** 时去调用层发列表推导式（`[_ml(x,2) for x in xs]`），其余场景（用到 `$position()`、`$reduce`、`$each`、`$sift`、带比较器的 `$sort`）才 helper + lambda | `$map` `$filter` `$sort` |
| C 类非确定 | `NONDETERMINISTIC` 4 个 | 无（`$shuffle` 除外） | 不可复现 | 走 `rt.CLOCK` / `rt.RANDOM`，可注入固定实现 | `$now` `$millis` `$random` `$shuffle` |

分组以 §12.1 为准，已核对：`$indexOf` 归字符串函数（`dcg/jx/policy.py` 的 `ROW_FUNCS` 注释与 §12.1 一致）。

选择规则：纯感知输入、逐行确定 → A 类；需要把别的表达式当参数（回调）→ B 类；结果随时间/随机源变化 → C 类。**错领会破坏可复现性契约**：C 类算子如果写进 A 类，同一份输入两次运行会给出不同产物结果却没人知道；B 类算子如果写进 A 类，函数字面量会在编译期失去绑定位置。

### 13.6 算子必须满足的语义硬约束

任何新增算子都必须与引擎既有语义对齐。下面这些是**机器可读**的（`dcg/runtime.py` 的 `UNDEFINED_SEMANTICS` 六条 + 汇总表），改动时以代码为准：

1. **undefined 是独立单例对象**，不是 `0`、`0.0`、`False`、`None`、`''`；比较必须用身份比较 `is`，产物里任何 `_U ==` 写法都会被 `tests/test_sentinel_integrity.py` 打红。
2. **传播规则六条**：`&` 不传播（undefined 当空串）；`or` / `and` 参与；`=` / `!=` 遇 undefined 返回 `false`；大小比较遇 undefined 返回 undefined；数组字面量跳过 undefined；对象字面量丢弃 undefined 键。
3. **不把异常静默吞成错值，但「抛错」只约束算子级 helper**：算术/比较/逻辑/拼接等算子的 EAFP 默认规则是——含 undefined 的操作数传播 undefined，否则抛带算子名的 `TypeError`（如 `'a' + 1` → `TypeError: $add: 操作数必须是数值`）。**函数级 helper 按各自函数语义**处理非预期类型，多数返回 `undefined`（`$length(5)`、`$floor('a')`、`$abs('a')`、`$indexOf(5,'x')`、`$sqrt(-1)`），少数有专属错误（`$sum` 元素不可累加 → `TypeError: $sum: 元素必须是数值`）。以各函数表为准，不要把算子级的「两种结局」套到函数级 helper 上。
4. **`type` 是声明不是转换**：算子不得偷偷把 `str` 变成 `int`；需要转换就必须让用户显式写 `$number` / `$string`。
5. **`null` 是值不是缺失**：`null + 1` 报 `TypeError: $add: 操作数必须是数值`（不是 undefined，也不是 1）。

> **`$sum` 与 `bool` 的边界**：`$sum([1, 'a'])` 抛 `TypeError: $sum: 元素必须是数值`；但 `$sum([true, 1])` 返回 `2`。它跟随 Python `sum()` 的表现，允许 `bool` 混入并当作 `1` 累加；算术算子 `+` 也是同一口径（`true + 1 == 2`）。**对 `bool` 宽容是全局规则，不是 `$sum` 独有的取舍**。详见 §12.2、接口文档 §3.8。

### 13.7 只读的确定性事实（改前先看）

| 事实 | 值 | 断裂点（改了会红的地方） |
|---|---|---|
| runtime helper 数 | 86（`HELPERS`） | `tests/test_docs_consistency.py`（`len(HELPERS) == 86`） |
| `runtime.__all__` 导出面 | 96 | 同上 |
| 三个能力表的并集 | 55（46 + 6 + 4 − 1，`$sort` 同属 A/B，并集只计一次） | `tests/test_jx_policy.py`、`tests/test_docs_consistency.py` |
| 裸名函数别名 | `abs`, `bool`, `float`, `int`, `len`, `str`（`BARE_CALL_ALIASES`，6 个） | `tests/test_jx_policy.py` |
| 结构构造永久拒绝 | `bind_join`, `bind_pos`, `eval`, `group`, `path`, `range`, `reduce`, `regex`, `sort`, `transform`（十类，10 个键；`$$` 另在 jx 层拒绝、`$x` 不在表内） | `tests/test_jx_policy.py` |
| 需要 `math` 的 helper | `_fl` `_ce` `_sq`（`SOURCE_NEEDS_MATH`） | `tests/test_codegen_snapshot.py`、`tests/test_docs_consistency.py` |
| 保留字段名 | `rows` `row` `r` `out` `ap` `_`（`_` 前缀整体禁用） | `dcg/schema.py::_check_fname` |
| undefined 语义条数 | 6 条 | `dcg/runtime.py::UNDEFINED_SEMANTICS` |
| 语料例数 | 242 例 / 15 个 `.jx` 文件 | `tests/test_jsonata_manual.py`、本文 §14 |
| 生成一次 `compile()` | 恰好 1 次 | `tests/test_codegen_snapshot.py` |

### 13.8 命名与产物纪律

- helper 名一律 `_` 开头、以两字符为主（`_S` / `_ad` / `_ix_get`），因为它们是产物体积的一部分；用户字段名撞上即 `SchemaError`（`rows` / `row` / `r` / `out` / `ap` / `_` 被禁用，`_` 前缀整体禁用）。
- 生成器占位符一律 `__jx` 前缀：`__jx_row`（裸 `$`）、`__jx_p<i>`（`py{}` 形参）、`__jx_f<i>`（`$.` 链占位）、`__jxbt<i>`（块内反引号段）；用户在表达式里写 `__jx` 开头会被直接拒绝。
- 产物必须字节可复现：helper 表、`FUNCTION_GROUPS`、`_RESERVED` 等集合按名排序后再发射，不许依赖 `dict` / `set` 的插入序。

### 13.9 扩展时不可越的三条红线

1. **不加运行期解释器**：`$eval` 永久拒绝（否则等于在产物里装一个解释器入口 + 注入面）。新增算子必须是编译期就能定形的确定性代码，不是「运行期按名字查表再求值」。
2. **不引入运行期分派表**：`dcg/runtime.py` 的每条算子都是独立函数，没有 dict-of-lambda 分派；派发脚手架会破坏**接口文档 §4.2 规则 7**（optional 守卫恰好一次）的计数判据（开销见架构 §7.5 策略二的微基准）。新增算子请直接写函数、进 `HELPERS`，不要「先查表再调用」。
3. **不在产物里做类型转换**：`type` 是声明不是转换，算子不得偷偷把 `str` 变成 `int`；需要转换就必须让用户显式写 `$number` / `$string`。

> 想在**不改引擎**的前提下把自己的函数接进某个 schema，请用 DCG 的三类扩展点：表达式层外部函数（`Registry`）、数据级外部函数（`output.before` / `output.after`）、内联 Python（反引号段 / `py{}`）。三者的定义、产物形态与边界见 `DCG 整体设计方案与架构文档.md` §7.6「DCG 的三类扩展点」。

## 14. 覆盖统计

| 文件 | 主题 | 例数 | 其中 gap / defect / fixed |
|---|---|---|---|
| 01_literals.jx | 字面量与基础值 | 12 |  |
| 02_arithmetic.jx | 算术运算 | 11 |  |
| 03_compare_logic.jx | 比较、逻辑与真值 | 13 |  |
| 04_concat.jx | 字符串连接 & | 5 |  |
| 05_paths.jx | 路径、下标与行内取值 | 17 |  |
| 06_string_funcs.jx | 字符串行函数 | 15 |  |
| 07_number_funcs.jx | 数值函数、类型与存在性 | 18 |  |
| 08_array_object.jx | 数组与对象构造 | 6 |  |
| 09_conditional.jx | 条件表达式 ? : | 6 |  |
| 10_backtick.jx | 反引号内联 Python | 8 |  |
| 11_rejected.jx | 编译期永久拒绝清单（DCG 接口实现细节文档 §3.10） | 23 |  |
| 12_funcs.jx | 数组 / 高阶 / 字符串与数值函数（全部已支持） | 41 |  |
| 13_known_defects.jx | 仍然存在的缺陷（运行期错误结果 / 异常） | 0 |  |
| 14_fixed_regressions.jx | 已修复（回归保护） | 33 | fixed 33 |
| 15_py_block.jx | py{} 多语句代码块（task-7） | 34 |  |
| **合计** | 正常 209 ｜ 未实现(gap) 0 ｜ 仍存缺陷(defect) 0 ｜ 已修复(fixed) 33 | **242** |

## 15. 已知缺陷与回归保护

### 15.1 未修缺陷：当前为空（`state: defect` 计数为 0）

当前没有故意断言错误行为的用例：已知缺陷全部修复，且都迁到 §15.2 作为回归保护。再出现回归时，对应用例会坏而不是继续正确——不需要专门维护一份「当前错误行为」清单。

### 15.2 已修复（`state: fixed`，回归保护）

共 33 条，断言的是**正确值**（源：`14_fixed_regressions.jx`）；曾经是错误行为，修复后重新冻结，再次退化就会变红。

| 表达式 | row | 实测（正确值） | 修复前 / 说明 |
|---|---|---|---|
| a in [1, 2, 3] | {'a': 2} | true | runtime 里 `_in` 的名字与参数撞车，对不可调用对象发起调用（`'list' object is not callable`）。修复后得到 True，与官方一致。 |
| null | {} | null | 生成码引用未定义名 `u`（NameError）。修复后得到 JSONata 的 null，与官方一致。 |
| null + 1 | {} | TypeError: $add: 操作数必须是数值 | 接口文档 §4.4「附加三条」：`null` 参与算术是**错误**（不是 undefined）。NameError `u`；修复后是明确的 TypeError。官方 jsonata 同样报错（JException），措辞不同但语义一致：都是"错误"。 |
| a > 1 ? 'big' : 'small' | {'a': 0} | 'small' | 生成码 `v=(_gt(a,1) and (_t1:='big') or _t1)`，走假分支时 `UnboundLocalError: _t1`。 |
| a ? 'y' : 'n' | {'a': 0} | 'n' | 同上（`a` 为 0 时取假分支）。 |
| $number(s) | {'s': '42'} | 42 | 生成码引用未定义名 `_NNN`（NameError）。修复后得 42，与官方一致。 |
| z = 0 | {'z': 0} | true | `_U = 0` 且 CPython 小整数缓存使 `0 is _U` 为真 ⇒ 任何取值为 0 的字段都被 `_eq` 当成 undefined（契约 §4.4 第 3 条：遇 undefined 返回 False）。修复后哨兵是唯一对象，0 就是 0。 |
| z != 0 | {'z': 0} | false | 同上一类。 |
| z < 5 | {'z': 0} | true | 同上一类（曾被当成 undefined，比较返回 undefined）。 |
| z + 1 | {'z': 0} | 1 | 同上一类（曾被当成 undefined 而传播 undefined）。 |
| $not(z) | {'z': 0} | true | JSONata `$not(0)` = true。 |
| $exists(z) | {'z': 0} | true | 0 是存在的值（`$exists` 只判断是否 undefined）。 |
| $type(z) | {'z': 0} | 'number' | 修复后 0 的类型是 'number'，与官方一致。 |
| $boolean(a) | {'a': 0} | false | JSONata `$boolean(0)` = false（与官方一致）。 |
| false = false | {} | true | 生成码里 `false` 是 int `0`，与哨兵同一对象 ⇒ False。修复后 `false` 是真正的 `False`。 |
| [false] | {} | [false] | 返回 `[]`（契约 §4.4 第 5 条把 0 当 undefined 跳过）。修复后 `[False]`。 |
| [false, true] | {} | [false, true] | 返回 `[1]`（`false` 被丢掉、`true` 退化成 int 1）。 |
| $type(true) | {} | 'boolean' | 这一组是 01_literals.jx 布尔两例的回归保护：布尔字面量必须编成 Python `bool`，`$type`/`$string`/`$boolean` 与数组构造都依赖它。 |
| $type(false) | {} | 'boolean' |  |
| $string(true) | {} | 'true' | 若 `true` 被编成 int 1，这里会变成 '1' —— 这正是要防住的退化。 |
| $string(false) | {} | 'false' |  |
| true and false | {} | false |  |
| true or false | {} | true |  |
| $not(true) | {} | false |  |
| $not(false) | {} | true |  |
| 1 = true | {} | false | 官方 jsonata 同样是 False：数字与布尔是不同类型，不能相等。 |
| $type(0) | {} | 'number' |  |
| 0 = 0 | {} | true | 字面量 0 与字段取值 0 现在行为一致（原缺陷只影响取值路径）。 |
| z ? 'y' : 'n' | {'z': 0} | 'n' | JSONata 真值规则：0 为假。注意区分：0 **不是** undefined（`$exists(0)` 仍为 true）。 |
| `math.floor($.a * 1.2)` | {'a': 3} | 3 | 生成码只 `from dcg.runtime import *`，没有 `import math`，原样嵌入的段在运行期 NameError。修复后凡产物文本用到 `math.` 都会补 `import math`，得到 3。 |
| $.m[0] | {'m': None} | TypeError: 'NoneType' object is not subscriptable | v2.1 起 **required 字段一律裸链**（`R['m'][0]`，`null[0]` 抛真实 `TypeError`）。要容错请声明 `optional: true`：**含整数下标的路径在 `optional` 下整条走 `_ix_get`**（`.get` 快链只对纯名字段成立，规则见接口文档 §4.4 的「四种读法一张表」），`null[0]` 得 `null`；若同一路径是纯名字段且声明了具体 `type`，则走 `.get(k,_U)` 快链，中间层不是字典时仍抛 `AttributeError`。 |
| $round(n) | {'n': 2.5} | 3 | 内联成 Python `round()`，得 2。修复：`$round` 不再内联，一律走 `_ro`（Decimal ROUND_HALF_UP），与 JSONata 的 round half away from zero 一致。 |
| $count('abc') | {} | 1 | 返回 undefined。修复：`_ct` 对非容器一律按 1 计（`$count('abc')` = 1、`$count({'k':1})` = 1、`$count([1,2,3])` = 3）；与 `$length` **不同**——`$length(5)` 返回 undefined（长度只对字符串/数组/对象有定义）。 |

## 附录 A：易错点速查

| 事项 | 说明 |
|---|---|
| 裸名 | `hourB` = 本行已声明字段，不是字符串键、也不是未定义变量 |
| 下标里的裸名 | `$.a[b]` 里的 `b` 是字段引用；字符串键要写 `$.a['b']` |
| `$name` 的唯一命运 | **一律编成 `var` 节点**（没有同名降级——`$i` 不是字段 `i`），逐行模式没有变量作用域 → **生成器层**报 `SchemaError`。`$$`（整篇文档根）则在 **jx 编译层**报 `Unsupported`——两者抛出层不同，但都在 build 期拒绝 |
| undefined vs null | `null` 是值，会输出、参与构造；undefined 是「没有值」的哨兵，出现在输出里时原样显示为 `undefined` |
| 取值缺失 | 非 optional 字段缺键 → 运行期 `KeyError`；声明 `optional: true` 的字段缺值不抛：位置/字典/rows/list 位置行落 `null`（`None`），`object` 嵌套字段丢弃该键（`_ob`）；`value` 与 `expr` 同形；有 `default` 时落默认值 |
| `$foo` 未声明 | **一律** build 期 `SchemaError`（变量形态，逐行模式无作用域）；声明同名字段也不降级 |
| `$exists` 不能探测缺失键 | **未声明 `optional` 且未设 `default`** 的字段里 `$.missing` 是裸链 `R['missing']`，取键本身抛 `KeyError`，`$exists` 没机会执行；**声明了 `optional: true` 或设了 `default`** 时整链走守卫，缺键得 undefined，`$exists($.missing)` 返回 `false`。它对取到的值只判 undefined（`null` 也算存在） |
| `&` 与 `+` | `&` 连接（undefined/null 当空串）；`+` 是算术（null 参与报错） |
| 比较遇 undefined | `=`/`!=` 返回 false；大小比较返回 undefined |
| 布尔 | `true`/`false` 是布尔值，不是 1/0；`1 = true` 为 false |
| `$round` | half away from zero（半值远离零）：`$round(2.5)` = 3、`$round(3.5)` = 4，与 JSONata 一致（不由 Python `round()` 承担） |
| `$count` 对标量 | 非容器标量按 1 计：`$count('abc')` = 1、`$count(5)` = 1；只有 undefined 得到 undefined。**不要类推到 `$length`**：`$length(5)` 是 undefined |
| py{} 不是沙箱 | 只做语法约束，不做安全隔离 |

