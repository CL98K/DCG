# DCG 接口实现细节文档

> **DCG 是什么**：DCG 是一个运行时代码生成器：用 JSON schema + JSONata 子集描述“怎么转”，在 build 期编译成固化的 Python 模块 `gen/<name>.py`，运行期直接运行，无解释器开销。
> **读者**：DCG 的实现者与评审者。
> **定位**：本文是 DCG 的**接口与实现契约**。设计与架构以 `DCG 整体设计方案与架构文档.md` 为准，JSONata 子集与逐条示例以 `JSONata 语法.md` 为准；本文把两者落到接口层——每个模块的公开面、数据结构、输入输出、生成码形态、错误形态与性能口径。
> **范围**：IR、schema、表达式编译、代码生成、运行期 helper、公开 API、能力边界、错误格式、性能与形态口径。
> **总原则**：build 期全量报错（带列号或 JSON 路径）；运行期没有解释器；产物确定性（同 schema 同字节，可 diff 可评审）。

---

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
│  ├─ policy.py         能力策略表：ROW_FUNCS / HIGHER_ORDER / NONDETERMINISTIC / STRUCTURAL
│  ├─ paths.py          路径段解析与结构工具
│  └─ registry.py       函数注册表（唯一扩展点）
├─ codegen.py           IR → Python 源码（plan / emit / body / module / finish 五步）
├─ runtime.py           生成码运行期 helper（undefined 哨兵、82 个 helper）
├─ migrate.py           旧版 describe 格式 → 当前 schema 的一次性迁移导出器
└─ build.py             构建入口
```

本文以下各节按上述顺序逐个说明该目录内的模块。

## 1. `dcg/ir.py` —— IR

### 1.1 IR 的形态

IR 是**纯 tuple**（不是 class）：可比较、可哈希、零构造开销、snapshot 友好。每个节点的**末元素恒为 `loc`**（`(line, col)` 或 `None`），任何节点都能直接报列号。

IR 共 **41 个符号**：

| 组成 | 数量 | 内容 |
|---|---|---|
| 节点常量 | 16 | `KINDS` 中的种类名（见 §1.2） |
| 算子集合 | 3 | `KINDS` / `UNARY_OPS` / `BIN_OPS` |
| 构造器 | 16 | 每个节点一个：`lit_` `field_` `index_` `root_` `var_` `unary_` `bin_` `cond_` `call_` `rfunc_` `array_` `object_` `py_` `pyblock_` `range_` `lambda_` |
| 结构工具 | 6 | `kind` / `loc_of` / `children` / `walk` / `is_const` / `key_path` |

构造器只做**结构校验**（参数个数、`op` 合法性、`loc` 形态），非法即 `ValueError`；策略拒绝不在这里做（由 §3 的表达式编译层在更早的位置抛 `Unsupported`）。

### 1.2 节点表

```python
("lit", value, loc)                         # Python 常量
("field", name, loc)                        # 裸标识符 = 本行字段；name 不含 "$"
("index", base, idx, loc)                   # base[idx]；idx 为 lit → Python 下标；
                                            # idx 非 lit → 运行期安全下标 helper（§3.4）
