# JSONata 语法（DCG 支持范围与示例）

> **读者**：使用 DCG 编写 schema 的开发者
> **定位**：本文是 DCG 支持的 JSONata 子集**唯一权威清单**：每条给出写法、输入行与结果，全部实跑校验；实现必须与本文一致。
> **范围**：只讲表达式层的支持范围与示例；schema 结构、产物形态、字符预算见 `DCG 整体设计方案与架构文档.md`，接口细节见 `DCG 接口实现细节文档.md`。

## 0. 一页速览

| 问题 | 答案 |
|---|---|
| 本文是什么 | DCG 支持的 JSONata 子集：按类别列出写法、输入行与结果，全部实跑校验 |
| 支持范围 | 54 个函数：A 类行内 45 / B 类高阶 6 / C 类非确定 4（`$sort` 同属 A/B）；永久不支持的构造见 §12.5 |
| 用在哪里 | schema 的 `expr` 表达式、`value` 取值路径、反引号段，以及 `py{}` 块内部的表达式层 |
| 求值模型 | 按**行**求值：表达式在 build 期编译进生成码，运行期无解释器；字段按依赖关系求值、前向引用合法 |
| 怎么验证 | 语料 234 条例样逐条 `dcg.generate` → `exec` → `run`；本文与渲染结果逐字节一致 |
| 从哪看起 | §1 阅读前须知 → §§2–11 逐类语法 → §12 支持面与永久拒绝 → §13 覆盖统计 → §14 缺陷与回归 |

## 1. 阅读前须知

**undefined 与 null 不同**：
- `null` 是一个**值**：可以输出、可以放进数组；参与算术会报错。
- undefined 表示「没有值」，是表达式层的哨兵：例如 `$count(标量)` 这类场景会得到 undefined；它出现在输出里时原样显示为 `undefined`，`&` 连接时当空串，比较规则见 §5。
- **取值缺失不是 undefined**：非 optional 字段直接按键取值，行里缺键会在运行期抛 `KeyError`（`list` 下标越界抛 `IndexError`）；只有声明 `optional: true` 的字段/表达式缺值时才输出 `null`，不抛异常。
- `$foo` 必须是本行字段名，否则 build 期报 `SchemaError`。`$.missing` 这类数据键 build 期不检查，运行期缺键即 `KeyError`——所以 `$exists($.a)` 也无法探测缺失键（取键本身就会抛）；需要容错时请在数据准备侧保证键存在，或用 optional 字段。

**字段按依赖关系求值**：字段在 schema 里的声明顺序只决定输出结构；计算顺序由字段之间的引用关系决定，前向引用（引用后声明的字段）合法。依赖成环或引用不存在的字段在 build 期报 `SchemaError`。

**产物形态**：每条示例标注生成的 Python 产物形态——「内联（裸代码）」= 直接发 Python 表达式、不调 helper；「helper（`_ad`…）」= 调用 `dcg.runtime` 的专用函数（保留 undefined / 类型守卫）；「内联 Python」= 反引号段或 `py{}` 原样嵌入。能否发裸代码只取决于**操作数类型能否在 build 期证明**：字面量、required 且已声明类型的字段可以裸；`any`、optional、未声明类型必须走 helper。比较 / 逻辑 / 条件同理：两侧类型同类可证时发裸表达式（`(a<b)`、`(a and b)`、`(x if c else y)`），只有 JSONata 特例才留 helper——混合类型相等（`1 = true` 为 `false`）、可能 `undefined` 的操作数（undefined 传播语义）。有语义一致的 Python 内建等价的列函数也发内建表达式（`len()`、`abs()`、`in`、`split()`、`partition()`、`replace()`、`ljust()`、切片、`sum/max/min`、`bool`、`not`、`math.floor/ceil`、`round()`、`a**b`、`math.sqrt`）；`$number`、`$join`、`$type`、`$exists` 等无逐项等价或语义有差异的仍走 helper；`$sqrt` 只在可证非负时内联（负数走 helper 的 undefined 语义）。

**裸名即字段**：`hourB`、`$.hourB` 读的都是本行字段 `hourB`；`$i` 不是真正的变量绑定，在本行内等价于字段 `i`。**下标里的裸名同样是字段**：`$.a[b]` 等价于 `$.a[$.b]`；要按字符串键取值请写引号：`$.a['b']`。

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

