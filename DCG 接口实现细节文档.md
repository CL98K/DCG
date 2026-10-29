# DCG 接口实现细节文档

> **DCG 是什么**：DCG 是一个运行时代码生成器：用 JSON schema + JSONata 子集描述“怎么转”，在 build 期编译成固化的 Python 模块 `gen/<name>.py`，运行期直接运行，无解释器开销。
> **读者**：DCG 的实现者与评审者。
> **定位**：本文是 DCG 的**接口与实现契约**。设计与架构以 `DCG 整体设计方案与架构文档.md` 为准，JSONata 子集与逐条示例以 `JSONata 语法.md` 为准；本文把两者落到接口层——每个模块的公开面、数据结构、输入输出、生成码形态与错误形态。
> **范围**：IR、schema、表达式编译、代码生成、运行期 helper、公开 API、能力边界、错误格式、形态口径。
> **总原则**：build 期全量报错（带列号或 JSON 路径）；运行期没有解释器；产物确定性（同 schema 同字节，可 diff 可评审）。

---

## 目录

- [0. `dcg/` 目录结构](#0-dcg-目录结构)
- [1. IR（dcg/ir.py）](#1-ir（dcgirpy）)
- [2. Schema 校验与归一化（dcg/schema.py；契约用法见 README §2）](#2-schema-校验与归一化（dcgschemapy；契约用法见-readme-§2）)
- [3. 表达式编译（dcg/jx/）](#3-表达式编译（dcgjx）)
- [4. 代码生成（dcg/codegen.py）](#4-代码生成（dcgcodegenpy）)
- [5. 公开 API](#5-公开-api)
- [6. 能力边界](#6-能力边界)
- [7. 错误信息格式](#7-错误信息格式)

## 0. `dcg/` 目录结构

```
dcg/
├─ __init__.py          顶层公开面：8 个符号
├─ schema.py            Schema 校验与归一化
├─ ir.py                IR 节点定义与结构工具（41 个符号）
├─ jx/                  表达式层
│  ├─ __init__.py       表达式层公开面（21 个符号）
│  ├─ lexer.py          词法
│  ├─ parser.py         Pratt 语法
│  ├─ compiler.py       语法树 → IR：路径 / 函数 / 反引号 / py{} 编译
│  ├─ policy.py         能力策略表（`__all__` 8 名：7 个策略项 + Unsupported 异常）：Unsupported / ROW_FUNCS / HIGHER_ORDER / NONDETERMINISTIC / STRUCTURAL / BARE_CALL_ALIASES / is_row_func / rejection_reason
│  ├─ paths.py          路径段解析与结构工具
│  └─ registry.py       函数注册表（扩展点之一，见 §3.1 Registry）
├─ codegen.py           IR → Python 源码（plan / emit / body / module / finish 五步）
├─ runtime.py           生成码运行期 helper（undefined 哨兵、87 个 helper）
└─ build.py             构建入口
```

本文以下各节按上述顺序逐个说明该目录内的模块。

## 1. IR（dcg/ir.py）

### 1.1 IR 的形态

IR 是纯 tuple（不是 class）：可比较、可哈希、零构造开销、snapshot 友好。每个节点的末元素恒为 `loc`（`(line, col)` 或 `None`），任何节点都能直接报列号。

IR 共 41 个符号：

| 组成 | 数量 | 内容 |
|---|---|---|
| 节点常量 | 16 | `KINDS` 中的种类名（见 §1.2） |
| 算子集合 | 3 | `KINDS` / `UNARY_OPS` / `BIN_OPS` |
| 构造器 | 16 | 每个节点一个：`lit_` `field_` `index_` `root_` `var_` `unary_` `bin_` `cond_` `call_` `rfunc_` `array_` `object_` `py_` `pyblock_` `range_` `lambda_` |
| 结构工具 | 6 | `kind` / `loc_of` / `children` / `walk` / `is_const` / `key_path` |

构造器只做结构校验（参数个数、`op` 合法性、`loc` 形态），非法即 `ValueError`；策略拒绝不在这里做（由 §3 的表达式编译层在更早的位置抛 `Unsupported`）。

### 1.2 节点表

```python
("lit", value, loc)                         # Python 常量
("field", name, loc)                        # 裸标识符 = 本行字段；name 不含 "$"
("index", base, idx, loc)                   # base[idx]；idx 为 lit → Python 下标；
                                            # idx 非 lit → required 字段内联裸下标；optional/default 字段走 _ix_get（§3.4）
("root", loc)                               # "$" → 行对象本身
("var", name, loc)                          # JSONata 变量 $v；name 不含 "$"
("unary", op, operand, loc)                 # op ∈ {"-","+","not"}
("bin", op, left, right, loc)               # op 见 §1.3
("cond", test, then, orelse, loc)           # 三元 ? :
("call", fname, [args...], loc)             # fname 含 "$"，如 "$uppercase"
("rfunc", qualname, [args...], loc)         # 注册函数；qualname 形如 "pkg.mod.fn"
("array", [子节点...], loc)               # 数组字面量
("object", [(key, val_ir), ...], loc)       # 对象字面量，保序
("py", source_str, [free_field_names...], loc)      # 反引号段（不透明）
("pyblock", stmts_source, [free_field_names...], loc)  # py{} 多语句块（不透明）
("range", lo, hi, loc)                      # 区间；永远不出现在流出的 IR 里（§3.9 永久拒绝）
("lambda", [param_names], body, loc)        # 函数字面量（§3.6），可达
```

**几点硬约定**

* `field` 的成员资格不在 IR 层校验：`dcg/jx` 是纯函数、没有 schema；裸名是否为已声明字段由生成器在 build 期校验（§4.3），错误形如 `SchemaError: out.fields: 裸名 'foo' 不是本次 schema 声明的字段；本次 schema 已声明: [...]`。
* `index` 的常量链（`$.5.a.1`）与动态下标（`$.5[a].1`）是同一个节点，区别只在 `idx` 是否为 `("lit", ...)`。常量链由 `key_path` 折成段元组；动态下标在 required 字段里内联为裸下标 `base[idx]`，在 optional/default 字段里走运行期安全下标 helper `_ix_get`，不得用 `_ix`（`_ix` 是 `$indexOf`，语义完全不同）。
* `py` / `pyblock` 的 `free_field_names` 元素有两种形态：`$.` 链的链文本（不含前导 `$.`，如 `"5.a.1"`）与裸名（如 `"hourB"`）。链的解析与 JSONata 路径共用同一实现（`dcg/jx/paths.py`，路径段语义见 §4.3）。
* `pyblock.stmts_source` 是编译期已定稿的 Python 语句序列文本：块内局部名已重命名为 `__jx*` 前缀、恰好一个 `return`。codegen 落地为命名辅助函数（§4.3），不得用 lambda/IIFE 承载多语句。
* `range` 只为节点表完备而存在：`..` 在表达式编译期就抛 `Unsupported`，该节点不会被发射。

### 1.3 `bin` 的 op 与 undefined 传播

| op | 含义 | undefined 传播 | 运行期 helper |
|---|---|---|---|
| `+` `-` `*` `/` `%` | 算术 | 传播；`null` 参与算术是错误（不是 undefined） | `_ad` `_sb` `_ml` `_dv` `_md` |
| `&` | 字符串拼接 | 不传播，undefined 当空串 | `_cc` |
| `=` `!=` | 相等 | 任一侧 undefined → `False` | `_eq` `_ne` |
| `<` `<=` `>` `>=` | 大小 | 任一侧 undefined → undefined | `_lt` `_le` `_gt` `_ge` |
| `and` `or` | 逻辑 | 按 JSONata 真值；undefined 参与运算 | `_and` `_or` |
| `in` | 成员 | 与 `=` 同族（任一侧 undefined → `False`） | `_in` |

`BIN_OPS` 是 op → (含义, undefined 家族) 的唯一表；`bin_(op, ...)` 对表外 op 抛 `ValueError`。

### 1.4 结构工具

| 工具 | 签名 | 语义 |
|---|---|---|
| `kind` | `kind(node) -> str` | 节点种类名（`node[0]`） |
| `loc_of` | `loc_of(node) -> loc \| None` | 末元素为 2-tuple 时返回它，否则 `None` |
| `children` | `children(node) -> tuple` | 按节点类型给出子节点（如 `cond` → `(test, then, orelse)`） |
| `walk` | `walk(node)` | DFS 遍历所有子节点 |
| `is_const` | `is_const(node) -> bool` | 字面量、全常量数组/对象、常量一元/二元 |
| `key_path` | `key_path(node) -> tuple \| None` | 把 `field`/`index` 常量链折成段元组（`$.5.a.1` → `(5,"a",1)`）；`index.idx` 非 lit 或值非 int/str 时返回 `None` |

`key_path` 是「常量链」判定的**唯一实现**：命名、常量折叠、内联决策都走它，不得另写一套。

---

## 2. Schema 校验与归一化（dcg/schema.py；契约用法见 README §2）

### 2.1 公开面

| 符号 | 签名 | 说明 |
|---|---|---|
| `validate` | `validate(schema) -> dict` | 校验 + 归一化深拷贝；返回 §2.4 的形状 |
| `SchemaError` | `SchemaError(path, msg)` | `ValueError` 子类；`.path` / `.msg`，`str(e) == f"{path}: {msg}"` |
| `parse_value_path` | `parse_value_path(s: str) -> tuple` | `"$.5.a.1"` → `(5, "a", 1)`；`$` 后点号与方括号等价，纯数字段解析成整数下标（§2.3 路径语法）；非法即 `SchemaError` |
| `value_source` | `value_source(spec) -> str` | 返回 `"value"` / `"const"` / `"expr"`（三键恰好一个） |
| `is_optional` | `is_optional(spec) -> bool` | `spec.get("optional", False)` |
| `TYPES` | `("int64","int32","float64","str","bool","list","object","any")` | `type` 取值 |
| `SHAPES` | `("list","dict","index","rows")` | `output.shape` 取值 |
| `NAME_RE` / `FNAME_RE` | 正则 | `name` 与字段名的合法字符集 |

### 2.2 顶层四块

| 键 | 必填 | 作用 | 形态 |
|---|---|---|---|
| `name` | 是 | 产物模块名 | `[A-Za-z_][A-Za-z0-9_]*` |
| `input` | 否 | 行集合在输入数据里的路径 | `{"source": "$.a.b"}`，缺省 `$`（整份输入就是行集合） |
| `output` | 是 | 输出形态 | `shape` / `before` / `after` / `trace` 四键（§4.1）；这四个键名之外的一切键都是未知键 |
| `out` | 是 | 输出结构 | `{"type": "object", "fields": {...}}`，`fields` 必须非空 |

* 顶层出现未知键 → `SchemaError`（不允许静默忽略）。
* `input.source` 用的是与字段 `value` 完全相同的那套路径语法，语义就是「行集合在哪」：
  * 不写 / `"$"` → 整份输入就是行集合，产物 `run()` 直接 `for R in _d:`，不打头取路径；
  * `"$.a.b"` → 产物开头 `_d=_d['a']['b']`，之后照常逐行；等价于调用前先把 `data["a"]["b"]` 取出来传进去，但现在由 schema 声明、调用方不必知道数据埋在哪。
  * `"$.a.0.b"` → `_d=_d['a'][0]['b']`（纯数字段是整数下标）；`"$['x y']"` → `_d=_d['x y']`（引号段是字符串键）。
  * `"$[0].a"` → `_d=_d[0]['a']`，支持整数下标。
* 这条路径是 schema 声明的固定结构，不是逐行 optional 字段：产物用裸下标取、缺键就 `KeyError`，不套 `_ix_get` 静默降级成 `UNDEF`（与 required 字段的裸读口径一致，§2.3）。
* 裸字符串（如早期的 `"rows"` / `"index"`）不再接受，会报 `input.source: value 必须是 "$" 开头的路径`。

### 2.3 FieldSpec

每个字段恰好用三键之一声明值来源：

| 键 | 形态 | 编译结果 |
|---|---|---|
| `value` | `"$.a.b"` / `"$.5.a.1"` | 直接引用输入列的访问链（零额外开销） |
| `const` | 任意 JSON 常量 | 内联常量 |
| `expr` | JSONata 子集 + 反引号内联 Python | 编译成定制表达式 |

**路径语法**（`value` 与 `input.source` 共用同一套，实现见 `dcg/schema.py` 的 `parse_value_path`）：
`$` 之后点号与方括号等价，可任意混用、可留冗余点号：

| 写法 | 解析结果 | 说明 |
|---|---|---|
| `$` | `()` | 空路径 = 整份输入 |
| `$.a` / `$a` | `("a",)` | 标识符段；`$` 后不强制点号 |
| `$.a.0.b` | `("a", 0, "b")` | 纯数字段 = 整数下标，与 `$.a[0].b` 完全相同 |
| `$[0].a` / `$[0][1]` | `(0, "a")` / `(0, 1)` | `$` 后可直跟方括号 |
| `$["x y"]` / `$['x y']` | `("x y",)` | 引号段 = 字符串键，可含点号/空格 |
| `$.a..b` / `$.a.` | `("a", "b")` / `("a",)` | 冗余点号容忍 |

裸字符串（`"a"`、`"rows"`）不是路径；`[-1]`、`[`、`[1x]`、`[1,2]` 一律 `SchemaError`。

**段间 `$.` 不被接受**：路径语法里每一段都直接跟在 `$` 或前一段之后，写成 `$.a.$.b` 会报 `SchemaError: value 路径非法: '$.a.$.b'（剩余 '$.b'）`——它不是 `$.a.b` 的等价写法。**但在 `expr`（JSONata 表达式）里它会被吞**：`expr: "$.a.$.b"` 编成 `R['a']['b']`、`expr: "$.a.$.hour.v"` 编成 `R['a']['hour']['v']`，即段间的 `$.` 当分隔符吃掉了，`hour` 变成字面字段名——语义与预期不同，`expr` 里不要这样写。要拿某个路径值当**下标**，写 `$.a[$.b]`（产物 `R['a'][R['b']]`），见 §3.4 的动态下标。

配套声明：

| 键 | 必填 | 说明 |
|---|---|---|
| `type` | 是 | 八选一（§2.1）；**声明**不是转换：声明越具体，生成码越能省掉类型判断（§4.2 规则 4） |
| `optional` | 否 | 默认 `false`；为 `true` 时值可能是 undefined，产物生成一次守卫（undefined → `None`） |
| `default` | 否 | 字段算出 undefined 时替换成该 JSON 常量；必须与 `type` 相容、与 `fields` 互斥；设了 `default` 不再生成 `None` 分支（§4.2 规则 7） |
| `fields` | 视 `type` | `type="object"` 时必填且非空（嵌套具名对象，产物 `_ob([(字段名, 值), ...])`）；`type="list"` 时写了就是嵌套位置行（产物 `_rl([值, ...])`，按声明顺序、undefined 落 `None` 保位），不写则必须用 `value`/`expr` 给出列表来源 |
| `itype` | 否 | 只用于 `type="list"` 且不带 `fields` 的字段：声明**元素类型**（§2.1 的八选一）。它把类型传到 `$map`/`$filter` 的回调形参，函数体里的算术/比较因此发裸运算：`[v for v in xs if (v>1)]` 而不是 `[... if _gt(v,1)]`，`[(v*2) for v in xs]` 而不是 `[_ml(v,2) ...]`（每元素省一次 helper 调用）。**这是声明式承诺**：数据里真出现 `null` 或别的类型时，裸运算直接抛 `TypeError` —— 与 required 字段缺键同类，不做静默兜底。不写就等于「元素类型未知」，此时若**使用方式**已能唯一确定元素类型则按推断走（见下条），否则完全回退到 `itype` 之前的行为 |
| （推断） | — | `itype` 省略时，生成器会从**使用方式**反推元素类型（`_infer_elem_types`）：该列表被 `$sum`/`$average`/`$max`/`$min`/`$number` 用过，或它的 `$map`/`$filter` 回调形参参与了算术/顺序比较/只收字符串的函数。任一成立即视同声明。**不做运行时探测**：推断只是把「已经写出来的用法」的必然含义读出来（`$filter(xs, function($v){$v > 1})` 本身就宣告了 `xs` 是数值数组）。同一字段被推成互相冲突的类型（既当数值又当字符串）时整条推断作废、回退 helper。显式 `itype` 永远优先 |

嵌套必须显式，且只有 `fields` 一个键：`fields` 只能配 `type:"object"`（具名对象，`_ob`）或 `type:"list"`（位置行，`_rl`），且不能是空对象；不带 `fields` 的列表字段必须用 `value`/`expr` 给出来源（只写 `{"type":"list"}` 报 `SchemaError: value/const/expr 必须恰好提供一个`）。递归适用；没有「list 里出现 dict 就当嵌套」这类隐式规则。数组不逐元素重建：列表值由 `value`/`expr` 直接给出（如 `{"type":"list","value":"$.tags"}`）。要声明**元素类型**用 `itype`（见上表，可选；只声明标量类型、不描述结构，与 `fields` 互斥）。

### 2.4 归一化结果

`validate` 返回深拷贝，默认值补齐、顺序保持用户声明顺序：

```jsonc
{
  "name": "cap",
  "input":  {"source": "$"},
  "output": {"shape": "list", "before": null, "after": null, "trace": false},
  "out": {
    "type": "object",
    "fields": {
      "hourA": {"type": "int64", "value": "$.5.a.1"},
      "hourE": {"type": "int64", "expr": "$.5.a.1 + $.5.a.2"}
    }
  }
}
```

`out.fields` 的键序即声明序（Python dict 保序）；生成器只读归一化结果。字段声明了 `itype` 时该键原样保留在归一化结果里（只对 `type:"list"` 且不带 `fields` 的字段有效）。

**返回值是生成器内部形态，不得回喂**（历史裁决 B）。归一化把路径变成段元组（如 `"$.5.a.1"` → `(5, "a", 1)`）、把钩子变成二元组（`("expr", "$.rows")` / `("func", "pkg.mod.fn")`）。这些 tuple 只供 `dcg/codegen.py` 与 `dcg/ir.py` 消费，**不是可信输入**：把 `validate()` 的返回值再传给 `validate()` / `generate()` / `build()` 会立即失败，报 `SchemaError: <路径>: 收到 dcg.validate() 返回的规范化内部形态（…），不是可信输入：请把原始 schema 直接传给 generate()/build()（它们会自行校验），不要先 validate() 再把结果回喂`。正确姿势：把 `validate()` 当检查器用（看它是否抛错、或按需读取归一化结果做展示），真正生成时把**原始 schema** 交给 `generate()` / `build()`——它们内部各会 `validate()` 一次。

### 2.5 声明顺序与计算顺序

* **输出按声明序**：`list` 按字段声明顺序取值；`dict` 按声明顺序建键；嵌套 `object` 按内层声明顺序。行数据的键顺序不影响输出顺序。
* **计算按依赖关系**：字段之间通过表达式里的字段引用构成依赖；生成器在 build 期建依赖图，按稳定拓扑序求值（Kahn 式逐轮取依赖就绪字段、每轮按声明序 tie-break），与声明顺序无关；同一 schema 的求值顺序唯一确定，产物在同 `generator_version` 下逐字节可复现。
* **依赖来源**：表达式里的裸名（`hourA`）、`$.` 链里的字段名（`$.stats.ids` 依赖 `stats`、`ids` 这两段）、动态下标段里的字段名（`$.5[hour].v` 依赖 `hour`）、嵌套 `fields` 内同层兄弟的裸名（在该嵌套作用域内构成依赖）。**`$字段名` 不构成字段依赖**：`$name` 是变量形态（§3.4 的 `$v` 行），一律 `SchemaError`，不会降级成字段引用。
* **前向引用合法**：后声明的字段可以被先声明的字段引用。
* **依赖必须无环**：环 → `SchemaError` 并报出环上的字段链；引用未声明字段 → `SchemaError` 并列出可用字段名。
* **每个字段最多求值一次**：被多字段引用时绑定一次复用（内联决策见 §4.2 规则 1）。
* **作用域**：每个 `fields` 是一层独立作用域——内层嵌套字段可引用外层字段，同层兄弟可按裸名互引（前向引用合法），内层同名字段遮蔽外层（就近优先），外层不能引用内层字段名。嵌套层的环报路径到该层，如 `out.fields.doc.fields: 字段依赖成环: a -> b -> a`。
* **`py{}` 与反引号段里的 `$.字段名`**：build 期只校验「必须是已声明字段」，块内读原始行值；不构成依赖边（因此 `v = py{ $.a }` 与 `a = v + 1` 可正常生成）。反引号段里的**裸名**（如 `` `a + 1` ``）才是字段引用，读该字段的计算结果并参与依赖图。

### 2.6 硬规则与报错

1. **裸字符串不是路径**：`"a"` 必须报 `SchemaError`；`value` 只能以 `$` 开头的路径，或改走 `const`。
2. **嵌套必须显式**：嵌套结构只能用 `fields` 声明（`type:"object"` 具名对象 / `type:"list"` 位置行），不写就不展开（此时列表值必须由 `value`/`expr` 给出）；数组由 `value`/`expr` 直接给出，没有 `items` 这个键（写了即未知键报错）。

报错形态统一：`SchemaError(path, msg)`，`path` 是 JSON 路径，`str(e)` = `"{path}: {msg}"`。

```
out.fields.sum: value/const/expr 必须恰好提供一个（给了 2 个: value、const）
out.fields.hourD.type: 必须是 int64/int32/float64/str/bool/list/object/any 之一，得到 'number'
out.fields.x.value: value 必须是 "$" 开头的路径（如 "$.a.b"），得到 'a'；v2 里裸字符串不是路径
out.fields.doc.fields.b.expr: 裸名 'foo' 不是本次 schema 声明的字段；已声明: ['a', 'doc']（行内取值请写 $.foo 或 $.路径）
```

---

## 3. 表达式编译（dcg/jx/）

### 3.1 公开面

```python
# dcg/jx/compiler.py
class Unsupported(Exception):
    def __init__(self, feature: str, loc=None, msg: str | None = None): ...
    # str(e) 见 §7.2；属性 loc / col

def compile_expr(src: str, *, loc_base=(1, 1), registry=None) -> tuple:
    """JSONata 源码 → §1 的 IR。遇不支持结构抛 Unsupported（带列号）。"""

# dcg/jx/registry.py
class Registry:
    # 写入
    def fn(self, name: str, func) -> None: ...          # name 形如 "$myfn"；守卫非法名与非可调用对象（抛 ValueError / TypeError）
    def unregister(self, name: str) -> None: ...        # 幂等删除
    # 读取
    def get(self, name: str): ...                       # 未注册返回 None
    def qualname(self, name: str) -> str: ...           # 生成产物用的 "pkg.mod.fn"；不可导入的 qualname 抛 Unsupported
    def names(self) -> tuple: ...                       # 已注册名字快照
    # 容器协议
    def __contains__(self, name) -> bool: ...
    def __getitem__(self, name): ...                    # 未注册抛 KeyError（与 get 不同）
    def __len__(self) -> int: ...
```

* **`get()` 与 `__getitem__` 的差异**：`get` 未注册返回 `None`，`__getitem__` 未注册抛 `KeyError` —— 前者用于「可选查表」，后者用于「必须存在」。
* **`qualname()` 是产物可导入性的唯一守卫**：只接受「点分隔标识符序列」形式的 `__qualname__`（如 `Class.method`、`_mod.fn`）；lambda / 闭包 / 局部函数 / 推导式一律抛 `Unsupported` 并给出修法。

  产物 `_x` 分派器按「从长到短试」导入：`pkg.mod.Class.method` → `pkg.mod.Class` → … → `pkg`，逐级 `importlib.import_module` + 剩余段 `getattr`。不可导入的名字运行期才会炸；契约要求构建期全量报错，因此 `strict_qualname=True` 时 `generate()` 会先做一遍可行性探测。
* `default_registry` 是给「进程级注册」留的单例；**`compile_expr` 不会隐式使用它**（纯函数要求），必须显式传 `registry=`。

* `compile_expr` 是纯函数：同输入同输出，不读任何全局状态；注册表通过参数显式传入。
* `Registry` 只影响表达式层：注册后 `$myfn(...)` 编成 `("rfunc", "pkg.mod.fn", args, loc)`；不改变 schema 结构。
* **产物生成侧要注册表必须显式传**：`dcg.generate(schema, registry=reg)`（以及 `build(..., registry=reg)`）才会把 `$myfn` 发成模块级 `_x(...)` 惰性分派；不传（默认 `None`）时 `$myfn` **照旧报 `未知函数`**，这样 `generate()` 仍是纯函数、进程里注册过什么不影响产物字节。
* 次级公开面（模块级、非顶层导出，21 个）：`compile_expr` `compile_ast` `Unsupported` `Registry` `default_registry` `analyze_py_segment` `compile_py_block` `parse_expr` `lex` `JxSyntaxError` `ROW_FUNCS` `HIGHER_ORDER` `NONDETERMINISTIC` `parse_path` `parse_chain` `parse_path_chain` `split_chain` `chain_ir` `FIELD_REF` `count_symbolic` `RESERVED_PREFIX`。

### 3.2 词法

* `TOKEN` 正则扫描，`lex(src) -> [(kind, value, col), ...]`。
* 反引号 `` ` `` 是词法边界：段内容原样捕获到下一个未转义反引号，段内不解析 JSONata；`py{ ... }` 多语句块整体扫描为一个段。
* 必须识别：数字（含小数/指数）、字符串（单/双引号）、正则字面量 `/.../flags`、`$` / `$$` / `$name`、`$.name`、裸标识符、运算符
  `+ - * / % & = != < <= > >= and or in ? : . [ ] ( ) { } , # @ ^ | ~> ..`。
* `lang` 不是词法或 schema 概念，不存在该键。

### 3.3 语法（Pratt）

结合力（数字越大越紧）：`or 10` · `and 20` · 比较/`in` 30 · `& 40` · `+ - 50` · `* / % 60` · 一元 `- + not` 70 · 后缀 `.` `[` `(` 90 · `..` 45 · 三元 `? :` 5。

* 调用/下标/路径统一在中缀循环的后缀位置处理。
* 先判函数归属、再 emit 实参：归属查 §3.5 的四张表，避免实参里的函数名抢答拒绝原因。
* 解析结果只产出 §1 的节点；`..` 解析后立即由策略判据拒绝（`range` 不流出）。

### 3.4 路径、下标与字段引用

| 源码 | IR | 运行期 |
|---|---|---|
| `$` | `root` | 行对象本身 |
| `$.a` / `$.5` / `$.a.b` / `$.5.a.1` | `index` 常量链（任意长度） | Python 下标链 `R['a']['b']` |
| `$[expr]` / `$.a[expr]` | `index(base, <idx IR>)` | 安全下标 helper（undefined 安全） |
| `a`（裸名） | `field("a")` | 生成局部名（build 期校验已声明） |
| `$v` | `var("v")` | **变量引用**：逐行模式没有变量作用域 → 生成器在 build 期报 `SchemaError`。**没有同名降级**：`$name` 一律编成 `var` 并报错，即便 `out.fields` 里声明了同名字段（`$i` ≠ 字段 `i`）；要引用同名字段请写裸名 `i` 或 `$.i`。唯一合法的**变量**是 B 类回调的形参（§3.6） |
| `str(x)` 等 6 个裸名调用 | 归一化为对应 `$` 函数 | 同 A 类行内函数 |

* **路径长度不限**：`$.a.b.c.d`、`$.5.a.1.b` 都编成同一种下标链，不需要靠嵌套 `fields` 表达；行必须是 dict/对象。
* **动态下标在 required 字段里内联**（`base[expr]`，`expr` 非字面量）：直接生成 `base[idx]`，越界/缺键/类型不符就是真实 Python 异常（该字段没有 undefined 出口）。
* **动态下标在 optional/default 字段里走 `_ix_get(base, idx)`**：int 下标 / str 键 / 越界与类型不符 → `UNDEF`，交给守卫或默认值。**不得**路由到 `_ix`（`_ix` 是 `$indexOf`）。

动态下标有两层判定：①字段级 `optional`/`default` 决定下标访问是否走 `_ix_get`——required 字段内联裸下标 `base[idx]`，optional/default 字段走 `_ix_get(base, idx)`；②基座读取由基座自身决定——裸名字段按自身 `optional`/`type` 分档（required → 裸链 `xs=R['xs']`；optional + 具体 type → `R.get('xs',_U)` 快链 + 守卫；optional + any → `_ix_get` + 守卫），`$.` 路径基座无声明可依、一律 `_ix_get` 链。两层叠加实测：required 字段 + 路径基座 → `R['xs'][R['i']]`；optional 字段 + 路径基座 → `_t1=_ix_get(_ix_get(R,'xs'),_ix_get(R,'i'))` + 守卫；optional 字段 + required 裸名基座 → `xs=R['xs']` + `_t1=_ix_get(xs,_ix_get(R,'i'))` + 守卫。

上例中的下标写的是路径 `$.i`，而基座 `a` 声明了 `optional`，所以在 `_ix_get(base, idx)` 里 idx 也走路径读值 `_ix_get(R,'i')`。**决定下标访问是否走 `_ix_get` 的是字段级 `optional`/`default`，不是路径读值本身**：required 字段里同样的写法直接发裸下标 `R['a'][R['i']]`（路径读值只是裸表达式 `R['i']`，可能行首绑定 `_i=R['i']`）；只有 optional/default 字段才把整个下标访问包成 `_ix_get(base, idx)`。若下标改写成**裸名** `i`：只有当 `i` 是本次 schema **已声明**的字段时才成立，行首绑定 `i=R['i']` 后发 `R['a'][i]`；`i` 未声明则 build 期报 `SchemaError: 裸名 'i' 不是本次 schema 声明的字段`。另外动态下标只存在于 `expr`：写成 `value` 形态（`"$.a[$.i]"`、`"$.a[i]"`）一律 `SchemaError: value 路径下标非法`。四种叠加形态（单次引用 / 多处引用行首绑定 / 裸名已声明 / optional 基座）各自编出什么产物，见《验收报告》 §2.2 的实测对照。
* **裸名调用别名**（`BARE_CALL_ALIASES`，6 个）：`str→$string`、`int→$number`、`float→$number`、`bool→$boolean`、`len→$length`、`abs→$abs`；表外的 `a(...)` 一律 `Unsupported`。

### 3.5 函数分类与白名单

归属判定顺序（policy 唯一来源）：`ROW_FUNCS`（A 类）→ `HIGHER_ORDER`（B 类）→ `NONDETERMINISTIC`（C 类）→ `STRUCTURAL`（永久拒绝）。

**函数总数口径**：A 46 + B 6 + C 4 = 56 个条目，但 `$sort` 同时属于 A 与 B（`$sort(array)` 是行内函数，`$sort(array, function($a,$b){...})` 是带比较器的高阶形态），按名字去重后共 **55** 个函数。判定顺序保证 `$sort` 先按 A 类解析，带比较器时再进 B 类分支。

**A 类｜行内函数（46 个；类型可证时发内联表达式/内建调用，否则发运行期 helper）**

| 分组 | 函数 |
|---|---|
| 字符串（20） | `$string $uppercase $lowercase $trim $pad $contains $split $join $substring $substringBefore $substringAfter $replace $length $base64encode $base64decode $encodeUrlComponent $decodeUrlComponent $formatNumber $formatBase $indexOf` |
| 数值（10） | `$number $abs $floor $ceil $round $sqrt $power $sum $min $max` |
| 布尔/其他（4） | `$boolean $not $exists $type` |
| 数组与对象（12） | `$count $append $average $reverse $sort $distinct $merge $keys $values $lookup $spread $single` |

实参只需**逐行可求值**：字面量、字段取值、动态下标、数组/对象字面量都行；`$sum([$.5.a.1, $.a.c])` 与 `$sum($.items)` 等价，**没有**「仅限字面量数组」的限制。各函数语义见 `JSONata 语法.md` §6–§8。

> **本节只列函数清单与能力归类。** 各函数的取值语义（含 `undefined` 传播、Python 算不出来时的行为）统一见 **§4.4 undefined 语义** 与 `JSONata 语法.md` §6–§8；`$sum` 与算术算子 `_ad` 等**都按 Python 语义接受 `bool`**（不是特例，是 EAFP 自然结果），见 **§4.4 末「函数语义补充」**。

**B 类｜高阶函数（6 个，需要函数字面量，§3.6）**

`$map $filter $reduce $each $sift`，以及带比较器的 `$sort(array, function)`。`$position()` 只在 B 类函数体内可用（序列上下文）。

**C 类｜非确定函数（4 个，§3.7）**

`$now $millis $random $shuffle`。

**永久拒绝**：见 §3.9。未列入任何表的函数 → `Unsupported`（feature 为「未知函数 `$x`」）。

### 3.6 函数字面量与高阶函数

`function($x){ <表达式> }` 编成 `("lambda", [参数...], body, loc)`，由生成器编译成 Python 函数（表达式体）。函数字面量**只能作为上面六个高阶函数的实参**。放错位置分两种情形：直接当字段表达式 → **生成器层** `SchemaError`；作为调用头（如 `(function($a){$a})('a')`）→ **表达式层** `Unsupported`（`lambda() 不可逐行编译（只有 $ 函数可以被调用）`）。两者都只在 build 期抛出。

* **函数体是单条表达式**：可用行内函数、字段取值、常量与运算符；允许内含 `py{}` 块（与普通字段一样编译成 run() 之外的辅助函数）。
* **函数体内的 `py{}`**：块内不能引用回调参数（`$x` 只存在于表达式层），只能读原始行的 `$.字段链`（字段须在 schema 声明）；块编译成 run() 之外的辅助函数，每元素调用一次。
* **自由字段**：函数体引用的字段进入依赖图，在进入函数前绑定；函数参数遮蔽同名字段。
* **编译期校验**：参数名是合法 Python 标识符，且不与生成器保留名冲突；参数重复报 `SchemaError`。
* **求值**：`$map` / `$filter` / `$sort` 对行内数组逐元素调用；`$reduce` 带初值折叠；`$each` 按对象键值对回调；`$sift` 过滤对象键。全部行内完成，不需要整批状态。
* **语义基准**：以 JSONata 1.8 为语义基准（空数组、`undefined` 元素、比较器返回值约定、`$position` 语义）；**显式偏离集中见 `JSONata 语法.md` §1.4「与 JSONata 1.8 的显式偏离」表**（8 条），另有 §1.4.1 列出的 5 类模型边界差异（输出单值塌缩、函数级宽松、无函数值模型、DCG 扩展、`$string(undefined)`）。

### 3.7 非确定函数与注入接口

`$now()`（ISO 8601 UTC 毫秒时间戳）、`$millis()`（epoch 毫秒）、`$random()`（`[0,1)` 浮点）、`$shuffle(array)`（洗牌）由运行期 helper 提供，产物里只出现 helper 调用（`_nw()` / `_ms()` / `_rn()` / `_sh()`）。

* **产物仍可复现**：生成代码确定，同一 schema 逐字节一致；非确定只发生在运行结果。
* **注入接口**：运行期 helper 从模块级可替换的时钟/随机源取值（`dcg.runtime` 暴露时钟与随机源对象，测试与证据重跑用固定源替换）；不引入环境变量这类隐式全局开关，避免产物行为随宿主环境静默变化。
* **结果不可复现**：同一产物两次运行结果可能不同；使用 C 类函数的切片在证据里标注「输出含非确定值」，并用固定源重跑一次作为复跑证据。

### 3.8 反引号段与 `py{}` 块

**反引号段**（`expr` 里的 `` `...` ``）：

1. 段内必须是**单个 Python 表达式**：`ast.parse(src, mode="eval")` 在 build 期校验；违规 → `Unsupported`（带段内字符偏移），错误形如
   `第 1 列: 反引号段 Python 语法错误 不可逐行编译（invalid syntax（段内偏移 1）—— 必须是单个 Python 表达式）`。
2. 字段引用 = `$.` + 链（`$.a`、`$.5.a.1`、单段 `$.data` 也合法），或裸名（本 schema 已声明字段）；字符串字面量里的裸名不登记。
3. 解析结果登记进 `free_field_names`：链文本（不含前导 `$.`）或裸名；成员资格校验落在生成器（§4.3），不在表达式层。
4. 反引号段 = 任意 Python 执行，不是沙箱：build 期不执行代码，只 `ast.parse`；`PY_SAFE_MODULES`（25 个）只约束裸模块名（`math re json decimal datetime itertools ...`），不约束内置函数。
5. `__jx` 是表达式层保留的生成器命名空间（前缀），用户段里出现即报错。

**`py{}` 多语句块**：

* py{} 的语法白名单只有两类：`let NAME = EXPR;` 绑定（可多条、按书写顺序）与结尾唯一一条 `return EXPR`。除这两类外的任何 Python 语句起始形态一律拒绝（带列号）：① 导入（`import` / `from … import`）；② 赋值（含增强赋值、海象 `:=`）；③ 控制流（`if`/`elif`/`else`/`for`/`while`/`try`/`except`/`finally`/`with`/`match`）；④ 定义与声明（`def`/`class`/`global`/`nonlocal`）；⑤ 其他语句（`del`/`assert`/`raise`/`pass`/`yield`/`await`/`async`）；⑥ `lambda`（**任何位置**都拒，不只是语句起始）；⑦ 名字层面禁 `__import__`（唯一被禁的全局名）、`__jx` 前缀占位名、裸字段名、`$x`/`$$`。**这是权威清单**（完整规则以本节为准；常见拒绝形态与报错文案示例见 `JSONata 语法.md` §12.7）。语义边界：语法白名单只限制语句形态，不限制 `return` 表达式里能调用什么（`open()`/`eval()`/`exec()` 等内置可用）；它不是沙箱。**已知宽松点与常见误读**：同名 `let` 重复绑定时后者覆盖、不报错（`tests/test_jx_py_block.py::test_let_shadowing_allowed` 钉住）。`lambda` **不是**宽松点——语句起始与表达式内都会被拒（`块内不支持 lambda`，见 `tests/test_jx_py_block.py::test_lambda_is_rejected_even_inside_expr`）；需要函数式回调请写反引号段或普通 JSONata 高阶函数。
* **裸 `$` 是允许的**：块内独立出现的 `$`（前后都不是标识符字符 / 不是另一个 `$`）表示「当前行 / 整份数据本身」，与反引号段同口径。
  * 行内块：`$` 就是当前行；`before`/`after` 钩子块：`$` 就是 input 定位后的全量数据 / 组装后的全量结果。
  * 钩子里的 `py{}` 与行内 `py{}` 的 `$` 语义不同：`$` 是全量数据本身，`$.链` 首段**不需要**是已声明字段（它只是数据里的键），详见 §4.1 钩子小节的 `py{}` 说明（「钩子里的 py{}…」段）。
  * 实现上它必须走形参：`_pyN` 是模块级函数，看不到 `run()` 里的局部名。块内出现裸 `$` 时该块多接一个末尾形参，调用处传 `R`（行内）或 `D`（钩子）；不出现就不多接，避免产物冗余。
  * 块内也可写反引号段（`` py{ let k = `[r for r in $ if r > 1]`; ... } ``），段内裸 `$` 与块内裸 `$` 同义。
  * `$x` 与 `$$` 仍拒绝：行模式没有变量作用域，也没有整篇文档。
* 编译期把语句序列定稿为辅助函数体：局部名重命名为 `__jx*` 前缀，恰好一个 `return`。
* 生成器把它发成 `run()` **之外**的 `def _pyN(f1, f2, ...): <stmts>`，调用处传已解包字段；禁止 lambda/IIFE 承载多语句；辅助函数不得每行重建；`_pyN` 编号按源码出现顺序，禁止 random。
* 块内 `$.` 链读原始行、不进入依赖图（只校验已声明）；反引号段里的裸名是字段引用，进入依赖图。

> **schema 必须来自可信来源**：`py{}` 与反引号段同等级别——语法白名单不是安全边界（只限制语句形态，不限制 `return` 表达式里的调用），详见上方**权威清单**的「语义边界」。

### 3.9 永久拒绝

**十类结构构造 + `$$`，共 13 条现象。** 键定义在 `dcg/jx/policy.py` 的 `STRUCTURAL` 元组（`eval` / `range` / `regex` / `bind_pos` / `bind_join` / `sort` / `transform` / `reduce` / `group` / `path`，共 10 个键）；下表按**现象**逐条列（13 条），因为一个键可以覆盖多条现象 —— `regex` 一键含带不带 flags，`transform` 一键含 `~>` 与 `|...|`，`group` / `reduce` 是 `{...}` 在「独立表达式」与「`.` 后的路径阶段」两个位置的两条现象。**条数与键数不是一回事**（与 `JSONata 语法.md` §12.5 的 13 行逐条对应）。

> **关于文案里的「契约 §3.5」**：表中 jx 语法层的报错原文带「（契约 §3.5 要求拒绝）」，那是**历史引用**（早期版本把「永久拒绝」规则记在契约文档 §3.5），并不是指向本文 §3.5（本文 §3.5 是函数分类与白名单）。拒绝规则的现行唯一来源就是**本节 §3.9** 与 `dcg/jx/policy.py` 的 `STRUCTURAL` 元组；报错文案未改，只作字面登记。

| 拒绝项 | 理由 | 异常 |
|---|---|---|
| `$eval(...)` | 运行期求值任意表达式 = 运行期解释器入口 + 注入面 | `Unsupported` |
| `a..b` 区间 | 需要序列语义（生成器/迭代器），逐行编译无对应形态 | `Unsupported` |
| `/ab/` 正则字面量 | 正则编译有状态、会引入隐藏缓存；jx 语法层 `第 1 列: 正则字面量 不可逐行编译（契约 §3.5 要求拒绝）` | `Unsupported` |
| `/ab/i` 正则字面量（带 flags） | 与上一条同属 `regex` 键（feature 写作 `正则字面量`，不区分 flags）；jx 语法层同上 | `Unsupported` |
| `a#$i` 位置绑定 `#` | 隐式整批序号，逐行编译无对应形态；jx 语法层 `第 1 列: 位置绑定 # 不可逐行编译（契约 §3.5 要求拒绝）` | `Unsupported` |
| `a@$j` join 绑定 `@` | 整批序列语义；jx 语法层 `第 1 列: join 绑定 @ 不可逐行编译（契约 §3.5 要求拒绝）` | `Unsupported` |
| `a^.b` 排序 `^` | 整批排序运算符（与 A 类函数 `$sort` 是两回事）；jx 语法层 `第 1 列: 排序 ^ 不可逐行编译（契约 §3.5 要求拒绝）` | `Unsupported` |
| `a ~> b` 变换操作符 `~>` | 整批管道语义；jx 语法层 `第 3 列: 变换操作符 ~> / \|...\| 不可逐行编译（契约 §3.5 要求拒绝）` | `Unsupported` |
| `a \| b \| c` 变换操作符 `\|...\|` | 与上一条同属 `transform` 键（feature 写作 `~> / \|...\|`）；jx 语法层同上 | `Unsupported` |
| `a{ 'k': v }` 分组/聚合 `{...}` | `{...}` 出现在独立表达式位置即分组/聚合（整批语义）；jx 语法层 `第 N 列: 分组/聚合 {..} 不可逐行编译（契约 §3.5 要求拒绝）` | `Unsupported` |
| `$.items.{ 'k': v }` Reduce 路径阶段 `{k:v}` | `{...}` 紧跟 `.` 后就是路径阶段的 Reduce —— 同一个 `{...}` 的两个位置、两条现象；jx 语法层 `第 1 列: Reduce 路径阶段 {k:v} 不可逐行编译（契约 §3.5 要求拒绝）` | `Unsupported` |
| 不支持的路径形态（`path`） | 路径必须**锚在 `$` 上**、段只能是标识符段或常量下标段（长度不限）：把分组/字面量/调用的结果再接路径段（`('s').x`、`($.a).b`）、或把 `$` / `$$` 当路径下标（`$.a[$$]`）都拒绝；jx 编译层 `不支持的路径 $x 不可逐行编译（路径只支持 $ 起头、由标识符段与常量下标段组成（长度不限））`，同类 feature 还有 `路径下标非法 [...]`、`路径段非法 ...` | `Unsupported` |
| `$$`（整篇文档根） | 逐行函数只看到行与整批数据，没有第二套作用域；`$$` 在 **jx 层**即拒绝（`dcg/jx/compiler.py` 中拦 `var` 节点与调用位置的两处检查）`第 1 列: $$ 文档根 不可逐行编译（行模式没有整篇文档，只有当前行）`。它**不在** `STRUCTURAL` 的 10 个键里，是额外一类 | `Unsupported` |

**`$x`（变量引用）不属于本表**：它编成 `var` 节点、在 **生成器**里报错 —— `SchemaError: 行模式没有变量作用域：$x 不是任何函数字面量的形参（逐行函数只看到行与整批数据）；要取本行字段请写裸名 x 或 $.x`。两类异常都只在 build 期抛出，运行期都不会出现（§7.3）。**没有同名降级**：`$name` 一律编成 `var` 并在生成器报 `SchemaError`，声明了同名字段也不改判（`$i` 是变量形态、`i` 是字段形态）——判定顺序是「回调形参 → 抛 `SchemaError`」（`dcg/codegen.py` 的 `var_name`），见 `JSONata 语法.md` 的「`$name` 的唯一命运」。

**一律抛出，绝不静默降级。** feature 是「最小判据名」，msg 是原因；格式见 §7.2。

### 3.10 语义基准

* 语义基准是**官方 JSONata 1.8 规范**；JSONata 子集与逐条示例见 `JSONata 语法.md`。
* 仓库内冻结对照集 `tests/fixtures/conformance.json`：50 例（core 26 + edge 24），期望值由官方实现一次性求出后固化；**运行期不依赖 `jsonata` 包**。
* C 类非确定函数不进对照集：用注入的固定源校验类型与范围。
* 语义边界以本文 §3.5 / §3.9 与 `DCG 整体设计方案与架构文档.md` §6.2 / §6.4 为准。

---

## 4. 代码生成（dcg/codegen.py）

### 4.1 公开面与输出形状

| 符号 | 签名 | 说明 |
|---|---|---|
| `generate` | `generate(schema: dict, *, cython: bool = False, inline_optional: bool = True, registry=None, strict_qualname: bool = True) -> str` | schema → 完整模块源码；确定性；内部只调用一次 `compile()`；返回前验证可编译；`registry` 传 `Registry` 实例才把 `$myfn` 发成 `_x(...)`，`strict_qualname` 控制构建期 qualname 可导入性探测 |
| `build` | `build(schema, out_dir, *, name=None, overwrite=False, registry=None, strict_qualname=True) -> dict` | 写 `<out_dir>/gen/<name>.py` 与并排的 `<name>.manifest.json`；目标存在且内容不同 + `overwrite=False` → `SchemaError`；返回 manifest（见下表同名键） |

**manifest 键（三份文档共用同一张表；`build()` 返回值与并排 `.manifest.json` 逐字相同）**：

| 键 | 含义 |
|---|---|
| `name` | 产物模块名（= 规范化后的 `schema["name"]`） |
| `schema_sha1` | 规范化 schema 的 sha1（来源自证） |
| `artifact` | 产物路径 `<out>/gen/<name>.py` |
| `trace_artifact` | trace 产物路径；`output.trace=true` 时为 `<out>/gen/<name>.trace.py`，否则 `None`。**`generate()` 不产 trace**（它是纯函数，只返回源码字符串，不落盘）；只有走 `build()` 且 `output.trace=true` 才会额外生成这个文件 |
| `artifact_sha256` | 产物**字节**的 sha256（交付后校验落盘内容没被改过） |
| `generator_version` | 生成器版本 `<dcg.__version__>.g1`；版本变了意味着产物语义可能变，旧产物要重新生成 |
| `runtime_helper_hash` | `dcg.runtime` 的 helper 指纹（`SOURCE` 逐条 sha1 的前 16 位）：产物实际执行的是运行期 helper，这个值变了就说明语义来源变了 |
| `lines` / `chars` | 产物行数 / 字符数（**报告项，不是门禁**；字符预算已取消，历史决策见 git 记录） |
| `cython` | **恒为 `False`**：`build()` 没有 `cython` 参数（签名是 `build(schema, out_dir, *, name=None, overwrite=False, registry=None, strict_qualname=True)`），manifest 里这个键硬编码写死 `False`。Cython 产物只能走 `generate(schema, cython=True)` 直接拿源码，不经 `build()` 落盘 |

**生成器的次级公开面（模块级，非 manifest 键）**：

| 符号 | 签名 | 说明 |
|---|---|---|
| `gen_name` | `gen_name(schema) -> str` | 确定性模块名 = `schema["name"]` |
| `canonical_json` | `canonical_json(obj) -> str` | canonical 序列化（`schema_sha1` 的输入） |
| `schema_sha1` | `schema_sha1(schema) -> str` | 归一化 schema 的 sha1 |
| `load` | `load(path, mod_name=None)` | 载入已生成产物模块 |

生成器 CLI（`dcg/build.py`）：`<schema.json>` / `-o --out`（默认 `.`）/ `--overwrite` / `--stdout`；打印 `name` / `schema_sha1` / `artifact` / `lines` / `chars`。

**输出形状**（编译期常量）—— 四个候选值**只决定「同一个多行结果怎么装容器」**，不改行内字段：

| `shape` | `run(rows)` 返回 | 说明 |
|---|---|---|
| `list`（默认） | `[[行值...], ...]` | 每行是一个位置 list，行内不写字段名 |
| `rows` | `[{"字段": 值}, ...]` | 每行是一个具名 dict，键序 = 字段声明序 |
| `dict` | `{"字段": [每行值], ...}` | 列式：整个结果一个 dict，每个字段一列 |
| `index` | `{0: 行值, 1: 行值, ...}` | 行下标作键：整个结果一个 dict，值是每行 |

同样两行输入 `[[1,2],[3,4]]`、字段 `a`←`$.0`、`b`←`$.1`，四个形状分别产出：

```
list  -> [[1, 2], [3, 4]]
rows  -> [{'a': 1, 'b': 2}, {'a': 3, 'b': 4}]
dict  -> {'a': [1, 3], 'b': [2, 4]}
index -> {0: [1, 2], 1: [3, 4]}
```

**`list` 与 `rows` 的区别只在「行有没有字段名」**：`list` 是位置数组、`rows` 是具名 dict。
**`list` 与 `dict` 的区别是「批的方向」**：`list` 按行（一行的字段挤在一个 list 里），
`dict` 按列（一个字段的所有行的值挤在一个 list 里）。**`index` 是 `list` 的外面多包一层行号键**，
行值本身与 `list` 形状完全相同 —— 它用在输出要按序号寻址、且不想依赖列表下标的场合。

生成码可以直接看出来（都只差最后一行）：

```python
list  -> return _o
rows  -> return [{k:r[i]for i,k in enumerate(['a','b'])}for r in _o]
dict  -> return {'a':[r[0]for r in _o],'b':[r[1]for r in _o]}
index -> return {i:r for i,r in enumerate(_o)}
```

* `before` / `after`：**数据级钩子**，每个都**只求值一次**，不逐行跑。
  * `before`：在 `input.source` 定位之后、行循环之前求值，入参是**定位后的全量数据**，
    返回的数据替换输入数据（产物里 ` _d=<before>(_d)`，位置在 `_o=[]` 之前）。
  * `after`：在 `shape` 组装完成之后、`return` 处求值，入参是**全量处理结果**，
    返回的数据替换返回值（产物里 `return <after>(<finish>)`）。
  * 表达式里的**裸 `$` 就是那份整体数据本身**（不是某一行）；反引号段里的 `$` 同理。
    **`$` 的具体类型随 `output.shape` 变化**（`after` 尤其要注意，它拿到的是形状组装后的结果）：

    | `output.shape` | `before` 里 `$` | `after` 里 `$` | `after` 里 `$count($)` |
    | --- | --- | --- | --- |
    | `list` | 行数组 | 行数组 `[[..], ..]` | 行数 |
    | `index` | 行数组 | `{0: [..], 1: [..]}` | 恒 1（对象算一个值） |
    | `rows` | 行数组 | `[{..}, {..}]` | 行数 |
    | `dict` | 行数组 | 列字典 `{'v': [..]}` | **恒 1** |

    在 `dict` 下数行数取一列（`$count($.v)`）；`index` 取不到列名，无通用写法。
    `$merge` 只接受对象，
    `shape: "list"` 时 `$` 是数组 → `$merge` 返回 undefined → 被 `_ha` 拦成 `TypeError`。
  * 值的写法：`"= <JSONata 表达式>"`（`=` 前缀可省）、`"= py{ let …; return … }"`（多语句块）或 `"@pkg.mod.fn"`（按名 import 后整批调用一次）。**`before` 与 `after` 三种写法等价支持**。
    布尔值不接受 → `SchemaError: output.before: before 不接受布尔值：它是「整体数据 → 新数据」的转换钩子…`。
  * 表达式被包成 `(lambda D:<expr>)(...)`：形参名是 `D` 而不是 `R`，因为它拿到的不是行；
    钩子里的表达式分两种展开，不要混写：
    * **JSONata 表达式**里的路径发 `_ix_get(D, 'a')` 安全链（缺键得 undefined，再由 `_hb` / `_ha` 守卫抛指名 `TypeError`）；
    * **`py{}` 块**里的 `$.链` 作为 `_pyN` 形参传入，调用处写 `D['a']`（裸下标）。
    两者都不做行级前缀提升（数据级钩子没有行循环）。
    `after` 的 py{} 形态产物形如 `return _ha((lambda D:_py0(D))(<finish>))`。
  * **返回值守卫**（`_hb` / `_ha`，`dcg/runtime.py`）：钩子返回值会直接替换行集合 /
    最终结果，所以两条路上都挡 undefined，抛**指名钩子**的 `TypeError`，而不是让它
    静默变成脏结果：
    * `before` 返回 undefined → `TypeError: output.before: 返回了 undefined（没有值）—— 钩子必须返回数据本身（裸 `$` 就是它）；要取字段请写 $.路径`。
      （没有守卫时这里只会抛 `'_Undef' object is not iterable`，看不出源头。）
    * `before` 返回不可迭代标量 → `TypeError: output.before: 必须返回数组（行集合），得到 <类型> —— 钩子必须返回数据本身（裸 `$` 就是它）；要取字段请写 $.路径`。
    * `after` 返回 undefined → `TypeError: output.after: 返回了 undefined（没有值）—— 钩子必须返回数据本身（裸 `$` 就是它）；要取字段请写 $.路径`（文案由 `dcg/runtime.py` 的 `_HOOK_HINT` 拼接生成，逐字如此）。
      （没有守卫时 undefined 哨兵会原样泄进输出，那不是合法 JSON。）
    * `after` 返回**标量 / 对象 / 数组**一律放行 —— 「结果结构随你定」是 after 的正当用法
      （`"= 42"`、`` "= `{'n': len($)}`" ``、`"= $count($)"` 都合法）。
    * 没写钩子就**不生成**这两个调用（不为未声明的选项付费）。
  * 钩子里的 `py{}` 会登记成**模块级辅助函数**（定义在 `def run(` 之前），`$.链` 变成它的形参；
    与行内 `py{}` 不同，钩子里的 `$.链` 首段**不需要**是已声明字段——它只是数据里的键。
    块内裸 `$` 就是那份整体数据本身，走末尾一个额外形参（产物里 `_py0(D['rows'],D)`）；
    不需要时就只有 `$.链` 那几个形参（产物里 `_py0(D['rows'])`）。
* **钩子是数据级的**：`before` / `after` 各只求值一次、不逐行。需要按行筛选
  或改写时在 `before` 里对整份数据做变换。
* `trace`：调试开关；`true` 时额外生成独立调试产物，**正式产物与 `trace=false` 逐字节相同**，不污染热路径。
* 嵌套表示由 `type` 决定：`type="object"` + `fields` 发 `_ob([(字段名, 值), ...])`（具名对象）；`type="list"` + `fields` 发 `_rl([值, ...])`（按字段声明序的位置行，undefined 落 `None`）。只改该层嵌套的表示，不改 `shape` 的返回形态。
* `type="object"` + `fields` 的字段有两种用法，结构上无从分辨，由 `aggregate` 显式声明：
  * **不标**（默认）：该键下是**一列具名行** —— `{"docs": [{"day": 0, ...}, ...]}`。
  * **`aggregate: true`**：该字段是**列容器** —— 每个子键各聚合成一列，可逐层嵌套 —— `{"docsx": {"A": 100, "B": {"B2": [...]}}}`。

  只能用在带 `fields` 的 `object` 字段上，其它组合 → `SchemaError`。

  **生效条件与失败方式**：`aggregate: true` 只在 `output.shape == "dict"`（列式容器）下生效 —— 子键各成一列、可逐层嵌套。`list` / `index` / `rows` 是行式容器、没有「列」的位置，因此 `shape != "dict"` 又写了 `aggregate` 时 **build 期报 `SchemaError`**（`dcg/codegen.py` 的 `_check_aggregate_shape()`，对嵌套 `fields` 逐层递归检查），不会静默失效。迁移只有两条路：把 `output.shape` 改成 `"dict"`，或删掉 `aggregate`（行式容器下不标 `aggregate` 本来就是「每行一份具名对象」，与聚合结果逐值相等）。

  递归聚合时，一个被 ≥2 个子键复用的父列会被提到 `return` 之前预算一次、
  起一个 1–3 字符的短名（`_a`、`_a2`……），避免 `[r[6]for r in _o]` 这类表达式在
  返回表达式里原地展开 N 遍（N×M 层还会进一步放大）。只被一个子键用的列不提，
  原地写更短。短名由编译器生成，schema 侧无需声明；命名规则与普通局部变量一致
  确定性（同 schema、同 `generator_version` 下两次 `generate()` 字节相同）。
* 形状不可运行期切换：要两种输出就 build 两个产物。

### 4.2 生成码形态与九条规则

模块结构（自上而下）：

```
[from dcg.runtime import *]      # 仅在真的用到 helper 时出现（按需）
[def _pyN(...): <py{} 块体>]      # 每个 py{} 块一个，定义在 run() 之外
def run(_d):
 ...                             # 循环体：行首解包 + 逐字段计算 + 组装
 return <按 shape 的返回式>
```

产物*不写 docstring*：来源由 manifest 的 `schema_sha1` 记录；产物正文保持最小。

**九条形态规则**（形态判据，逐条可核对）：

1. **每个被引用的下标前缀在有行首语句的位置只取一次**：命名阶段记忆化（`$.5.a.1` → `_a` → `_a1` → `_a2`），任何字段复用同一前缀直接返回已有名字；按计算顺序（依赖拓扑序）解包，被引用字段先算并绑定；只被引用一次的可在使用处内联，未被引用的在组装输出时内联；**输出始终按声明序组装**。提升**只在有行首语句的位置生效**（`shape:"list"` 的行循环）；`shape:"rows"` 把整行压成一条推导式，同一字段的多次读取在表达式内内联、不落中转变量；**安全链（`optional` 路径）前缀同样计入有效提升**：安全链引用统一走 `safe_ref()` 令牌化，`plan()` 之后由 `text_of()` 从最近的前缀绑定派生逐段 `_ix_get`，绑定被消费端复用（数据级钩子 `_data_var=="D"` 没有行循环前缀，仍就地拼文本）。
2. **零恒等赋值、零「只为 return 而生的中转变量」**：不生成 `datas = datas`、`_buf = {'doc': []}; _doc = _buf['doc']`。
   **`append` 绑定是唯一豁免，且由生成器强制发出**：只要产物有行循环，就绑一次 —— `_c=_o.append`，行内改用 `_c([...])`（不写就静态门禁红）。这不是「中转变量」而是 **method hoisting**（消掉的是每行一次的属性查找，不是多存一份数据），且要求明确（「append = doc.append 需要绑定」；口径确认后，见「无条件」）。约束：(a) 只允许绑定 `_o.append`，名字固定为 `_c`；(b) 绑定后必须有调用，否则算纯中转；(c) 其他任何 `x = y.append` 形式仍然禁止。
3. **输出形状编译期固定**：`list` 直接构造列表；`dict` 生成 `{字段名: [每行值]}` 列式字面量（标了 `aggregate` 的 object 字段逐层展开成子键列；非 `dict` 形状写 `aggregate` → build 期 `SchemaError`，见 §4.1 的「生效条件与失败方式」）；`index` 生成 `{行下标: 行值}`；`rows` 在尾部包一层行 dict 推导式（行字面量仍是 list）。无运行期容器判断。嵌套表示按 `type` 发：`type="object"` + `fields` 发 `_ob(...)`，`type="list"` + `fields` 发 `_rl([...])`（位置行，undefined 落 `None`）。
4. **无冗余类型转换**：`type` 是声明不是转换；`type=int64` 不插入 `int(...)`，需要转换就在 `expr` 里显式写 `` `int($.x)` ``。快路径：`$string(a) & $string(b)` 直接生成 `_S(a) + _S(b)`，不再套 `_cc(...)`。
5. **无 `isinstance` 分派链；每个运算符一个专用 helper**：算术/比较/逻辑/拼接各走专用函数，数值守卫走 `_num(v)`、比较守卫走 `_cmp_ok(a,b)`；产物里不内联分派逻辑。
6. **每次 build 只 `compile` 一次，无空桩**：`generate()` 里 `compile()` 恰好一次。
7. **`optional: true` 才生成守卫，且恰好一次 undefined 判断**：内联形态 `X=None if _t is _U else _t`；设了 `default` 的字段改为编译期常量替换 `_df(<expr>, <default>)`；必然存在的字段零判断。
8. **变量名确定性、可读、1–3 字符**：`_` + 字段短名（链段命名 `_a` `_a1` `_a2`，冲突加数字后缀）；由字段声明顺序与链结构决定，禁止 random；命名只跟声明序与链有关、与计算顺序无关，保证同 schema 产物在**同 `generator_version` 下**逐字节可复现（跨版本可能变，需重新生成）。
9. **Cython 模式同源**：`cython=True` 的产物恒等于「纯 Python 产物 + 每局部一行 `cdef object`」，逐行一一对应，不改语义。

**build 期折叠与公共子表达式消除**（只改产物文本，不改 IR）：

* **常量折叠**：纯字面量的 `+ - *`、`&`、同类比较、`and`/`or` 在 `emit` 时直接折成字面量 —— `1 + 2 * 3` → `7`、`a * (2 * 3)` → `(a*6)`、`'a' & 'b'` → `'ab'`、`1 = 1.0` → `True`。**`/`、`%` 不折**（会把 `ZeroDivisionError` 提前到 build 期）；`true = 1`、`null & 'x'`、`true + 1` 也不折（1.8 语义与 Python 不同，交 helper）。
* **公共子表达式消除（CSE）**：同一字段的 `expr` 里出现 ≥2 次的**同构纯函数调用**只算一次，提到行级 defs：`$sum(xs) + $sum(xs)` → `_t1=_sm(xs)` + `_ad(_t1,_t1)`；嵌套同构子树整体提升（`$count($filter(xs,f)) + $sum($filter(xs,f))` 只算一次 `$filter`，再分别 `_ct`/`_sm`）。不纯函数（`$random`/`$shuffle`/`$now`/`$millis`/`$eval`/`$clone`）永不合并；字段路径、字面量、`py{}` 不参与。
* 两者都**不改语义**：折叠只发生在结果与 1.8 一致的组合上，CSE 只复用纯调用（守护：`tests/test_e2e_flight.py::TestOptPassFoldAndCSE`、`::TestOptPassElementType`）。

**helper 内联纪律**：helper 按需内联并只带传递依赖（按 helper 传递依赖拓扑排序）；产物只在真的用到 helper 时才发运行期导入行；CAP/M0 这类纯内联负载连一行 import 都不产生。

**helper 的局部绑定**：运行期导入行只把 helper 放进模块全局，函数体里每次调用都是 `LOAD_GLOBAL`。产物因此在 `run()` 之前发一行 `_g=globals()`，并在函数开头把**实际用到**的 helper 各绑一次（`_gt=_g['_gt']`），此后同名在函数内是局部变量（`LOAD_FAST`）。名字按字典序发出，同一 schema 产物逐字节稳定；一个 helper 都不用的裸运算产物不发 `_g` 行。

* 收益与 Python 版本强相关：CPython 3.13 的 `LOAD_GLOBAL` 已有内联缓存，实测每次调用只差 **8.2 ns**（74.0 → 65.8 ns），故每行 1–3 次 helper 调用时端到端约 **1%**（落在噪声内），helper 密集的表达式收益更大。
* 只换查找路径，**不改任何语义**；守护：`tests/test_e2e_flight.py::TestOptPassLocalBinds`。

### 4.3 与表达式层衔接

`schema.out.fields[f].expr` → `compile_expr(src)` → IR → `emit(node) -> str`（Python 表达式源码，内联形式）。

* **裸名校验**：每个 `("field", name)` 与反引号/`py{}` 的裸名都要查 schema 已声明字段集；未声明 → **build 期** `SchemaError` 并列出可用字段名（绝不拖到运行期 `NameError`）。
* **`py` 节点**：`ast.parse(source, mode="eval")` 校验（build 期）；`free_field_names` 逐条替换为生成局部名，链解析与 JSONata 路径共用同一实现。
* **`pyblock` 节点**：`ast.parse(stmts_source, mode="exec")` 且恰好一个 `return`；发成 `def _pyN(f1, f2, ...)`，调用处传已解包字段。
* **`rfunc` 节点**：产物在模块级发一次 `_xr={}` 与惰性分派器 `def _x(q,*a):`——先查 `_xr` 缓存；未命中则**从长到短试**导入（`s=q.rfind('.')` 起，`importlib.import_module(q[:s])`，成功后再把 `q[s+1:]` 按 `.` 切开逐段 `getattr`；`ImportError` 就把 `s` 退到上一个点继续），成功后写入缓存；全部分法失败才 `raise ImportError('$ 函数不可导入: '+q)`。每个 `rfunc` 引用点发 `_x('<qualname>',[args...])`。钩子 `@pkg.mod.Class.method` 用的是同一套算法。
* **`inline_optional`**（默认 `True`）：`True` 时守卫内联为 `X=None if _t is _U else _t`；`False` 时改走运行期 `is_u()`（`X=None if is_u(_t) else _t`），产物多一次调用。

**内联判据**（`type` 是声明 ⇒ 值的可能性可静态判定）：

| 情形 | 产物形态 |
|---|---|
| `optional` 非真 且 `type` 为 `int64`/`int32`/`float64`/`str`/`bool` 之一 | 裸运算 / 内建：`a+b`、`str(x)`；字符串函数实参类型可证时发内建方法：`s.upper()`、`s.lower()`、`s.strip()` |
| `optional: true` | 内联守卫：`X=None if _t is _U else _t` |
| 字段设了 `default` | 编译期常量替换：`_df(<expr>, <default>)`；值存在时原样返回 |
| 操作数类型可证（与目标字段类型无关） | 裸运算 / 内建方法 / 裸字面量 / 裸比较逻辑：`a+b`、`s.upper()`、`[1,2,3]`、`{'k':a}`、`(a<b)`、`(a and b)`、`(x if c else y)` |
| 比较/逻辑/条件的 JSONata 特例 | 保留 helper：混合类型（`1 = true` 为 `false`）、可能 `undefined` 的操作数（`_eq`/`_lt`/`_and`/`_tru`/`_co` 负责传播） |
| 有语义一致的 Python 内建等价的列函数 | 直接发内建表达式：`$length`→`len()`、`$count`→`len()`、`$abs`→`abs()`、`$contains`→`in`、`$split`（无 limit）→`str.split()`、`$substring`（下标字面量非负）→切片、`$substringBefore`→`partition()[0]`、`$replace`→`str.replace()`、`$pad`→`str.ljust()`、`$sum/$max/$min/$average`→内建聚合（可证数值元素；`$sum([])`→`sum([])`）、`$reverse`（字面量数组）→`([...])[::-1]`、`$sort`（同类型字面量数组）→`sorted(...)`、`$boolean`→`bool()`（真值域可证时；否则走 `_B`，见 §4.4）、`$not`→`not`、`$floor/$ceil`→`math.floor/ceil`、`$power`→`a**b`、`$sqrt`（可证非负）→`math.sqrt`、`and`/`or`（**两侧都可证为布尔**时）。**`$substringAfter` 不在内联表内**——`partition()[2]` 未命中时给 `''`，而 1.8 给整串。**`$round` 也不在内联表内**：带 precision 时 1.8 走 `toString()` 十进制移位（`$round(2.675,2)` = 2.68，`round(2.675,2)` = 2.67） |
| 无内建等价或语义有差异 | 保留 helper：`$number`、`$sqrt`（不可证非负）、`$round`（全部，含 1 参与 2 参精度；**银行家舍入**，实现为 `runtime._ro` 的 `ROUND_HALF_EVEN`，`$round(2.5)` = 2）、`$substringAfter`、`$split`（带 limit）、`$count`（非数组实参，标量按 1 计）、`$join`、`$type`、`$exists`、`$keys`/`$values`/`$lookup`/`$merge`/`$spread`、`$reverse`/`$sort`（实参不可证为数组、或 `$sort` 带比较器）、`and`/`or`（任一侧不可证为布尔）、`in`、字符串编码类、高阶与非确定函数 |
| 列表元素类型由 `itype` 声明，或由使用方式推断（`$map`/`$filter` 回调形参） | 形参类型可证 → 裸运算：`[v for v in xs if (v>1)]`、`[(v*2) for v in xs]`；未声明且推不出则回退 `_gt` / `_ml` |
| `type == "any"` 或类型无法静态判定 | helper 调用（`_ad` / `_S` / …，语义安全网） |

**`type` 为 `list` / `object` 的字段不参与本表判据**：它们不是「裸运算还是走 helper」的问题，而是按 §4.1 的嵌套规则**组装**——字段值是裸读（或安全链），外面套 `_ob([(名, 值), ...])`（`type:"object"` + `fields`）或 `_rl([...])`（`type:"list"` + `fields`，位置行）。参与本表判定的是这两个容器内部**标量字段**的读写。

**`$boolean` 的内联边界**：`_truthy_provably` 认三类可证节点——① 布尔 / 数值 / 字符串**字面量**；② schema 里声明了具体 `type`（`bool` / 数值 / `str`）且**不是 `optional`** 的字段，并在表达式里以**字段名**引用（`_kind()` 对字段节点查 `_decl_type`，也包含从运算上下文推导出的类型）；③ 由上述可证节点参与的比较 / 逻辑 / 条件结果（`= != < <= > >=`、`and`/`or`/`not`、两布尔支的三元）。

**裸路径读值不是可证节点**：直接写在表达式里的路径（`$.a`、`$.0`、`$.7.lv`）是「根 / 下标」节点，不参与 `_kind()` 的类型证明，因此**一律走 helper `_B`**——即使 schema 里另有一个字段声明了与它相同的读值路径也一样。要让 `$boolean` 内联，请在 schema 里给该读值声明一个字段（`value`/`expr` + 具体 `type`），再在表达式里用**字段名**引用：`lv: {"type":"int64","value":"$.0"}` 时 `$boolean(lv)` 发 `bool(lv)`，而 `$boolean($.0)` 发 `_B(_R0)`。

**`optional` 字段一律不可证**：无论字段的读值路径是什么形态（纯名字段还是含整数下标），只要声明了 `optional`，`$boolean` 都走 `_B`；这与 §4.4 的读值分档无关——那边决定「怎么读」，这里决定「读到的结果能否静态证明真值域」。`_B` 拿到的就是读值结果：可能 undefined（来自其它 helper）、`None`（optional 缺键）、数值或字符串；undefined 原样传播，`None` / `0` / 空串 / 空容器为假，其余按 JSONata 真值。修复 D-BOOL-01 后内联 `bool()` 与 `_B` 对可证三类的结果一致，所以「可证/不可证」只影响产物形态、不影响结果。

**裸代码 vs helper 的判定顺序**（自上而下，命中即定）：

| # | 情形 | 产物 |
|---|---|---|
| 1 | 字面量、常量链、常量运算 | 裸代码 |
| 2 | **路径读值**（`value` 与 `expr` 内联路径同规则）：**未声明 `optional`**（`type` 是 `any` 还是具体类型**不影响**） | 裸链 `R['a']`、`R['a']['b']`、`R[5]['a'][1]`（缺键 = `KeyError`）；同一路径被多处引用或作为前缀时行首绑定一次；见下方「`type` 不参与 optional 判定」 |
| 3 | **路径读值**：**声明了 `optional`** | `_ix_get` 安全链（缺键 = undefined），再按 `default` 落 `None` 或默认值 |
| 4 | 运算 / 函数 / 字面量容器，操作数类型可证 | 裸运算符 / 内建 / 裸字面量 |
| 5 | 操作数类型不可证（`any`、路径值、函数结果、lambda 参数） | helper |
| 6 | 动态下标 | 两层判定：①字段级 required → 裸下标 `base[idx]`，optional/default → `_ix_get`；②基座自身 optional/type 决定基座读法（裸名 required 裸链 / optional+type `.get` 快链 / optional+any `_ix_get`；`$.` 路径一律 `_ix_get`）。详见 §3.4 |
| 7 | 语义特例（无内建等价 / 比较特例 / 高阶 / 非确定） | helper |

**缺键行为只看 `optional` / `default`；`type` 不参与「裸链还是安全链」的判定（它决定的是「类型不符」时的行为）**（与 §4.4 一致；`type` 只在声明了 `optional` 之后决定走 `.get` 快链还是 `_ix_get` 全安全链，见 §4.4 的读法分档）：`{"type":"any","value":"$.zz"}` 与 `{"type":"int64","value":"$.zz"}` 在未声明 `optional` 时产物**完全同形**——都是裸链 `r['zz']`，缺键都抛 `KeyError`；`any` 只影响**后续运算**走 helper 还是裸运算符（第 5 条）。要缺键得 `undefined` 而不是 `KeyError`，必须显式写 `optional: true`。（历史：早期曾按 `type` 分叉、让 `any` 发 `_ix_get`，该实现已回滚；回归由 `tests/test_value_expr_parity.py` 锁定。）

**「`value` 与 `expr` 同规则」的精确含义**：判定「裸链还是 `_ix_get`」只看**字段是否声明 `optional`**，与路径写成 `value` 还是出现在 `expr` 里**无关**。实测：

| schema 写法 | 产物 | 缺失时 |
|---|---|---|
| `{"value": "$.zz"}`（`type: any`，无 `optional`） | `r['zz']` | `KeyError` |
| `{"expr": "$.zz"}`（无 `optional`） | `r['zz']` | `KeyError` |
| `{"expr": "$.zz + 1"}`（无 `optional`） | `_ad(r['zz'],1)` | `KeyError`（路径本身裸取，helper 只兜类型不符） |
| `{"expr": "$.p.q"}`（无 `optional`） | `r['p']['q']` | `KeyError` |
| `{"value": "$.p.q", "optional": true}` | `None if (_v:=_ix_get(_ix_get(r,'p'),'q')) is _U else _v` | `None` |
| `{"expr": "$.p.q", "optional": true}` | `None if (_v:=_ix_get(_ix_get(r,'p'),'q')) is _U else _v` | `None` |

**`value` 与 `expr` 的产物逐字符相同，无例外**：带 `default` 的字段，`value` 与 `expr` 写法也**完全同形**——`{"value": "$.zz", "default": 0}` 与 `{"expr": "$.zz", "default": 0}` 都发 `_df(_ix_get(r,'zz'),0)`（带不带 `optional` 都一样：判据是「有无 `default`」，有 `default` 即走守卫 + 落默认值）。早期文档曾另行列过「表达式里的单段路径走 `_ix_get`」，与代码不符（表达式里的路径同样按 `optional`/`default` 决定读法），已删除。

表按「行内位置」给出落值：`shape:"list"` 的行、`shape:"dict"`/`"rows"` 的字段、`type:"list"` 的嵌套位置行都落 `None`（`None if (_v:=...) is _U else _v` / `_rl` 保位）；`type:"object"` 的嵌套字段由 `_ob` 组装，undefined 的键被**丢弃**（既不落 `None` 也不报错）。`value` 与 `expr` 在以上每种形态里逐字符同形。

`shape:"list"` 的行用行首绑定临时变量（`_t1=_ix_get(...)`；`x=None if _t1 is _U else _t1`）；`shape:"dict"`/`"rows"` 的行内表达式无法提前绑定，改用海象运算符就地赋值（`None if (_v:=...) is _U else _v`）。两者语义相同，形态差异只为避免行内重复求值。

「操作数类型可证」的来源：字面量；`optional` 非真且声明了具体 `type` 的字段；上述值的算术 / 比较 / 逻辑 / 条件 / 内建函数结果；字面量容器。

内联只改产物形态、不改 undefined 语义；语义只在「值真可能 undefined」的地方花钱。

代价是**内联不做类型校验**：一旦运行期数据的实际类型违反了 build 期声明（`type: "int64"` 的列里出现 `"abc"`），裸内建会抛 Python 原生异常（`math.floor("abc")` → `TypeError`），而 helper 版本会返回 undefined。也就是说在「数据违反声明」这条路上，内联与 helper 表现不同——这不属于 undefined 语义（那描述的是**合法数据**的取值规则），而是「数据本身不符合声明」的后果。要严格守住 undefined 语义，就别让数据违反声明，或把该字段声明为 `any`（此时走 helper）。

字符串函数 `$uppercase` / `$lowercase` / `$trim` 在实参类型可证为 str 时同样走内建方法调用（`s.upper()` 等），不再套一层 helper。

`$floor` / `$ceil` / `$sqrt`（可证非负）走内建表达式时（`math.*` 形态）产物头部额外补一行 `import math`（**仅在用到时**：产物正文生成后现扫文本是否出现 `math.` 字面量，出现才补，不查任何常量集合）；`$power` 不在其列（发 `a**b`）。`$sqrt` 的负数是 helper 的 `undefined` 语义，`math.sqrt` 会抛 `ValueError`，因此只有字面量 `>= 0` 或 `$abs(...)` 才内联。

### 4.4 运行期 helper 与 undefined 语义

`dcg/runtime.py` 是 undefined 语义的**唯一实现处**，同时是生成码内联文本的来源。

* **undefined 用单例表示**：一个空 `__slots__` 的哨兵类、一个模块级单例 `UNDEF`，判定用身份比较（`is`）；产物里短名 `_U`。产物不自建哨兵。
* **87 个 helper**，按类别：算术/比较/逻辑/字符串/数值/数组/类型/下标/守卫/钩子守卫；生成码里的内联文本由 `SOURCE` / `HELPERS` 从源码派生（与实现同源，不手抄）。

  **完整名单（按 `dcg/runtime.py` 的 `HELPERS` 键序，逐项与本表一致性由验收门 `docs` 守护）**：

  ```text
  谓词（哨兵不计入）  _num、_tru、is_u
  算术        _ad _sb _ml _dv _md _pg _nt _neg _pos _cc _and _or _co
  比较        _eq _ne _lt _le _gt _ge _in
  字符串      _S _up _lo _tm _ln _ct _sp _jn _ss _ix _cn _pd _sbb _sba _rp _b64e _b64d _ue _ud _fb _fnm
  数值        _N _B _ab _fl _ce _ro _sq _pw _sm _mn _mx _av
  数组/对象   _ar _ob _rl _ix_get _ap _as_list _rv _so _ds _mg _ky _vl _lk _sr _sg _RW
  高阶        _mp _fi _rd _ea _sf
  非确定      _nw _ms _rn _sh（+ 可注入单例 CLOCK/RANDOM）
  类型/守卫   _ty _ex _b _df
  钩子守卫    _hb _ha
  ```

  说明：`len(HELPERS)` 是 **87**，与上表逐项一致（`_num`/`_tru`/`is_u` 是谓词，也计在内）；其中 **`_pg`、`_b` 两个不产出**（见下），所以**实际会被生成器引用的是 85 项**。
  `UNDEF`/`_U`/`CLOCK`/`RANDOM` 是常量或可注入实例，不是 helper，故**不**计入 `len(HELPERS)`。
  `_hb`/`_ha` 是 `before`/`after` 数据级钩子的返回值守卫，只有写了钩子才会出现在产物里。
  `_pg` 是 `$power` 的旧/备用实现（`a ** b`），当前生成器**不产出**任何 `_pg` 调用；`$power` 的 helper 兜底是 `_pw`（`math.pow`，float）。
  `$boolean(x)` 在实参**真值域可证**时内联 `bool(x)`（判据 `_truthy_provably`，只认布尔/数值/字符串三类可证节点；这三类在 `bool()` 与 `_B` 下结果一致，所以两条路径不会分叉）；不可证（`any`/`optional`/路径值/未声明类型）时走 helper `_B`。`_B` 是 `$boolean` 的 JSONata 1.8 语义实现：`undefined` 原样传播；`null`、空串、`0`、空容器 → `False`；非空串、非零数值、非空容器 → `True`；布尔原样。**字符串不解析** `'true'`/`'false'`（`$boolean('false')` 为 `True`，因为它是非空字符串）。`_b` 是更早的恒等实现（`_b(v)` 返回 `v`），当前生成器不产出，保留在 `HELPERS`/`__all__` 仅为兼容历史产物。`_B` 与 `$not` 的 `_nt` **共用 `_tru` 真值口径**（`_nt(a)` 先判 `undefined`、再 `not _tru(a)`）：`$boolean`/`$not` 对 `undefined` 都原样传播，`$filter` 等布尔域判断同用 `_tru`，三者不会分裂。
* `__all__` 导出 **97 个名字**（含全部下划线 helper 与哨兵）；产物通过一行 `from dcg.runtime import *` 取得。
* **`import math` 只在用到时才发**：产物正文里出现 `math.` 时，生成器在产物头部补一行 `import math`（用到 runtime helper 时它在 `from dcg.runtime import *` 之后）。**判据是现扫产物文本里的 `math.` 字面量，不读任何清单**；`math.` 只可能由内联表（`$floor`/`$ceil`/`$sqrt`）或反引号段里的用户代码写出。
* **`SOURCE_NEEDS_MATH` 与生成器无关**：`dcg/runtime.py` 的 `SOURCE_NEEDS_MATH` 常量（= `{"_fl","_ce","_sq"}`，由 `runtime.__all__` 导出）仍定义着，但生成器**不使用**。它的身份是「源码里真的引用了 `math` 的 helper 全集」这条自检不变量（显式排除从不被内联的 `_pw`），其内容由测试作为自检不变量钉住；历史失配原因（helper 改用 `_M.floor` 后源码不再含 `"math"` 字面量）见验收报告 D-MATH-01。`_M` 只是 `dcg/runtime.py` 内部的模块级别名，**产物里不出现 `_M`**。
* `$power` 可证时发 `(a**b)`（不用 `math.pow`）；不可证时走 `_pw` helper，`_pw` 内部用 `math.pow`，但 `math` 已在 `dcg.runtime` 里 `import`，所以**产物侧永远不会因为 `$power` 触发 `import math`**。
* **自包含性的口径**：产物不依赖第三方包，但可以依赖 `dcg.runtime`（哨兵与 helper 只有一份实现，不复制进产物）。

**undefined 六条语义**（必须逐条保持）：

1. `&` 的 undefined **不传播**，当空串：`'x' & miss == 'x'`
2. `or` 的 undefined **参与运算**：`miss or true == True`
3. `=`/`!=` 遇 undefined 返回 `False`：`miss = miss == False`
4. 大小比较遇 undefined 返回 **undefined**：`miss < 5 == undefined`
5. 数组字面量**跳过** undefined：`[miss, a] == [1]`
6. 对象字面量**丢弃** undefined 键：`{'k': miss} == {}`

附加三条：`null` 参与算术是**错误**（不是 undefined）；`$not(miss) == undefined`；`a[0]` 对标量也成立（`a[0] == a`）。

**`_Undef.__bool__` 返回 `False`**：`if x` 对 undefined 取假。`_tru` 白名单的等价性依赖这一点（比较/`in` 等只可能产出 `True`/`False`/`undefined` 时，`if _tru(x)` 与 `if x` 布尔结果相同）；`tests/test_sentinel_integrity.py` 钉住该行为。

**行内 undefined 出口**：`expr` 字段可能返回 undefined 时（如 `expr: "$lookup({},'k')"` 且无 `optional`/`default`），产物形如 `_c([_lk({},'k')])` —— UNDEF 哨兵会**原样进入输出**（不是 JSON 合法值）。因此这类字段必须声明 `optional`（落 `None`）或 `default`（落默认值）；引擎**不自动守卫**：自动守卫会改变所有产物形态与性能，属未来可选项；当前要求用户对可能返回 undefined 的 `expr` 字段显式声明 `optional`/`default`，哨兵由用户承担。`_hb`/`_ha` 只守 `before`/`after` 数据级钩子的返回值，不守行内字段（`_hb`/`_ha` 见 §4.1「返回值守卫」）。

路径读值只看 `optional`：未声明发裸链；声明了发安全链。`value` 与 `expr` 同规则。`type` 不改变「裸链/安全链」判定，只在 `optional` 内部选择 `.get` 快链或 `_ix_get` 全安全链；带 `default` 时优先走 `_df(_ix_get(...), default)`。

> **四种读法一张表**（自上而下命中即定：`default` 最优先，然后看 `optional`，最后看 `type`）：
>
> | # | 声明 | 产物形态 | 缺键 | 中间层存在但类型不符 |
> |---|---|---|---|---|
> | 1 | 带 `default`（无论有无 `optional` / 具体 `type`） | `_df(_ix_get(r,'a'), <default>)` | undefined → **默认值** | undefined（全安全） |
> | 2 | 无 `default`、无 `optional`（required，**含 `type: "any"`**） | 裸链 `R['a']['b']` | `KeyError`（真实异常，用户承担） | `TypeError`（对 `null`/标量取下标） |
> | 3 | 无 `default`、`optional` + 具体 `type` | `r.get('a', _U).get('b', _U)` | undefined → `None` | **`AttributeError`**，不兜底 |
> | 4 | 无 `default`、`optional` + `type: "any"`（或未声明 `type`） | `_ix_get(_ix_get(r,'a'),'b')` | undefined → `None` | undefined（全安全） |
>
> 第 1 行**优先于**第 3/4 行：`default` 决定产物形态，`type` 只在这个字段**没有** `default` 时才参与分档。第 3 行是**选择性的边界**：声明具体类型就等于声明「这一层是对象」，类型不符属于数据违反声明（与 README §2.3 同口径）。要完全 undefined 安全请声明 `type: "any"`。

**第 3 行的 `.get` 快链只对纯名字段路径成立**；含整数下标的路径（`$.0`、`$.7.lv`）整条走 `_ix_get`。判据：`key_path` 的段元组里没有 `int`。因此「optional + 具体 type ⇒ 一律 `.get`」是不准确的；这类位置的 optional 字段实际是 `_ix_get` 链（验收报告 §5.3 的 `optwide_hit` 就是这种情形）。第 3 行的 `.get` 快链依赖哨兵实现 `.get(k, default)` **恒返回自身**，因此纯名字段链 `r.get('a',_U).get('b',_U)` 任一段缺键都得 undefined、不会中途抛 `AttributeError`；`$exists($.missing)` 对 required 字段探不到缺失（取键本身已抛 `KeyError`，`$exists` 没有机会执行）。

**实现方针：所有算子必须是 EAFP 的**——假定绝大多数参数合法，正常路径先算、异常再兜底、成功路径末尾才判结果域。**完整骨架、为什么不能只写 `try/except`、算子内联实测收益、精确类型判断的理由，全部见架构文档 §7.5 策略二，本节不复述。**

这里只记接口层必须知道的三条约束：

* **`_ad` 等六个算术算子同构，但各自内联同一骨架**，不许互相调用（`op(a,b)` 间接调用慢 20%）。所以接口面上它们是 6 个独立 helper，不是 1 个通用 `_arith`。
* **语义不降级是接口承诺**：`1 + undefined` 传播 undefined、缺键/越界/除零/负数 `$sqrt` 全部保持原语义；算术算子对 Python 自己能算的组合（`True + 2 == 3`、`"a" + "b" == "ab"`、`[1] + [2] == [1, 2]`）一律放行，只有 Python 抛 `TypeError` 才报 `$add: 操作数必须是数值`。写新算子时必须逐条保证。
* **精确类型比较**（`type(v) is int`），不用 `isinstance` 链 —— `bool` 由精确类型比较自然排除。

**产物里不保留不改变语义的调用层**（架构文档 §7.5 策略三）：

| 形态 | 产物 |
|---|---|
| `$map(arr, function($v){$v * 2})` | `[_ml(v,2) for v in _as_list(R[6])]`（不是 `_mp(R[6], lambda v,_i1:_ml(v,2))`） |
| `$filter(arr, function($v){$v > 1})` | `[v for v in _as_list(R[6]) if _gt(v,1)]`（不是 `_fi(...)`，也不套 `_tru`） |
| `$filter` 的函数体值域不限布尔 | `[v for v in _as_list(R[6]) if _tru(<体>)]`（**必须**保留 `_tru`） |
| 全字段可从原始行直接求值的 `shape=dict`/`rows` | `{'a':[r[0] for r in _d], ...}` / `[{'a':r[0], ...} for r in _d]`（不建 `_o` 中间容器） |
| `$reduce` / `$sort` 带比较器 | 累加**形态可展**时展成行级循环（见下一条）；比较器语义写不成推导式，保持 helper 调用 |
| `$reduce(arr, function($a,$b){$a + $b}, 0)`（单一 `$a OP $b`，`OP` 是 `+ - *`，init 是字面量，第一实参可证为数组） | 展成循环，字段表达式只返回临时量：`_t1=0` / `for _t2 in <arr>:` / `_t1=_ad(_t1,_t2)`。省掉 `_rd` 的循环与每元素一次 lambda 调用，实测 20 万行 × 2 元素 0.778 → 0.508 µs/行（**1.53×**）。`OP` 仍发 helper（`_ad`/`_sb`/`_ml`）而不是裸运算符：helper 保有 JSONata 的 `'a'+'b'`/`[1]+[2]`/`True+1` 语义与 undefined 传播，裸运算只差 0.05 µs/行 却换掉报错文案，不值。其余形态（无 init、`/`、`%`、函数体嵌套运算、`$b+$a`、init 非字面量、第一实参不可证为数组）**一律退回** `_rd` + lambda |
| 第一实参不保证是数组（`$map($.6, ...)` 这类根元组下标、路径链） | 推导式源头套一层 `_as_list(...)`：运行期做 1.8 的单值提升（标量包成单元素、`undefined` 变空序列）；**不再**退回 `_fi`/`_mp` + lambda（那是每元素一次 Python 调用）。`$reduce` 不同：`_as_list` 的单值提升与 `_rd` 的「非容器得 undefined」不等价，所以只在**可证为数组**时展开 |

`_tru` 可以省的判据是**白名单**：函数体只可能产出 `True`/`False`/`undefined` 时（比较、`in`、`and`/`or`/`not`、`$exists`/`$boolean`/`$contains`，或两支都是布尔的三元），`if _tru(x)` 与 `if x` 的**布尔结果完全相同**；其余一律保留 `_tru`。该等价性依赖 `_Undef.__bool__` 返回 `False`（见上文「undefined 六条语义」后的加粗注）；可省判据仍按 `_bool_valued()` 白名单。

**函数语义补充（不是「特例」）**

**算子级 helper 的默认规则**：算术/比较/逻辑/拼接/取负等算子走同一条 EAFP 默认规则（见本节「实现方针」）：`try` 里只做最可能的运算，**Python 算得出来就算成功**（`true + 1 == 2`、`'a' + 'b' == 'ab'`、`[1] + [2] == [1,2]`）；Python 抛 `TypeError` 时，含 undefined 的操作数传播 undefined，否则抛带算子名的 `TypeError`（`'a' + 1` → `TypeError: $add: 操作数必须是数值`）。**「类型不符 → 返回 undefined」的旧默认规则已不成立**：算子级 helper 里类型不符只有两种结局——含 undefined 传播 undefined，否则抛错。

**函数级 helper 按各自的函数语义处理非预期类型**：A 类 46 个行内函数对应的运行期实现不受上一条约束——多数对非预期类型返回 `undefined`（实测 `_ln(5)`、`_fl('a')`、`_ab('a')`、`_ix(5,'x')`、`_sq(-1)` 都是 undefined），少数有专属错误语义（如 `$sum` 的元素不可累加时抛 `TypeError: $sum: 元素必须是数值`）。以 `JSONata 语法.md` 各函数表与 §12.1 的逐函数说明为准；不要把算子级的「两种结局」套到函数级 helper 上。

**内联与 helper 在「数据违反声明」时可能不同**：内联不做类型校验（见上文「内联判据」），裸内建会抛 Python 原生异常（`math.floor("abc")` → `TypeError`），helper 版本按函数语义返回 undefined。这不属于 undefined 语义分歧，是「声明被数据违反」时两条路径的既定差异。

唯一补充要求在 `$sum`：为保留一次 C 级 `sum()` 成功快路径，它额外要求实参能被 `sum()` 接受——`$sum([1,'a'])` 报 `$sum: 元素必须是数值`，`$sum([True,1]) == 2`（bool 按 Python 语义参与，不是特例），`$sum([]) == 0`；实参整份是对象时给专属文案（点明「实参是整个对象，不是数组」，并指引写 `$sum($.列名)`）。

因此 `_ad` 对 bool 不是「放行特例」而是 EAFP 自然结果；`$sum` 对 bool 也不是特例。早期「算术算子走定点类型判断、严格路径」的说法作废：EAFP 成功路径只有一次 `a op b`，比定点判断更便宜。

> **仍然容易猜错的返回值**：`$boolean(x)` 在真值域可证时返回**真正的 `bool`**（内联 `bool()`；不可证时走 `_B`：`_B(1)` → `True`、`_B('abc')` → `True`（非空串）、`_B('false')` → `True`（字符串不解析）、`_B('')` → `False`、`_B(UNDEF)` → `undefined`，见上文「完整名单」的名单注）。注意小写 `_b` 是另一个 helper（`_b(1)` → `1` 是 int，同样见名单注）、`$length`/`$floor`/`$abs` 对标量返回 `undefined` 而不是抛错——它们**不偏离**任何规则，只是读者容易猜错，所以不列进上文的补充。
>
> **另一个返回值陷阱**：`$power` 整数底整数幂可证时内联 `(a**b)` 得 `int`（`(2**10)` → `1024`）；走 `_pw`（`math.pow`）得 `float`；`$sqrt` 恒返回 `float`。见 `JSONata 语法.md` §7（数值函数）；类型/存在性语义见 §8。

### 4.5 产物形态样例

**CAP 负载**（4 字段；`hourA`/`hourB` 是声明了 `int64` 的位置式
`value: "$.5.a.1"` / `"$.5.a.2"`，`hourD`/`hourE` 是 `expr`。四条链共用同一个前缀
`R[5]['a']`，§4.2 规则① 要求它**只取一次**——所以提升成 `_a`，并由 `_a` 逐段派生
`_a1`/`_a2`。`hourD`/`hourE` 的 `expr` 引用的是同一条**位置式**链（`$.5.a.1` /
`$.5.a.2`），命中已提升的 `_a1`/`_a2` 直接复用；**若 `expr` 换写成 `$.hourB` 之类
的裸名引用，则取的是已算好的字段局部名**，同样不重复求值。只有带 optional/default
的字段才退回 `_ix_get` 链（那是安全取值口径，不是冗余读）：

> 块首 `# dcg: <schema 名>` 是文档守护标记（`tests/test_doc_products.py` 用它把该块钉到 `tests/workload.py` 同名 schema 的 `generate()` 输出逐字符比对），**不是产物内容**。

```python
# dcg: V2_CAP
def run(_d):
 _o=[]
 _c=_o.append
 for R in _d:
  _a=R[5]['a']
  _a1=_a[1]
  _a2=_a[2]
  _c([_a1,_a2,(str(_a1)+str(_a2)),(_a1+_a2)])
 return _o
```

**M0 负载**（8 字段，追加了 `input.source="$.index"` 的适配行与 `_c=_o.append` 的 method hoisting）：

```python
# dcg: V2_M0
def run(_d):
 _d=_d['index']
 _o=[]
 _c=_o.append
 for R in _d:
  _a=R[5]['a']
  _a1=_a[1]
  _a2=_a[2]
  _c([_a,_a1,_a2,R[2],str(_a1) + str(_a2),(_a1+_a2),(_a2+_a1),(_a[2]+123)])
 return _o
```

**字段互相引用时**：被引用字段的求值提前到计算顺序前段，输出仍按声明序。例如 `b` 声明在 `a` 前、`b = $a * 2`，产物先算 `_a` 再算 `_b`，append 里仍是 `[..._b,..._a...]`。

**其它形状的尾部**（同一行字面量，只换结尾）：

| shape | `return` 形态 |
|---|---|
| `list` | `return _o` |
| `dict` | `return {k: [r[i] for r in _o] for i, k in enumerate([...字段名...])}` |
| `index` | `return {i: r for i, r in enumerate(_o)}` |
| `rows` | `return [{k: r[i] for i, k in enumerate([...字段名...])} for r in _o]` |

嵌套表示按 `type` 发：`type="object"` + `fields` 发 `_ob([(字段名, 值), ...])`（具名对象），`type="list"` + `fields` 发 `_rl([...])`（位置行，undefined 落 `None`）。

---

## 5. 公开 API

### 5.1 顶层公开面 —— 8 个符号

```python
from .schema import validate, SchemaError
from .codegen import generate, build, gen_name
from .jx import compile_expr, Unsupported, Registry

__all__ = ["validate", "SchemaError", "generate", "build", "gen_name",
           "compile_expr", "Unsupported", "Registry"]
```

| 符号 | 签名 | 说明 |
|---|---|---|
| `validate` | `validate(schema)` | 校验 + 归一化深拷贝（§2.4） |
| `SchemaError` | `SchemaError(path, msg)` | `ValueError` 子类；`.path` / `.msg` |
| `generate` | `generate(schema, *, cython=False, inline_optional=True, registry=None, strict_qualname=True) -> str` | schema → 模块源码；`registry` 传 `Registry` 实例才启用 `_x(...)` 分派；`strict_qualname` 在构建期验证 qualname 可导入 |
| `build` | `build(schema, out_dir, *, name=None, overwrite=False, registry=None, strict_qualname=True) -> dict` | 写产物 + manifest |
| `gen_name` | `gen_name(schema) -> str` | 确定性模块名 |
| `compile_expr` | `compile_expr(src, *, loc_base=(1,1), registry=None) -> tuple` | JSONata → IR |
| `Unsupported` | `Unsupported(feature, loc=None, msg=None)` | 不支持项异常（§7.2） |
| `Registry` | `Registry().fn(name, func)` / `.get(name)` / `.qualname(name)` / `.names()` / `.unregister(name)`（另有 `in` / `[]` / `len()` 容器协议） | 用户函数注册表；**注册对象必须模块级可导入**（lambda/闭包/局部函数在 compile 期报 `Unsupported`，因为产物按 qualname 做 `getattr` 导入）。`qualname()` 是这条约束的守卫，`get()` 未注册返回 `None` 而 `[]` 抛 `KeyError`。**完整方法语义见 §3.1** |

`dcg/__init__.py` 另导出 `__version__`（`"2.1.0"`，与 `pyproject.toml` 的 `version` 逐字一致，
由 `tests/test_docs_consistency.py` 守护）；它不是第 9 个契约符号，只是 manifest 的
`generator_version` 的数据来源。`dcg/jx/__init__.py` 必须再导出 `compile_expr` / `Unsupported` / `Registry`。

> **安全注解**：schema 是**构建期可信输入**。反引号段与 `py{}` 块被原样嵌入产物并在运行期执行任意 Python——`ast.parse` 只约束语法，不拦 `open()` / `__import__`；`Registry` 按 qualname 导入，只支持模块级可导入的函数（lambda/闭包在 build 期报 `Unsupported`）。详见 §3.1（Registry 公开面）、§3.8（反引号段与 `py{}`）与 §7。

### 5.2 次级公开面（模块级，非顶层导出）

* 生成器：`generate` / `build` / `gen_name` / `canonical_json` / `schema_sha1` / `load` / CLI。
* schema 层：`SchemaError` / `validate` / `parse_value_path` / `value_source` / `is_optional` / `TYPES` / `SHAPES` / `NAME_RE` / `FNAME_RE`。
* 表达式层：21 个（§3.1）。
* IR 层：41 个（§1.1）。
* 策略表（`dcg/jx/policy.py` 的 `__all__`，共 **8 名**）：`Unsupported` / `ROW_FUNCS` / `HIGHER_ORDER` / `NONDETERMINISTIC` / `STRUCTURAL` / `BARE_CALL_ALIASES` / `is_row_func` / `rejection_reason`。
  其中 `Unsupported` 是拒绝异常本身（位于本模块，不出现在 `dcg` 顶层导出里），其余 7 个是能力划分用的数据表与查询函数 —— 这就是「7 名 / 8 名」两种写法都出现过的原因：**7 个策略项 + 1 个异常**。

### 5.3 入口

* **生成器入口**（`dcg/build.py`）：只把参数转发给生成器，工程里没有第二份生成逻辑（§4.1）。
* **运行期公开面**只有 §5.1 的 8 个符号加 `dcg.runtime` 的 helper 面。

---

## 6. 能力边界

### 6.1 字段引用与函数支持

| 能力/边界 | 口径 | 表现 |
|---|---|---|
| 路径长度 | 不限：`$.a.b.c.d`、`$.5.a.1.b` 都编成下标链 | 行必须是 dict/对象 |
| 字段引用 | 裸标识符 = 本行字段；`$.` 链里的字段名、动态下标段字段名同样构成依赖（`$字段名` 是变量形态，不是字段引用，见 §3.4 的 `$v` 行） | 未声明 → build 期 `SchemaError` |
| 函数支持 | A 类 46 / B 类 6 / C 类 4（§3.5） | 编成对应 helper 或内联（见 §4.4） |
| 反引号 | 任意 Python 执行，不是沙箱；build 期只 `ast.parse` | 不安全能力，需在文档中如实声明 |
| 非确定函数 | 支持；产物可复现、结果不可复现 | 用注入固定源复跑（§3.7） |
| 输出形状 | 编译期常量，不可运行期切换 | 要多种输出就 build 多个产物 |
| 多段文档 | 一个产物只发一种行列表 | 多段文档 = 多个产物 + 一层组装 |
| 产物自包含 | 不依赖第三方包；可依赖 `dcg.runtime` | 来源自证靠 manifest 的 `schema_sha1` |
| 运行期 | 无解释器、无第三方表达式库 | 语义在 build 期固定 |
| 依赖 | 不依赖 `jsonata` 包 | 表达式在编译期翻成 IR |

### 6.2 永久拒绝项

§3.9 的永久拒绝项（**十类结构构造 + `$$`，共 13 条现象**）在 build 期一律报 `Unsupported`，绝不静默降级：`$eval(...)`、区间 `a..b`、正则字面量、位置绑定 `#`、join 绑定 `@`、排序 `^`、变换 `~>` / `|...|`、分组/聚合 `{...}`、Reduce 路径阶段 `{k:v}`、不支持的路径形态，以及 `$$`。逐条表格（拒绝项 / 理由 / 异常文案）见 §3.9；`$x`（变量引用）**不属于**本组，它在生成器报 `SchemaError`（§3.9 末）。

---

## 7. 错误信息格式

### 7.1 `SchemaError`

* 构造：`SchemaError(path, msg)`；`path` 是 **JSON 路径**，`msg` 是违反的规则。
* `str(e)` = `"{path}: {msg}"`。
* `path` 词表：顶层键名（`name` / `input` / `output` / `out`）、`output.<key>`、`out.fields`、`out.fields.<fname>`、`out.fields.<fname>.<value|type|optional|default|fields|aggregate>`，嵌套层级继续用 `.` 连接。**钩子里的裸名错误统一报 `out.fields`**：`output.before` / `output.after` 的裸名校验复用同一套字段声明集检查，因此报错路径是 `out.fields` 而不是 `output.before` / `output.after`。
* 文案规范：先定位（path），再说明违反什么，可选给出改法。
* 示例：

```
name: 必须是 [A-Za-z_][A-Za-z0-9_]*，得到 'cap-1'
out.fields.sum: value/const/expr 必须恰好提供一个（给了 2 个: value、const）
out.fields.hourD.type: 必须是 int64/int32/float64/str/bool/list/object/any 之一，得到 'number'
out.fields.z.expr: 裸名 'foo' 不是本次 schema 声明的字段；已声明: ['a', 'z']（行内取值请写 $.foo 或 $.路径）
output.before: before 不接受布尔值：它是「整体数据 → 新数据」的转换钩子，请写 JSONata 表达式（如 "= $map($, function($r){ $r })"）或外部函数（"@pkg.mod.fn"）
```

### 7.2 `Unsupported`

* 构造：`Unsupported(feature, loc=None, msg=None)`；属性 `loc`（`(line, col)` 或 `None`）与 `col`。
* `str(e)` 三种形态：

| 情形 | `str(e)` |
|---|---|
| 单行、有 loc | `第 {col} 列: {feature} 不可逐行编译（{msg}）` |
| 多行 | `第 {line} 行第 {col} 列: {feature} 不可逐行编译（{msg}）` |
| 无 loc | `{feature} 不可逐行编译（{msg}）` |

* `feature` 是「最小判据名」（如 `正则字面量`、`未知函数 $eval`、`反引号段 Python 语法错误`）；`msg` 是原因与改法。
* 示例：

```
第 1 列: 正则字面量 不可逐行编译（契约 §3.5 要求拒绝）
第 1 列: 反引号段 Python 语法错误 不可逐行编译（invalid syntax（段内偏移 0）—— 必须是单个 Python 表达式）
第 6 列: $eval 不可逐行编译（运行期求值任意表达式 = 运行期解释器入口 + 注入面）
```

### 7.3 抛出时机

* `SchemaError` 与 `Unsupported` **只在 build 期**抛出（`validate` / `compile_expr` / `generate` / `build`）。
* 运行期（`run(rows)`）不产生这两类异常；运行期错误（如输入不是 dict）不得包装成 `SchemaError`。