("root", loc)                               # "$" → 行对象本身
("var", name, loc)                          # JSONata 变量 $v；name 不含 "$"
("unary", op, operand, loc)                 # op ∈ {"-","+","not"}
("bin", op, left, right, loc)               # op 见 §1.3
("cond", test, then, orelse, loc)           # 三元 ? :
("call", fname, [args...], loc)             # fname 含 "$"，如 "$uppercase"
("rfunc", qualname, [args...], loc)         # 注册函数；qualname 形如 "pkg.mod.fn"
("array", [items...], loc)                  # 数组字面量
("object", [(key, val_ir), ...], loc)       # 对象字面量，保序
("py", source_str, [free_field_names...], loc)      # 反引号段（不透明）
("pyblock", stmts_source, [free_field_names...], loc)  # py{} 多语句块（不透明）
("range", lo, hi, loc)                      # 区间；永远不出现在流出的 IR 里（§3.9）
("lambda", [param_names], body, loc)        # 函数字面量（§3.6），可达
```

**几点硬约定**

* `field` 的成员资格**不在 IR 层校验**：`dcg/jx` 是纯函数、没有 schema；裸名是否为已声明字段由生成器在 build 期校验（§4.3），错误形如 `SchemaError: out.fields: 裸名 'foo' 不是本次 schema 声明的字段；本次 schema 已声明: [...]`。
* `index` 的常量链（`$.5.a.1`）与动态下标（`$.5[a].1`）是**同一个节点**，区别只在 `idx` 是否为 `("lit", ...)`。常量链由 `key_path` 折成段元组；动态下标在 required 字段里内联为裸下标 `base[idx]`，在 optional/default 字段里走运行期安全下标 helper `_ix_get`，**不得**用 `_ix`（`_ix` 是 `$indexOf`，语义完全不同）。
* `py` / `pyblock` 的 `free_field_names` 元素有两种形态：`$.` 链的**链文本**（不含前导 `$.`，如 `"5.a.1"`）与**裸名**（如 `"hourB"`）。链的解析与 JSONata 路径共用同一实现（§3.8）。
* `pyblock.stmts_source` 是编译期已定稿的 Python **语句序列**文本：块内局部名已重命名为 `__jx*` 前缀、恰好一个 `return`。codegen 落地为命名辅助函数（§4.3），不得用 lambda/IIFE 承载多语句。
* `range` 只为节点表完备而存在：`..` 在表达式编译期就抛 `Unsupported`，该节点不会被发射。

### 1.3 `bin` 的 op 与 undefined 传播

| op | 含义 | undefined 传播 | 运行期 helper |
|---|---|---|---|
| `+` `-` `*` `/` `%` | 算术 | 传播；`null` 参与算术是**错误**（不是 undefined） | `_ad` `_sb` `_ml` `_dv` `_md` |
| `&` | 字符串拼接 | **不传播**，undefined 当空串 | `_cc` |
| `=` `!=` | 相等 | 任一侧 undefined → `False` | `_eq` `_ne` |
| `<` `<=` `>` `>=` | 大小 | 任一侧 undefined → **undefined** | `_lt` `_le` `_gt` `_ge` |
| `and` `or` | 逻辑 | 按 JSONata 真值；undefined **参与运算** | `_and` `_or` |
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

## 2. `dcg/schema.py` —— Schema 校验与归一化

### 2.1 公开面

| 符号 | 签名 | 说明 |
|---|---|---|
| `validate` | `validate(schema) -> dict` | 校验 + 归一化深拷贝；返回 §2.4 的形状 |
| `SchemaError` | `SchemaError(path, msg)` | `ValueError` 子类；`.path` / `.msg`，`str(e) == f"{path}: {msg}"` |
| `parse_value_path` | `parse_value_path(s: str) -> tuple` | `"$.5.a.1"` → `(5, "a", 1)`；非法即 `SchemaError` |
| `value_source` | `value_source(spec) -> str` | 返回 `"value"` / `"const"` / `"expr"`（三键恰好一个） |
| `is_optional` | `is_optional(spec) -> bool` | `spec.get("optional", False)` |
| `TYPES` | `("int64","int32","float64","str","bool","list","object","any")` | `type` 取值 |
| `SHAPES` | `("list","dict","index","rows")` | `output.shape` 取值 |
| `NAME_RE` / `FNAME_RE` | 正则 | `name` 与字段名的合法字符集 |

### 2.2 顶层四块

| 键 | 必填 | 作用 | 形态 |
|---|---|---|---|
| `name` | 是 | 产物模块名 | `[A-Za-z_][A-Za-z0-9_]*` |
| `input` | 否 | 输入行来源 | `{"source": "rows" \| "index"}`，默认 `rows` |
| `output` | 是 | 输出形态 | `shape` / `single` / `filter` / `post` / `trace` 五键（§4.1） |
| `out` | 是 | 输出结构 | `{"type": "object", "fields": {...}}`，`fields` 必须非空 |

* 顶层出现未知键 → `SchemaError`（不允许静默忽略）。
* `input.source = "rows"`：`run(rows)` 直接迭代行序列；`"index"`：输入是 `{"index": [行, ...]}`（**行数组**，元素顺序即遍历顺序），产物先解包 `_d["index"]`，其余与 `rows` 相同（容器只作输入包装，不参与输出编号）。

### 2.3 FieldSpec

每个字段恰好用三键之一声明值来源：

| 键 | 形态 | 编译结果 |
|---|---|---|
| `value` | `"$.a.b"` / `"$.5.a.1"` | 直接引用输入列的访问链（零额外开销） |
| `const` | 任意 JSON 常量 | 内联常量 |
| `expr` | JSONata 子集 + 反引号内联 Python | 编译成定制表达式 |

配套声明：

| 键 | 必填 | 说明 |
|---|---|---|
| `type` | 是 | 八选一（§2.1）；**声明**不是转换：声明越具体，生成码越能省掉类型判断（§4.2 规则 4） |
| `optional` | 否 | 默认 `false`；为 `true` 时值可能是 undefined，产物生成一次守卫（undefined → `None`） |
| `default` | 否 | 字段算出 undefined 时替换成该 JSON 常量；必须与 `type` 相容、与 `fields`/`items` 互斥；设了 `default` 不再生成 `None` 分支（§4.2 规则 7） |
| `fields` | 视 `type` | `type="object"` 时必填且非空（嵌套对象） |
| `items` | 视 `type` | `type="list"` 时必填（嵌套数组元素） |

嵌套**必须显式**：对象走 `fields`，数组走 `items`，递归适用；没有「list 里出现 dict 就当嵌套」这类隐式规则。

> **实现现状提示**：`items` 的当前实现与目标语义存在落差——它被当作“嵌套字段规格”，产物生成按声明类型的一元素数组，并不把行数据映射成数组元素列表；落差登记见 `README.md` §2.5。

### 2.4 归一化结果

`validate` 返回**深拷贝**，默认值补齐、顺序保持用户声明顺序：

```jsonc
{
  "name": "cap",
  "input":  {"source": "rows"},
  "output": {"shape": "list", "single": false, "filter": true, "post": null, "trace": false},
  "out": {
    "type": "object",
    "fields": {
      "hourA": {"type": "int64", "value": "$.5.a.1"},
      "hourE": {"type": "int64", "expr": "$.5.a.1 + $.5.a.2"}
    }
  }
}
```

`out.fields` 的键序即声明序（Python dict 保序）；生成器只读归一化结果。

### 2.5 声明顺序与计算顺序

* **输出按声明序**：`list` 按字段声明顺序取值；`dict` 按声明顺序建键；嵌套 `object` 按内层声明顺序。行数据的键顺序不影响输出顺序。
* **计算按依赖关系**：字段之间通过表达式里的字段引用构成依赖；生成器在 build 期建依赖图，按稳定拓扑序求值（Kahn 式逐轮取依赖就绪字段、每轮按声明序 tie-break），与声明顺序无关；同一 schema 的求值顺序唯一确定，产物逐字节可复现。
* **依赖来源**：表达式里的裸名（`hourA`）、`$字段名`、**动态下标段里的字段名**（`$.5[hour].v` 依赖 `hour`）。
* **前向引用合法**：后声明的字段可以被先声明的字段引用。
* **依赖必须无环**：环 → `SchemaError` 并报出环上的字段链；引用未声明字段 → `SchemaError` 并列出可用字段名。
* **每个字段最多求值一次**：被多字段引用时绑定一次复用（内联决策见 §4.2 规则 1）。
* **作用域**：内层嵌套字段可引用外层字段；外层不能引用内层字段名。
* **`py{}` 与反引号段里的 `$.字段名`**：build 期只校验「必须是已声明字段」，块内读**原始行**值；不构成依赖边（因此 `v = py{ $.a }` 与 `a = v + 1` 可正常生成）。反引号段里的**裸名**（如 `` `a + 1` ``）才是字段引用，读该字段的计算结果并参与依赖图。

### 2.6 硬规则与报错

1. **裸字符串不是路径**：`"a"` 必须报 `SchemaError`；`value` 只能以 `$` 开头的路径，或改走 `const`。
2. **嵌套必须显式**：嵌套结构用 `fields` / `items` 声明；不写就不展开。

报错形态统一：`SchemaError(path, msg)`，`path` 是 JSON 路径，`str(e)` = `"{path}: {msg}"`。

```
out.fields.sum: value/const/expr 必须恰好提供一个（给了 2 个）
out.fields.hourD.type: type 必须是八种之一：int64/int32/float64/str/bool/list/object/any（给的是 "number"）
out.fields.x.value: value 必须是 "$" 开头的路径（给的是 "a"）
out.fields: 裸名 "foo" 不是本次 schema 声明的字段；本次 schema 已声明: hourA, hourB, hourE
```

---

## 3. `dcg/jx/` —— 表达式编译

### 3.1 公开面

```python
# dcg/jx/compiler.py
class Unsupported(Exception):
    def __init__(self, feature: str, loc=None, msg: str | None = None): ...
    # str(e) 见 §8.2；属性 loc / col