路径段可以引用本行**其它字段**的值：`$.a[hour].v` 里的 `hour` 可以是另一个字段加工出的结果，用来做动态键/下标。字段之间按**依赖关系**求值，声明顺序只决定输出结构（前向引用合法）；依赖成环或引用不存在的字段在 build 期报 `SchemaError`。

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
| `$.a[$i]` | `{'a': [10, 20], 'i': 1}` | `20` | helper（`_ix_get`） | 变量写法取本行字段 i 作下标 |
| `$.a[$.b]` | `{'a': [10, 20], 'b': 0}` | `10` | helper（`_ix_get`） | 路径作下标 |
| `$.a[b]` | `{'a': [10, 20], 'b': 1}` | `20` | helper（`_ix_get`） | **下标里的裸名也是字段引用** |
| `$.a[hour].v` | `{'a': {'x': {'v': 42}}}` | `42` | helper（`_ix_get`） | **跨字段取值**：`hour`（`expr` 算出 `'x'`）作动态键 |
| `$.a[$hour].v` | `{'a': {'x': {'v': 42}}}` | `42` | helper（`_ix_get`） | `$hour` 变量写法等价（`hour` 须是本 schema 的字段；前向引用合法） |
| `$.a[hour]` | `{'a': [10, 20, 30]}` | `20` | helper（`_ix_get`） | 整数下标：`hour = 1` 时取第 2 个元素 |
| `$.a['b']` | `{'a': {'b': 7}}` | `7` | 内联（裸代码） | 带引号 = 常量键 |
| `$.a.{hour}.v` | `{'a': {'x': {'v': 42}}}` | `Unsupported` | —（拒绝） | `{...}` 是对象构造/分组语义，不支持；动态键请用 `[...]` |
| `$` | `{'a': 1}` | `{'a': 1}` | 内联（裸代码） | 整行（根） |
| `$i + 1` | `{'i': 1}` | `2` | 内联（裸代码） | 变量写法 $i 在本行内等价于字段 i |

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

## 7. 数值函数

| 写法 | 输入行 | 结果 | 产物形态 | 说明 |
|---|---|---|---|---|
| `$number(s)` | `{'s': '42'}` | `42` | helper（`_N`） | 字符串转数字 |
| `$abs(n)` | `{'n': -3}` | `3` | 内联（裸代码） | 绝对值 |
| `$floor(n)` | `{'n': 3.7}` | `3` | 内联（裸代码） | 下取整 |
| `$ceil(n)` | `{'n': 3.2}` | `4` | 内联（裸代码） | 上取整 |
| `$round(n)` | `{'n': 3.5}` | `4` | 内联（裸代码） | 四舍五入（注意 $round(2.5) = 2，见下方说明） |
| `$sqrt(n)` | `{'n': 9}` | `3.0` | helper（`_sq`） | 平方根 |
| `$power(a, b)` | `{'a': 2, 'b': 10}` | `1024` | 内联（裸代码） | 幂 |

## 8. 类型、存在性与计数

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
| `$count(xs)` | `{'xs': [1, 2, 3]}` | `3` | 内联（裸代码） | 计数（字段数组） |

## 9. 数组与对象构造

| 写法 | 输入行 | 结果 | 产物形态 | 说明 |
|---|---|---|---|---|
| `[a, b]` | `{'a': 1, 'b': 2}` | `[1, 2]` | 内联（裸代码） | 引用字段构造数组 |
| `[a, b, a + b]` | `{'a': 1, 'b': 2}` | `[1, 2, 3]` | 内联（裸代码） | 数组内可写表达式 |
| `[$.m, a]` | `{'m': null, 'a': 1}` | `[null, 1]` | helper（`_ar`、`_ix_get`） | null 是值，保留在数组里 |
| `{'k': a}` | `{'a': 1}` | `{'k': 1}` | 内联（裸代码） | 对象字段 |
| `{'sum': a + b}` | `{'a': 1, 'b': 2}` | `{'sum': 3}` | 内联（裸代码） | 对象值可以是表达式 |

## 10. 反引号内联 Python