def compile_expr(src: str, *, loc_base=(1, 1), registry=None) -> tuple:
    """JSONata 源码 → §1 的 IR。遇不支持结构抛 Unsupported（带列号）。"""

# dcg/jx/registry.py
class Registry:
    def fn(self, name: str, callable) -> None: ...   # name 形如 "$myfn"
    def get(self, name: str): ...
```

* `compile_expr` 是**纯函数**：同输入同输出，不读任何全局状态；注册表通过参数显式传入。
* `Registry` 只影响表达式层：注册后 `$myfn(...)` 编成 `("rfunc", "pkg.mod.fn", args, loc)`；**不改变 schema 结构**。
* 次级公开面（模块级、非顶层导出，21 个）：`compile_expr` `compile_ast` `Unsupported` `Registry` `default_registry` `analyze_py_segment` `compile_py_block` `parse_expr` `lex` `JxSyntaxError` `ROW_FUNCS` `HIGHER_ORDER` `NONDETERMINISTIC` `parse_path` `parse_chain` `parse_path_chain` `split_chain` `chain_ir` `FIELD_REF` `count_symbolic` `RESERVED_PREFIX`。

### 3.2 词法

* `TOKEN` 正则扫描，`lex(src) -> [(kind, value, col), ...]`。
* 反引号 `` ` `` 是**词法边界**：段内容原样捕获到下一个未转义反引号，段内不解析 JSONata；`py{ ... }` 多语句块整体扫描为一个段。
* 必须识别：数字（含小数/指数）、字符串（单/双引号）、正则字面量 `/.../flags`、`$` / `$$` / `$name`、`$.name`、裸标识符、运算符
  `+ - * / % & = != < <= > >= and or in ? : . [ ] ( ) { } , # @ ^ | ~> ..`。
* `lang` 不是词法或 schema 概念，不存在该键。

### 3.3 语法（Pratt）

结合力（数字越大越紧）：`or 10` · `and 20` · 比较/`in` 30 · `& 40` · `+ - 50` · `* / % 60` · 一元 `- + not` 70 · 后缀 `.` `[` `(` **90** · `..` 45 · 三元 `? :` 5。

* 调用/下标/路径统一在**中缀循环的后缀位置**处理。
* **先判函数归属、再 emit 实参**：归属查 §3.5 的四张表，避免实参里的函数名抢答拒绝原因。
* 解析结果只产出 §1 的节点；`..` 解析后立即由策略判据拒绝（`range` 不流出）。

### 3.4 路径、下标与字段引用

| 源码 | IR | 运行期 |
|---|---|---|
| `$` | `root` | 行对象本身 |
| `$.a` / `$.5` / `$.a.b` / `$.5.a.1` | `index` 常量链（任意长度） | Python 下标链 `R['a']['b']` |
| `$[expr]` / `$.a[expr]` | `index(base, <idx IR>)` | 安全下标 helper（undefined 安全） |
| `a`（裸名） | `field("a")` | 生成局部名（build 期校验已声明） |
| `$v` | `var("v")` | JSONata 变量 |
| `str(x)` 等 6 个裸名调用 | 归一化为对应 `$` 函数 | 同 A 类行内函数 |

* **路径长度不限**：`$.a.b.c.d`、`$.5.a.1.b` 都编成同一种下标链，不需要靠嵌套 `fields` 表达；行必须是 dict/对象。
* **动态下标在 required 字段里内联**（`base[expr]`，`expr` 非字面量）：直接生成 `base[idx]`，越界/缺键/类型不符就是真实 Python 异常（该字段没有 undefined 出口）。
* **动态下标在 optional/default 字段里走 `_ix_get(base, idx)`**：int 下标 / str 键 / 越界与类型不符 → `UNDEF`，交给守卫或默认值。**不得**路由到 `_ix`（`_ix` 是 `$indexOf`）。
* **裸名调用别名**（`BARE_CALL_ALIASES`，6 个）：`str→$string`、`int→$number`、`float→$number`、`bool→$boolean`、`len→$length`、`abs→$abs`；表外的 `a(...)` 一律 `Unsupported`。

### 3.5 函数分类与白名单

归属判定顺序（policy 唯一来源）：`ROW_FUNCS`（A 类）→ `HIGHER_ORDER`（B 类）→ `NONDETERMINISTIC`（C 类）→ `STRUCTURAL`（永久拒绝）。

**A 类｜行内函数（45 个，直接发射运行期 helper）**

| 分组 | 函数 |
|---|---|
| 字符串（19） | `$string $uppercase $lowercase $trim $pad $contains $split $join $substring $substringBefore $substringAfter $replace $length $base64encode $base64decode $encodeUrlComponent $decodeUrlComponent $formatNumber $formatBase` |
| 数值（10） | `$number $abs $floor $ceil $round $sqrt $power $sum $min $max` |
| 布尔/其他（4） | `$boolean $not $exists $type` |
| 数组与对象（12） | `$count $append $average $reverse $sort $distinct $merge $keys $values $lookup $spread $single` |

实参只需**逐行可求值**：字面量、字段取值、动态下标、数组/对象字面量都行；`$sum([$.5.a.1, $.a.c])` 与 `$sum($.items)` 等价，**没有**「仅限字面量数组」的限制。各函数语义见 `JSONata 语法.md` §6–§8。

**B 类｜高阶函数（6 个，需要函数字面量，§3.6）**

`$map $filter $reduce $each $sift`，以及带比较器的 `$sort(array, function)`。`$position()` 只在 B 类函数体内可用（序列上下文）。

**C 类｜非确定函数（4 个，§3.7）**

`$now $millis $random $shuffle`。

**永久拒绝**：见 §3.9。未列入任何表的函数 → `Unsupported`（feature 为「未知函数 `$x`」）。

### 3.6 函数字面量与高阶函数

`function($x){ <表达式> }` 编成 `("lambda", [参数...], body, loc)`，由生成器编译成 Python 函数（表达式体）。函数字面量**只能作为上面六个高阶函数的实参**；直接放到字段表达式里 → build 期 `SchemaError`。

* **函数体是单条表达式**：可用行内函数、字段取值、常量与运算符；**允许内含 `py{}` 块**（与普通字段一样编译成 run() 之外的辅助函数）。
* **函数体内的 `py{}`**：块内**不能引用回调参数**（`$x` 只存在于表达式层），只能读原始行的 `$.字段链`（字段须在 schema 声明）；块编译成 run() 之外的辅助函数，每元素调用一次。
* **自由字段**：函数体引用的字段进入依赖图，在进入函数前绑定；函数参数**遮蔽**同名字段。
* **编译期校验**：参数名是合法 Python 标识符，且不与生成器保留名冲突；参数重复报 `SchemaError`。
* **求值**：`$map` / `$filter` / `$sort` 对行内数组逐元素调用；`$reduce` 带初值折叠；`$each` 按对象键值对回调；`$sift` 过滤对象键。全部行内完成，不需要整批状态。
* **语义基准**：与 JSONata 1.8 对齐（空数组、`undefined` 元素、比较器返回值约定、`$position` 语义）。

### 3.7 非确定函数与注入接口

`$now()`（ISO 8601 UTC 毫秒时间戳）、`$millis()`（epoch 毫秒）、`$random()`（`[0,1)` 浮点）、`$shuffle(array)`（洗牌）由运行期 helper 提供，产物里只出现 helper 调用（`_nw()` / `_ms()` / `_rn()` / `_sh()`）。

* **产物仍可复现**：生成代码确定，同一 schema 逐字节一致；非确定只发生在运行结果。
* **注入接口**：运行期 helper 从模块级**可替换**的时钟/随机源取值（`dcg.runtime` 暴露时钟与随机源对象，测试与证据重跑用固定源替换）；**不引入环境变量**这类隐式全局开关，避免产物行为随宿主环境静默变化。
* **结果不可复现**：同一产物两次运行结果可能不同；使用 C 类函数的切片在证据里标注「输出含非确定值」，并用固定源重跑一次作为复跑证据。

### 3.8 反引号段与 `py{}` 块

**反引号段**（`expr` 里的 `` `...` ``）：

1. 段内必须是**单个 Python 表达式**：`ast.parse(src, mode="eval")` 在 build 期校验；违规 → `Unsupported`（带段内字符偏移），错误形如
   `第 1 列: 反引号段 Python 语法错误 不可逐行编译（invalid syntax（段内偏移 1）—— 必须是单个 Python 表达式）`。
2. 字段引用 = `$.` + 链（`$.a`、`$.5.a.1`、单段 `$.data` 也合法），或**裸名**（本 schema 已声明字段）；字符串字面量里的裸名不登记。
3. 解析结果登记进 `free_field_names`：链文本（不含前导 `$.`）或裸名；成员资格校验落在生成器（§4.3），不在表达式层。
4. 反引号段 = **任意 Python 执行，不是沙箱**：build 期不执行代码，只 `ast.parse`；`PY_SAFE_MODULES`（25 个）只约束**裸模块名**（`math re json decimal datetime itertools ...`），不约束内置函数。
5. `__jx` 是表达式层保留的生成器命名空间（前缀），用户段里出现即报错。

**`py{}` 多语句块**：

* 块内只允许 `let NAME = EXPR;` 声明与结尾唯一一条 `return EXPR`；`import`、赋值、`if/for/while/try/def/class`、裸字段名（须 `$.路径`）、`$x`/`$$` 一律拒绝。
* 编译期把语句序列定稿为辅助函数体：局部名重命名为 `__jx*` 前缀，恰好一个 `return`。
* 生成器把它发成 `run()` **之外**的 `def _pyN(f1, f2, ...): <stmts>`，调用处传已解包字段；**禁止** lambda/IIFE 承载多语句；辅助函数不得每行重建；`_pyN` 编号按源码出现顺序，禁止 random。
* 块内 `$.` 链读原始行、不进入依赖图（只校验已声明）；反引号段里的裸名是字段引用，进入依赖图。

### 3.9 永久拒绝

| 拒绝项 | 理由 | 异常 |
|---|---|---|
| `$eval(...)` | 运行期求值任意表达式 = 运行期解释器入口 + 注入面 | `Unsupported` |
| `a..b` 区间 | 需要序列语义（生成器/迭代器），逐行编译无对应形态 | `Unsupported` |
| 正则字面量 `/.../flags` | 正则编译有状态、引入隐藏缓存 | `Unsupported` |
| `~>`、`\|...\|`、`^`、`#`、`@`、`{k:v}`、`{..}` | 变换/排序/绑定/分组都是整批语义 | `Unsupported` |