反引号段内是逐字嵌入的 Python **单表达式**（build 期只做语法校验，不执行）；段外仍是 JSONata。段内的 `$.路径` 与裸名都是字段引用。

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
| `py{ return int($.a) }` | `{'a': '7'}` | `7` | 内联 Python + helper（`_ix_get`） | 最小形态：一条 return |
| `py{ let t = $.a * 1.2; return int(t) }` | `{'a': 10}` | `12` | 内联 Python + helper（`_ix_get`） | let 绑定 + return |
| `py{ let s = $.a; let t = s * 2; return t + 1 }` | `{'a': 5}` | `11` | 内联 Python + helper（`_ix_get`） | 多条 let 链式依赖 |
| `py{ return len($.a) }` | `{'a': [1, 2, 3]}` | `3` | 内联 Python + helper（`_ix_get`） | 块内调用内置函数 |
| `py{ return [x * 2 for x in $.a] }` | `{'a': [1, 2]}` | `[2, 4]` | 内联 Python + helper（`_ix_get`） | 推导式 |
| `py{ let t = $.a; return t.upper() }` | `{'a': 'ab'}` | `'AB'` | 内联 Python + helper（`_ix_get`） | 对字段值调用方法 |
| `py{ return $.a + 1 } / 2` | `{'a': 5}` | `3.0` | 内联 Python + helper（`_dv`、`_ix_get`） | 块可以嵌在更大的表达式里 |
| `$map([1, 2], function($x){ py{ return $.a + 1 } })` | `{'a': 10}` | `[11, 11]` | 内联 Python + helper（`_ix_get`、`_mp`） | 写在函数字面量体内：块内只读原始行 `$.a`，每元素执行一次 |

## 12. 函数能力总览与边界

### 12.1 支持的函数（共 54 个）

A 类在行内直接求值（可接收字面量数组或行内数组值）；B 类的回调写成 `function($v){...}`，函数体是单条表达式、可以内含 `py{}`；C 类结果不可复现，但产物代码仍可复现（见 §12.4）。

| 类别 | 函数 | 个数 |
|---|---|---|
| A 类｜字符串 | `$base64decode`、`$base64encode`、`$contains`、`$decodeUrlComponent`、`$encodeUrlComponent`、`$formatBase`、`$formatNumber`、`$join`、`$length`、`$lowercase`、`$pad`、`$replace`、`$split`、`$string`、`$substring`、`$substringAfter`、`$substringBefore`、`$trim`、`$uppercase` | 19 |
| A 类｜数值 | `$abs`、`$ceil`、`$floor`、`$max`、`$min`、`$number`、`$power`、`$round`、`$sqrt`、`$sum` | 10 |
| A 类｜布尔 | `$boolean`、`$exists`、`$not`、`$type` | 4 |
| A 类｜数组与对象 | `$append`、`$average`、`$count`、`$distinct`、`$keys`、`$lookup`、`$merge`、`$reverse`、`$single`、`$sort`、`$spread`、`$values` | 12 |
| B 类｜高阶 | `$each`、`$filter`、`$map`、`$reduce`、`$sift`、`$sort` | 6 |
| C 类｜非确定 | `$millis`、`$now`、`$random`、`$shuffle` | 4 |
| **合计** | — | **54** |

### 12.2 数组与对象函数

| 写法 | 输入行 | 结果 | 产物形态 | 说明 |
|---|---|---|---|---|
| `$sum([1, 2, 3])` | — | `6` | 内联（裸代码） | 数组求和 |
| `$sum($.xs)` | `{'xs': [1, 2, 3]}` | `6` | helper（`_sm`） | 行内数组值（不要求字面量） |
| `$sum([])` | — | `0` | 内联（裸代码） | 空数组求和为 0 |
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
| `$count($.xs)` | `{'xs': [1, 2, 3]}` | `3` | helper（`_ct`） | 数组长度 |

### 12.3 高阶函数

回调最多接收两个位置参数；`$position()` 只在函数体内可用，表示当前下标。`$sort` 带比较器时，比较器返回 `true` 表示第一个参数应排在第二个参数之后（即交换两者，与 JSONata 1.8 一致）。函数体内**可以写 `py{}`**：块编译成 run() 之外的辅助函数、每元素调用一次；块内**不能引用回调参数**（`$x`），只能读原始行的 `$.字段链`。