**一律抛出，绝不静默降级。** feature 是「最小判据名」，msg 是原因；格式见 §8.2。

### 3.10 语义基准

* 语义基准是**官方 JSONata 1.8 规范**；JSONata 子集与逐条示例见 `JSONata 语法.md`。
* 仓库内冻结对照集 `bench/frozen/conformance.json`：50 例（core 26 + edge 24），期望值由官方实现一次性求出后固化；**运行期不依赖 `jsonata` 包**。
* C 类非确定函数不进对照集：用注入的固定源校验类型与范围。
* 语义边界以本文 §3.5 / §3.9 与 `DCG 整体设计方案与架构文档.md` §6.2 / §6.4 为准。

---

## 4. `dcg/codegen.py` —— 代码生成

### 4.1 公开面与输出形状

| 符号 | 签名 | 说明 |
|---|---|---|
| `generate` | `generate(schema: dict, *, cython: bool = False, inline_optional: bool = True) -> str` | schema → 完整模块源码；确定性；内部只调用一次 `compile()`；返回前验证可编译 |
| `build` | `build(schema, out_dir, *, name=None, overwrite=False) -> dict` | 写 `<out_dir>/gen/<name>.py`；目标存在且内容不同 + `overwrite=False` → `SchemaError`；返回 manifest `{name, schema_sha1, artifact, lines, chars, cython, trace_artifact}`（`trace_artifact` 在 `output.trace=true` 时为附属调试产物路径，否则 `None`） |
| `gen_name` | `gen_name(schema) -> str` | 确定性模块名 = `schema["name"]` |
| `canonical_json` | `canonical_json(obj) -> str` | canonical 序列化（`schema_sha1` 的输入） |
| `schema_sha1` | `schema_sha1(schema) -> str` | 归一化 schema 的 sha1 |
| `load` | `load(path, mod_name=None)` | 载入已生成产物模块 |

生成器 CLI（`dcg/build.py`）：`<schema.json>` / `-o --out`（默认 `.`）/ `--overwrite` / `--stdout`；打印 `name` / `schema_sha1` / `artifact` / `lines` / `chars`。

**输出形状**（编译期常量）：

| `shape` | `run(rows)` 返回 | 说明 |
|---|---|---|
| `list`（默认） | `list` of row | 每行是一个 list |
| `dict` | `dict{字段名: [每行值]}` | **列式**；禁止运行期物化列式 dict |
| `index` | `dict{行下标: 行值}` | 行下标为 int；禁止运行期拼接 |
| `rows` | `list` of `dict{字段名: 值}` | **行式**；键序 = 字段声明序 |

* `single=true`：`list` 返回**单行本身**，`rows` 返回**首个行 dict**；`dict`/`index` 与 `single=true` 组合 → `SchemaError`（该形状返回整列/整表，`single` 无意义）。
* `filter=true`（默认）：逐行 `if R:` 丢弃 falsy 行；`false` 时不生成该判断。
* `post`：`null` 或 `"pkg.mod.fn"`；非空时产物在返回前对组装后的结果调用**恰好一次**（按需 import，不进逐行热路径）。
* `trace`：调试开关；`true` 时额外生成独立调试产物，**正式产物与 `trace=false` 逐字节相同**，不污染热路径。
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

产物**不写 docstring**：来源由 manifest 的 `schema_sha1` 记录；产物正文保持最小。

**九条形态规则**（形态判据，逐条可核对）：

1. **每个被引用的下标前缀在行首只取一次**：命名阶段记忆化（`$.5.a.1` → `_a` → `_a1` → `_a2`），任何字段复用同一前缀直接返回已有名字；按计算顺序（依赖拓扑序）解包，被引用字段先算并绑定；只被引用一次的可在使用处内联，未被引用的在组装输出时内联；**输出始终按声明序组装**。
2. **零恒等赋值、零「只为 return 而生的中转变量」**：不生成 `datas = datas`、`_buf = {'doc': []}; _doc = _buf['doc']`、`append` 别名。
3. **输出形状编译期固定**：`list` 直接构造列表；`dict` 生成 `{字段名: [每行值]}` 列式字面量；`index` 生成 `{行下标: 行值}`；`rows` 在尾部包一层行 dict 推导式（行字面量仍是 list）。无运行期容器判断。
4. **无冗余类型转换**：`type` 是声明不是转换；`type=int64` 不插入 `int(...)`，需要转换就在 `expr` 里显式写 `` `int($.x)` ``。快路径：`$string(a) & $string(b)` 直接生成 `_S(a) + _S(b)`，不再套 `_cc(...)`。
5. **无 `isinstance` 分派链；每个运算符一个专用 helper**：算术/比较/逻辑/拼接各走专用函数，数值守卫走 `_num(v)`、比较守卫走 `_cmp_ok(a,b)`；产物里不内联分派逻辑。
6. **每次 build 只 `compile` 一次，无空桩**：`generate()` 里 `compile()` 恰好一次。
7. **`optional: true` 才生成守卫，且恰好一次 undefined 判断**：内联形态 `X=None if _t is _U else _t`；设了 `default` 的字段改为编译期常量替换 `_df(<expr>, <default>)`；必然存在的字段零判断。
8. **变量名确定性、可读、1–3 字符**：`_` + 字段短名（链段命名 `_a` `_a1` `_a2`，冲突加数字后缀）；由字段声明顺序与链结构决定，禁止 random；命名只跟声明序与链有关、与计算顺序无关，保证同 schema 产物逐字节可复现。
9. **Cython 模式同源**：`cython=True` 的产物恒等于「纯 Python 产物 + 每局部一行 `cdef object`」，逐行一一对应，不改语义；不计入字符预算。

**helper 内联纪律**：helper 按需内联并只带传递依赖（按 helper 传递依赖拓扑排序）；产物只在真的用到 helper 时才发运行期导入行；CAP/M0 这类纯内联负载连一行 import 都不产生。

### 4.3 与表达式层衔接

`schema.out.fields[f].expr` → `compile_expr(src)` → IR → `emit(node) -> str`（Python 表达式源码，内联形式）。

* **裸名校验**：每个 `("field", name)` 与反引号/`py{}` 的裸名都要查 schema 已声明字段集；未声明 → **build 期** `SchemaError` 并列出可用字段名（绝不拖到运行期 `NameError`）。
* **`py` 节点**：`ast.parse(source, mode="eval")` 校验（build 期）；`free_field_names` 逐条替换为生成局部名，链解析与 JSONata 路径共用同一实现。
* **`pyblock` 节点**：`ast.parse(stmts_source, mode="exec")` 且恰好一个 `return`；发成 `def _pyN(f1, f2, ...)`，调用处传已解包字段。
* **`rfunc` 节点**：生成 `Registry` 查表调用，或按 `qualname` 直接 import。
* **`inline_optional`**（默认 `True`）：`True` 时守卫内联为 `X=None if _t is _U else _t`；`False` 时改走运行期 `is_u()`（`X=None if is_u(_t) else _t`），产物多一次调用。

**内联判据**（`type` 是声明 ⇒ 值的可能性可静态判定）：

| 情形 | 产物形态 |
|---|---|
| `optional` 非真 且 `type ∈ {int64,int32,float64,str,bool}` | 裸运算 / 内建：`a+b`、`str(x)`；字符串函数实参类型可证时发内建方法：`s.upper()`、`s.lower()`、`s.strip()` |
| `optional: true` | 内联守卫：`X=None if _t is _U else _t` |
| 字段设了 `default` | 编译期常量替换：`_df(<expr>, <default>)`；值存在时原样返回 |
| 操作数类型可证（与目标字段类型无关） | 裸运算 / 内建方法 / 裸字面量 / 裸比较逻辑：`a+b`、`s.upper()`、`[1,2,3]`、`{'k':a}`、`(a<b)`、`(a and b)`、`(x if c else y)` |
| 比较/逻辑/条件的 JSONata 特例 | 保留 helper：混合类型（`1 = true` 为 `false`）、可能 `undefined` 的操作数（`_eq`/`_lt`/`_and`/`_tru`/`_co` 负责传播） |
| 有语义一致的 Python 内建等价的列函数 | 直接发内建表达式：`$length`→`len()`、`$count`→`len()`、`$abs`→`abs()`、`$contains`→`in`、`$split`→`str.split()`、`$substring`→切片、`$substringBefore/After`→`partition()`、`$replace`→`str.replace()`、`$pad`→`str.ljust()`、`$sum/$max/$min/$average`→内建聚合（可证数值元素；`$sum([])`→`sum([])`）、`$reverse`→`([...])[::-1]`、`$sort`（同类型字面量数组）→`sorted(...)`、`$boolean`→`bool()`、`$not`→`not`、`$floor/$ceil`→`math.floor/ceil`、`$round`（1 参）→`round()`、`$power`→`a**b`、`$sqrt`（可证非负）→`math.sqrt` |
| 无内建等价或语义有差异 | 保留 helper：`$number`、`$sqrt`（不可证非负）、`$round`（2 参精度）、`$join`、`$type`、`$exists`、`$keys`/`$values`/`$lookup`/`$merge`/`$spread`、`$reverse`/`$sort`（实参不可证为同类型数组、或 `$sort` 带比较器）、字符串编码类、高阶与非确定函数 |
| `type == "any"` 或类型无法静态判定 | helper 调用（`_ad` / `_S` / …，语义安全网） |

**裸代码 vs helper 的判定顺序**（自上而下，命中即定）：

| # | 情形 | 产物 |
|---|---|---|
| 1 | 字面量、常量链、常量运算 | 裸代码 |
| 2 | `value` 路径读值：required 且声明具体 `type` | 裸读 `R['a']`、`R['a']['b']`；缺键 = `KeyError` |
| 3 | `value` 路径读值：`type: "any"`；或表达式 / 下标里的**单段**路径 `$.x` | `_ix_get` 安全链（缺键 = undefined） |
| 4 | 表达式里的**多段**路径 `$.a.b`、`$.5.a.1` | 裸链；同一路径被多处引用或作为前缀时行首绑定一次 |
| 5 | 运算 / 函数 / 字面量容器，操作数类型可证 | 裸运算符 / 内建 / 裸字面量 |
| 6 | 操作数类型不可证（`any`、路径值、函数结果、lambda 参数） | helper |
| 7 | `optional` / `default` 字段的值 | `_ix_get` + 守卫 / `_df` |
| 8 | 动态下标 | required → `base[idx]`；optional/default → `_ix_get` |
| 9 | 语义特例（无内建等价 / 比较特例 / 高阶 / 非确定） | helper |

「操作数类型可证」的来源：字面量；`optional` 非真且声明了具体 `type` 的字段；上述值的算术 / 比较 / 逻辑 / 条件 / 内建函数结果；字面量容器。

内联只改产物形态、不改 undefined 语义；语义只在「值真可能 undefined」的地方花钱。

字符串函数 `$uppercase` / `$lowercase` / `$trim` 在实参类型可证为 str 时同样走内建方法调用（`s.upper()` 等），不再套一层 helper。

`$floor` / `$ceil` / `$round`（1 参）/ `$power` / `$sqrt`（可证非负）走内建表达式时，产物首行额外加 `import math`（仅在用到时）；`$sqrt` 的负数是 helper 的 `undefined` 语义，`math.sqrt` 会抛 `ValueError`，因此只有字面量 `>= 0` 或 `$abs(...)` 才内联。

### 4.4 运行期 helper 与 undefined 语义