| 写法 | 输入行 | 结果 | 产物形态 | 说明 |
|---|---|---|---|---|
| `$map([1, 2, 3], function($x){ $x * 2 })` | — | `[2, 4, 6]` | helper（`_ml`、`_mp`） | 逐元素映射 |
| `$map($.xs, function($x){ $x + 1 })` | `{'xs': [1, 2]}` | `[2, 3]` | helper（`_ad`、`_mp`） | 实参可以是行内数组值 |
| `$map([10, 20], function($x){ $position() })` | — | `[0, 1]` | helper（`_mp`） | `$position()` 是回调内的下标，只在函数体内可用 |
| `$map([1, 2], function($x, $i){ $x + $i })` | — | `[1, 3]` | helper（`_ad`、`_mp`） | 回调第二个参数就是下标 |
| `$filter([1, 2, 3], function($x){ $x > 1 })` | — | `[2, 3]` | helper（`_fi`、`_gt`） | 保留函数体为真的元素 |
| `$reduce([1, 2, 3], function($a, $b){ $a + $b }, 0)` | — | `6` | helper（`_ad`、`_rd`） | 带初值折叠 |
| `$each({'a': 1, 'b': 2}, function($v, $k){ $k & '=' & $v })` | — | `['a=1', 'b=2']` | helper（`_cc`、`_ea`） | 按键值对回调 |
| `$sift({'a': 1, 'b': 2}, function($v){ $v > 1 })` | — | `{'b': 2}` | helper（`_gt`、`_sf`） | 过滤对象键 |
| `$sort([3, 1, 2], function($a, $b){ $a < $b })` | — | `[3, 2, 1]` | helper（`_lt`、`_so`） | 比较器返回 true 表示交换：`$a < $b` 得到降序 |
| `$sort([3, 1, 2], function($a, $b){ $a > $b })` | — | `[1, 2, 3]` | helper（`_gt`、`_so`） | `$a > $b` 得到升序（与 JSONata 1.8 一致） |
| `$map([1, 2], function($x){ py{ return 7 } })` | — | `[7, 7]` | 内联 Python + helper（`_mp`） | 函数体内写 `py{}`：每元素执行一次 |
| `$map([10, 20], function($x){ py{ let t = 2; return t * 3 } })` | — | `[6, 6]` | 内联 Python + helper（`_mp`） | 块内可用 `let` 多语句（不能引用参数 `$x`） |
| `$map([10, 20], function($x){ py{ return $.base + 1 } })` | `{'base': 100}` | `[101, 101]` | 内联 Python + helper（`_ix_get`、`_mp`） | 块内读原始行：写 `$.base`（该字段须在 schema 声明），不是 `$x` |
| `$map([1], function($x){ py{ return $x } })` | — | `Unsupported` | —（拒绝） | 块内不能引用回调参数：`$x` 只存在于 JSONata 表达式层 |

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
| `$$` | `{}` | `Unsupported` | —（build 期拒绝） | `$` 已表示当前行，`$$` 不支持（当前行 `$$`） |
| `$eval(x)` | `{}` | `Unsupported` | —（build 期拒绝） | 运行期求值任意表达式 = 运行期解释器入口 + 注入面（`$eval`） |

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

## 13. 覆盖统计

| 文件 | 主题 | 例数 |
|---|---|---|
| 01_literals.jx | 字面量与基础值 | 12 |
| 02_arithmetic.jx | 算术运算 | 11 |
| 03_compare_logic.jx | 比较、逻辑与真值 | 13 |
| 04_concat.jx | 字符串连接 & | 5 |
| 05_paths.jx | 路径、下标与行内取值 | 17 |
| 06_string_funcs.jx | 字符串行函数 | 11 |
| 07_number_funcs.jx | 数值函数、类型与存在性 | 18 |
| 08_array_object.jx | 数组与对象构造 | 6 |
| 09_conditional.jx | 条件表达式 ? : | 6 |
| 10_backtick.jx | 反引号内联 Python | 8 |
| 11_rejected.jx | 编译期永久拒绝清单（DCG 接口实现细节文档 §3.9） | 23 |
| 12_funcs.jx | 数组 / 高阶 / 字符串与数值函数（全部已支持） | 41 |
| 13_known_defects.jx | 仍然存在的缺陷（运行期错误结果 / 异常） | 3 |
| 14_fixed_regressions.jx | 已修复（回归保护） | 30 |
| 15_py_block.jx | py{} 多语句代码块（task-7） | 30 |
| **合计** | 正常 201 ｜ 未实现(gap) 0 ｜ 仍存缺陷(defect) 3 ｜ 已修复(fixed) 30 | **234** |