`dcg/runtime.py` 是 undefined 语义的**唯一实现处**，同时是生成码内联文本的来源。

* **undefined 用单例表示**：一个空 `__slots__` 的哨兵类、一个模块级单例 `UNDEF`，判定用身份比较（`is`）；产物里短名 `_U`。产物**不自建哨兵**。
* **82 个 helper**，按类别：算术/比较/逻辑/字符串/数值/数组/类型/下标/守卫；生成码里的内联文本由 `SOURCE` / `HELPERS` **从源码派生**（与实现同源，不手抄）。
* `__all__` 导出 **92 个名字**（含全部下划线 helper 与哨兵）；产物通过一行 `from dcg.runtime import *` 取得。
* `SOURCE_NEEDS_MATH`（本快照 `{"_fl","_ce","_sq"}`）→ 用到才生成 `import math as _m`。
* **自包含性的口径**：产物**不依赖第三方包**，但可以依赖 `dcg.runtime`（哨兵与 helper 只有一份实现，不复制进产物）。

**undefined 六条语义**（必须逐条保持）：

1. `&` 的 undefined **不传播**，当空串：`'x' & miss == 'x'`
2. `or` 的 undefined **参与运算**：`miss or true == True`
3. `=`/`!=` 遇 undefined 返回 `False`：`miss = miss == False`
4. 大小比较遇 undefined 返回 **undefined**：`miss < 5 == undefined`
5. 数组字面量**跳过** undefined：`[miss, a] == [1]`
6. 对象字面量**丢弃** undefined 键：`{'k': miss} == {}`

附加三条：`null` 参与算术是**错误**（不是 undefined）；`$not(miss) == undefined`；`a[0]` 对标量也成立（`a[0] == a`）。

### 4.5 产物形态样例

**CAP 负载**（4 字段，整模块 **149** 字符）：

```python
def run(_d):
 _o=[]
 for R in _d:
  if R:
   _a=R[5]['a']
   _a1=_a[1]
   _a2=_a[2]
   _o.append([_a1,_a2,(str(_a1)+str(_a2)),(_a1+_a2)])
 return _o
```

**M0 负载**（8 字段，整模块 **195** 字符）：

```python
def run(_d):
 _d=_d['index']
 _o=[]
 for R in _d:
  if R:
   _a=R[5]['a']
   _a1=_a[1]
   _a2=_a[2]
   _o.append([_a,_a1,_a2,R[2],str(_a1) + str(_a2),(_a1+_a2),(_a2+_a1),(_a[2]+123)])
 return _o
```

**字段互相引用时**：被引用字段的求值提前到计算顺序前段，输出仍按声明序。例如 `b` 声明在 `a` 前、`b = $a * 2`，产物先算 `_a` 再算 `_b`，append 里仍是 `[..._b,..._a...]`。

**其它形状的尾部**（同一行字面量，只换结尾）：

| shape | `return` 形态 |
|---|---|
| `list` | `return _o`（`single` 时 `return _o and _o[0]`） |
| `dict` | `return {k: [r[i] for r in _o] for i, k in enumerate([...字段名...])}` |
| `index` | `return {i: r for i, r in enumerate(_o)}` |
| `rows` | `return [{k: r[i] for i, k in enumerate([...字段名...])} for r in _o]` |

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
| `generate` | `generate(schema, *, cython=False, inline_optional=True) -> str` | schema → 模块源码 |
| `build` | `build(schema, out_dir, *, name=None, overwrite=False) -> dict` | 写产物 + manifest |
| `gen_name` | `gen_name(schema) -> str` | 确定性模块名 |
| `compile_expr` | `compile_expr(src, *, loc_base=(1,1), registry=None) -> tuple` | JSONata → IR |
| `Unsupported` | `Unsupported(feature, loc=None, msg=None)` | 不支持项异常（§8.2） |
| `Registry` | `Registry().fn(name, callable)` / `.get(name)` | 用户函数注册表 |

`dcg/jx/__init__.py` 必须再导出 `compile_expr` / `Unsupported` / `Registry`。

### 5.2 次级公开面（模块级，非顶层导出）

* 生成器：`generate` / `build` / `gen_name` / `canonical_json` / `schema_sha1` / `load` / CLI。
* schema 层：`SchemaError` / `validate` / `parse_value_path` / `value_source` / `is_optional` / `TYPES` / `SHAPES` / `NAME_RE` / `FNAME_RE`。
* 表达式层：21 个（§3.1）。
* IR 层：41 个（§1.1）。
* 策略表：`ROW_FUNCS` / `HIGHER_ORDER` / `NONDETERMINISTIC` / `STRUCTURAL` / `BARE_CALL_ALIASES` / `is_row_func` / `rejection_reason`。

### 5.3 入口与迁移

* **生成器入口**（`dcg/build.py`）：只把参数转发给生成器，工程里没有第二份生成逻辑（§4.1）。
* **迁移器**（`dcg/migrate.py`）：旧版 describe → 当前 schema 的**一次性**迁移工具，参数
  `<旧版导出文件> -o out.json [--report r.md] [--name N] [--check] [--build-dir D] [--overwrite] [--quiet] [--no-wrap-doc]`；
  逐项报告 `mapped / derived / manual / unsupported / info`，**绝不静默丢弃**。
  旧版 `protocol=True` 的输出是文档（`{"doc": 行列表}`）：v2 没有 `shape="document"`，所以迁移器默认
  生成包装模块 `<name>_wrap.py` 并写 `output.post` 把它包回去（记为 `derived`）；`--no-wrap-doc` 关闭包装
  （记 `manual`、退出码 3）。多组 `struct` 不自动包装，报告给出「每组一个产物 + 一层组装」的建议。
  不属于运行期公开面，也不在顶层导出。
* **运行期公开面**只有 §5.1 的 8 个符号加 `dcg.runtime` 的 helper 面。

---

## 6. 能力边界