## 14. 已知缺陷与回归保护

### 14.1 仍未修复（`state: defect`）

这些条目**故意断言当前的错误行为**，修好后对应测试就会变红：

| 表达式 | row | 实测（错误行为） | 正确语义 |
|---|---|---|---|
| $.m[0] | {'m': None} | TypeError: 'NoneType' object is not subscriptable | 应当得到 None（契约 §4.4 第 7 条：`a[0]` 对标量成立，返回 a 本身）。常量下标在产物里内联成 `None[0]`（不经 `_ix_get`），因此仍抛 TypeError；对比 `$.a[0]`（数组）正常。 |
| $round(n) | {'n': 2.5} | 2 | 应当 3。JSONata 是四舍五入（round half away from zero），Python 的 `round()` 是 banker's rounding。官方 jsonata-python 的 `$round(2.5)` = 3。 |
| $count('abc') | {} | undefined | 应当 1。JSONata 把非数组当单个元素；当前返回 undefined（而 `$count({'k':1})` 却返回 1，自相矛盾）。 |

### 14.2 已修复（`state: fixed`，回归保护）

共 30 条，断言的是**正确值**（源：`14_fixed_regressions.jx`）；曾经是错误行为，修复后重新冻结，再次退化就会变红。

| 表达式 | row | 实测（正确值） | 修复前 / 说明 |
|---|---|---|---|
| a in [1, 2, 3] | {'a': 2} | true | runtime 里 `_in` 的名字与参数撞车，对不可调用对象发起调用（`'list' object is not callable`）。修复后得到 True，与官方一致。 |
| null | {} | null | 生成码引用未定义名 `u`（NameError）。修复后得到 JSONata 的 null，与官方一致。 |
| null + 1 | {} | TypeError: $add: 操作数必须是数值 | 契约 §4.4 第 7 条：null 参与算术是**错误**（不是 undefined）。NameError `u`；修复后是明确的 TypeError。官方 jsonata 同样报错（JException），措辞不同但语义一致：都是"错误"。 |
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

## 附录：易错点速查

| 事项 | 说明 |
|---|---|
| 裸名 | `hourB` = 本行已声明字段，不是字符串键、也不是未定义变量 |
| 下标里的裸名 | `$.a[b]` 里的 `b` 是字段引用；字符串键要写 `$.a['b']` |
| `$i` 不是变量 | `$i` 等价于字段 `i`；DCG 没有变量绑定 |
| undefined vs null | `null` 是值，会输出、参与构造；undefined 是「没有值」的哨兵，出现在输出里时原样显示为 `undefined` |
| 取值缺失 | 非 optional 字段缺键 → 运行期 `KeyError`；`optional: true` 的字段/表达式缺值 → 输出 `null` |
| `$foo` 未声明 | 必须是本行已声明字段名，否则 build 期 `SchemaError` |
| `$exists` 不能探测缺失键 | 取键本身会抛 `KeyError`；它判断的是取到的值（`null` 也算 exists） |
| `&` 与 `+` | `&` 连接（undefined/null 当空串）；`+` 是算术（null 参与报错） |
| 比较遇 undefined | `=`/`!=` 返回 false；大小比较返回 undefined |
| 布尔 | `true`/`false` 是布尔值，不是 1/0；`1 = true` 为 false |
| `$round` | 当前按 Python 银行家舍入：`$round(2.5)` = 2、`$round(3.5)` = 4（与 JSONata 的 half-up 不同，属已知差异） |
| `$count` 对标量 | 当前对非数组标量返回 undefined（已知差异） |
| py{} 不是沙箱 | 只做语法约束，不做安全隔离 |