| 能力/边界 | 口径 | 表现 |
|---|---|---|
| 路径长度 | 不限：`$.a.b.c.d`、`$.5.a.1.b` 都编成下标链 | 行必须是 dict/对象 |
| 字段引用 | 裸标识符 = 本行字段；`$字段名`、动态下标段字段名同样构成依赖 | 未声明 → build 期 `SchemaError` |
| 函数支持 | A 类 45 / B 类 6 / C 类 4（§3.5） | 永久拒绝项一律 `Unsupported`（§3.9） |
| 反引号 | 任意 Python 执行，不是沙箱；build 期只 `ast.parse` | 不安全能力，需在文档中如实声明 |
| 非确定函数 | 支持；产物可复现、结果不可复现 | 用注入固定源复跑（§3.7） |
| 输出形状 | 编译期常量，不可运行期切换 | 要多种输出就 build 多个产物 |
| 多段文档 | 一个产物只发一种行列表 | 多段文档 = 多个产物 + 一层组装 |
| 产物自包含 | 不依赖第三方包；可依赖 `dcg.runtime` | 来源自证靠 manifest 的 `schema_sha1` |
| 运行期 | 无解释器、无第三方表达式库 | 语义在 build 期固定 |
| 依赖 | 不依赖 `jsonata` 包 | 表达式在编译期翻成 IR |

---

## 7. 性能与形态口径

### 7.1 字符预算

两个基准负载（由 `bench/_workload.py` 定义，验收与基准测试共享同一批对象；量**整模块**源码字符数）：

| 负载 | 定义 | 上限 |
|---|---|---|
| **CAP** | 4 字段：`$.5.a.1` / `$.5.a.2` / `ext $string($.5.a.1) & $string($.5.a.2)` / `ext $.5.a.1 + $.5.a.2`，`filter=true` | **≤180**（形态 149） |
| **M0** | 8 字段：`$.5.a` / `$.5.a.1` / `$.5.a.2` / `$.2` + 3 个 ext + 动态下标 | **≤210**（形态 195） |

字符数是**代理指标**，真正的目标函数是零冗余六条：①每个被引用的下标前缀在行首只取一次 ②零恒等赋值、零为 return 而生的中转变量 ③无 `isinstance` 分派链 ④无冗余类型转换 ⑤输出形状编译期固定、无运行期判断 ⑥每次 build 只 `compile` 一次、无空桩。

### 7.2 测量方法学

* **主口径：CPU 时间**（`time.process_time()`），不是 wall-clock —— 本机 wall-clock 没有 5% 分辨率，跨会话绝对值漂移不可引用。
* 每次测量内跑 **K=8 遍**；各变体 **interleaved** 交替测量；取 **min of reps（reps=7）**，并同时报各变体自身 reps 的 spread。
* 唯一实现是 `bench/bench_row.py` 的 `measure_cpu` / `measure_interleaved_cpu`，**禁止第二份实现**；JSON 必须记 `k` 与 `reps`；测量前后各取一次 sha1 并断言 `revision_stable_during_run`。
* **噪声底标定**（每轮强制）：把同一个 callable 测两次（A/A），用与正式测量相同的 interleaved slot 顺序跑 ≥3 个 block，报最差 `|ratio-1|`，存 `noise_floor_blocks`。
* **判决规则**：

```
margin = |ratio - limit|            # limit = 1.05
if blocks_disagree_in_sign      -> INCONCLUSIVE
elif margin < noise_floor       -> PASS/FAIL, confidence = WITHIN_NOISE
else                            -> PASS/FAIL, confidence = RESOLVED
```

`noise_floor` 是 A/A 实测分辨率，不是全局否决门；`WITHIN_NOISE` / `INCONCLUSIVE` 一律判 **FAIL**（测了但分不清不算通过）。只报 ≥3% 的差异；主判定是**比值**（产物/手写参照 ≤1.05×），不是绝对值。

### 7.3 形态回归门

* 静态检查（九条规则、字符上限）只是**形态回归门**；行为 e2e 才是主判定。
* Cython 产物形态恒等于「纯 Python 产物 + 每局部一行 `cdef object`」，逐行固定。

---

## 8. 错误信息格式

### 8.1 `SchemaError`

* 构造：`SchemaError(path, msg)`；`path` 是 **JSON 路径**，`msg` 是违反的规则。
* `str(e)` = `"{path}: {msg}"`。
* `path` 词表：顶层键名（`name` / `input` / `output` / `out`）、`output.<key>`、`out.fields`、`out.fields.<fname>`、`out.fields.<fname>.<value|type|optional|default|items|fields>`，嵌套层级继续用 `.` 连接。
* 文案规范：先定位（path），再说明违反什么，可选给出改法或迁移提示。
* 示例：

```
name: name 必须是 [A-Za-z_][A-Za-z0-9_]*（给的是 "cap-1"）
out.fields.sum: value/const/expr 必须恰好提供一个（给了 2 个）
out.fields.hourD.type: type 必须是八种之一：…（给的是 "number"）
out.fields: 裸名 "foo" 不是本次 schema 声明的字段；本次 schema 已声明: hourA, hourB, hourE
output.single: shape='dict' 下 single=true 无意义：该形状返回的是整列/整表，不是单行
```

### 8.2 `Unsupported`

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
第 2 列: 正则字面量 不可逐行编译（正则编译有状态，逐行模型不支持）
第 1 列: 反引号段 Python 语法错误 不可逐行编译（invalid syntax（段内偏移 1）—— 必须是单个 Python 表达式）
第 5 列: 未知函数 $eval 不可逐行编译（运行期求值任意表达式 = 运行期解释器入口）
```

### 8.3 抛出时机

* `SchemaError` 与 `Unsupported` **只在 build 期**抛出（`validate` / `compile_expr` / `generate` / `build`）。
* 运行期（`run(rows)`）不产生这两类异常；运行期错误（如输入不是 dict）不得包装成 `SchemaError`。
