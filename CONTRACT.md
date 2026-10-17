# DCG 接口契约（CANONICAL — 冻结）

> 本文件是并行的唯一真相源。任何偏离都必须先改本文件并通知 Lead。
> 依赖方向严格单向，禁止反向 import：
>
> ```
> schema.py ──► ir.py ──► codegen.py ──► runtime.py
>                   ▲                       ▲
>                   │                       │
>               jx/compiler.py ─────────────┘
> ```
>
> `jx/*` 只能 import `ir`；`codegen.py` 只能 import `ir` 与（运行期）`runtime`；
> `schema.py` 不 import 任何同级模块。

---

## 1. `dcg/ir.py` —— 冻结的 IR

IR 是 **纯 tuple**（不是 class）：便于比较、可哈希、零构造开销、snapshot 友好。
每个节点的**末元素恒为 `loc`**（`(line, col)` 或 `None`）。

```python
# 字面量
("lit", value, loc)                       # value 为 Python 常量
# 字段访问（Lead 裁决，2026-10-17 —— 以实测为准，本行取代旧措辞）
#   * `$.name` / `$.5.a.1`：编成 ("index", ...) 常量下标链，见 §6.1 / §3.6
#     （**不是** ("field", ...)，实测 jx 行为正确，不得回退）
#   * **裸标识符就是字段**（实测：`a + b` -> ("field","a")/("field","b")）。
#     这是 M0/CAP 工作负载的命脉：真实生产 schema 的 ext 用 `hourA + hourB`、
#     `hour[2] + 123` 引用**本 schema 先声明的字段**。旧措辞"裸标识符一律不是字段"**已废弃**。
#     JSONata 裸名（如 `foo`）在 schema 里不存在时，**必须在 build 期报错**并列出可用字段名，
#     不得静默生成不存在的局部名（否则运行期 NameError）。
("field", name, loc)                      # name: str，不重复前导 "$."；裸名走这里
# 路径访问（逐层，v2 不支持 a.b.c 的 JSONata 深路径，见 §6）
#   拒绝判据（§6.1）：从 root 起，链条中出现 >= 2 个**符号段**即拒绝；
#   `$.5.a.1`（1 个符号段 'a'）允许，`$.5.a.1.b`（'a' 与 'b'）与 `$.a.b` 拒绝。
("index", base, idx, loc)                 # base[idx]，idx 为 IR；
                                          # 常量 -> 下标访问；动态 -> _ix_get（§3.6，
                                          # 禁止路由到 _ix，那是 $indexOf）
# 当前行 / 根
("root", loc)                             # "$"  展开为行对象本身
# JSONata 变量 $v
("var", name, loc)                        # name 不含 "$"
# 一元
("unary", op, operand, loc)               # op in {"-", "+", "not"}
# 二元
("bin", op, left, right, loc)             # op 见下表
# 三元
("cond", test, then, orelse, loc)
# 函数调用
("call", fname, [args...], loc)           # fname 含 "$"（如 "$uppercase"）
# 用户注册函数（Registry.fn 注册）
("rfunc", qualname, [args...], loc)       # qualname 为 "pkg.mod.fn"
# 数组字面量
("array", [items...], loc)
# 对象字面量（保序）
("object", [(key_ir_or_str, val_ir), ...], loc)
# 反引号内联 Python（不透明）
("py", source_str, [free_field_names...], loc)
   # source_str: 原样 Python 表达式
   # free_field_names: 其中出现的字段**取值链**（§3.7），如 "a"、"5.a.1"；不含前导 "$."
   # codegen 负责把每条链替换为对应的生成局部名（见 §4）；链的解析与 JSONata
   # 路径表达式共用同一实现（§3.6 / §3.7）
# py{} 多语句内联 Python 块（不透明；用户明确需求）
("pyblock", stmts_source, [free_field_names...], loc)
   # stmts_source: 编译期已定稿的 Python **语句序列**文本（已做 __jx 前缀重命名，
   #   必须恰好含一个 return；语义上是一个函数体，不是表达式）
   # free_field_names: 与 ("py", ...) 同口径
   # codegen 落地（冻结）：为每个 pyblock 生成命名辅助函数，定义在 run() **之外**：
   #     def _py0(f1, f2, ...): <stmts_source>
   #   调用处以局部名传已解包字段：... _py0(_a1, _a2) ...
   # 禁止用 lambda / IIFE 承载多语句（lambda body 只能是表达式，必然 SyntaxError）；
   # 辅助函数不得每行重建；_pyN 编号按源码出现顺序，禁止 random。
   # build 期必须校验 free_field_names 全部在 schema 已声明字段中，否则 SchemaError
   #   并列出可用字段名（与反引号段同一错误路径）。
# 区间（build 期必须被 policy 拒绝）
("range", lo, hi, loc)
# 高阶函数字面量（build 期必须被 policy 拒绝）
("lambda", [param_names], body, loc)
```

**`bin` 的 `op` 取值（与 JSONata 1.8 一致）**

| op | 含义 | undefined 语义（见 §4.4） |
|---|---|---|
| `"+"` `"-"` `"*"` `"/"` `"%"` | 算术 | 传播 undefined；`null` 参与算术 → **错误** |
| `"&"` | 字符串拼接 | **不传播**，undefined 当空串 |
| `"="` `"!="` | 相等 | 任一侧 undefined → `False` |
| `"<"` `"<="` `">"` `">="` | 大小 | 任一侧 undefined → **undefined** |
| `"and"` `"or"` | 逻辑 | 按 JSONata 真值；undefined 参与运算 |
| `"in"` | 成员 | 与 `"="` 同族 |

---

## 2. `dcg/schema.py` —— 冻结的 schema

```jsonc
{
  "name": "flight",                       # 必填，生成模块前缀；[A-Za-z_][A-Za-z0-9_]*
  "input":  {"source": "index"},          # 可选；"rows"|"index"，默认 "rows"
  "output": {                             # 必填
    "shape":  "list",                     # "list"|"dict"|"index"|"rows"  默认 "list"
    "single": false,                      # bool，默认 false
    "filter": true,                       # bool，默认 true
    "post":   null,                       # null 或 "pkg.mod.fn"
    "trace":  false                       # bool，默认 false
  },
  "out": {                                # 必填，一个 object 节点
    "type": "object",
    "fields": { "<fname>": <FieldSpec>, ... }
  }
}
```

**`FieldSpec` —— 取值三键互斥**

| 键 | 类型 | 生成结果 |
|---|---|---|
| `value` | str，形如 `"$.a.b"` | 直接引用列（**零开销**），支持数值下标 `$.5.a` |
| `const` | 任意 JSON 常量 | 内联常量 |
| `expr`  | str，JSONata + 反引号 | 编译表达式 |

- 三者**恰好出现一个**，否则 `SchemaError`（带 JSON 路径）。
- `"type"` 必填：`"int64" "int32" "float64" "str" "bool" "list" "object" "any"`。
- `"optional": true`（默认 `false`）是**唯一**决定是否生成 undefined 守卫的开关。
- 嵌套对象：`FieldSpec` 里给 `"fields": {...}` 且 `"type": "object"`（**必填**，空 object 报错）。
- 嵌套数组：`"type": "list"` 配 `"items": <FieldSpec>`（**必填**）。

**硬规则（必须实现）**
1. `"a"` 这种**裸字符串永远不是路径**。值要么是 `$` 开头的路径（含数值下标），要么走 `const`；不存在「靠前缀猜语义」的第三态。
2. 嵌套**必须**用 `"fields"` / `"items"` 显式声明。**没有**「list 里出现 dict 就当嵌套」这种特例。
3. 非法的 `struct`/`describe` 形状（例如 `{"字段名": {描述}}` 这种无 `name` 的旧形状）必须报**带 JSON 路径的 `SchemaError`**，而不是裸 `KeyError: 'name'`；若形状疑似旧版，报错里给出迁移指引（`python -m dcg.migrate`）。

**`SchemaError`**：`dcg/schema.py` 里定义，`ValueError` 子类，携带 `.path`（如 `out.fields.sum`）与 `.msg`；`str(e)` 形如 `out.fields.sum: value/const/expr 必须恰好提供一个（给了 2 个）`。

---

## 3. `dcg/jx/` —— JSONata 编译器

### 3.1 公开面（只有这三个）

```python
# dcg/jx/compiler.py
class Unsupported(Exception):
    def __init__(self, feature: str, loc, msg: str): ...
    # str(e) 形如: "第 12 列: $sum 不可逐行编译（需整批数据求值）"

def compile_expr(src: str, *, loc_base=(1, 1)) -> tuple:
    """JSONata 源码 -> §1 的 IR。遇不支持结构抛 Unsupported，带列号。"""

# dcg/jx/registry.py
class Registry:
    def fn(self, name: str, callable) -> None:  # name 形如 "$myfn"
        ...
    def get(self, name: str): ...
```

`compile_expr` 是**纯函数**：同输入同输出，不得读全局 `MODE`。

### 3.1b 一致性基准（冻结，位于 `bench/frozen/conformance.json`）

50 例（core 26 + edge 24）+ 3 条 `refused`。期望值由官方 `jsonata-python 0.7.0` **一次性**求出后固化进仓库（求值脚本不随仓库发布，也不需要复跑）。**任何回归测试都不需要 jsonata 包**（契约 D4/D10）。

```jsonc
{
  "undef_marker": "__undef__",
  "cases": [
    {"group": "core", "expr": "a + b", "ctx": {"a":3,"b":4}, "expect": {"value": 7}},
    {"group": "core", "expr": "miss",  "ctx": {"a":1},
     "expect": {"value": null, "null_or_undefined": true}},
    {"group": "edge", "expr": "null + 1", "ctx": {"a":1}, "expect": {"error": "JException"}}
  ],
  "refused": [{"expr": "$sum(xs)"}, {"expr": "$now()"}, {"expr": "$map(xs, $string)"}]
}
```

- `test_jx_conformance.py` **必须读取该文件**，不得复制粘贴用例（否则会漂移）。
- `expect.value == null` 且有 `null_or_undefined: true` 的 7 例：官方探针无法区分 undefined 与 null
  （二者在 Python 侧都归一为 `None`）→ 断言时做**归一化比较**，不要把二者当不同结果。
- `expect.error` 的 2 例（`null + 1` / `a + null`）是**正确行为**：JSONata 里 null 参与算术是
  **错误**而不是 undefined，实现也必须抛。
- `jsonata` 包**不得出现在运行期依赖里**（`dcg/` 与 `bench/` 均不得 import 它）；仓库里唯一允许的
  出现方式是 `tests/` 里的 `pytest.importorskip("jsonata")` 交叉复核（缺包时跳过，不算失败）。

### 3.2 词法

- `TOKEN` 正则 + `lex(src) -> [(kind, value, col), ...]`
- 必须识别 `` ` `` 为内联 Python 段的**词法边界**（JSONata 从不使用反引号，因此零歧义）。
  段内容原样捕获到下一个**未转义**反引号；段内不解析 JSONata。
- 必须支持：数字（含小数/指数）、字符串（单/双引号）、正则字面量 `/.../flags`、
  `$` / `$$` / `$name`、`$.name`、裸标识符、以及运算符
  `+ - * / % & = != < <= > >= and or in ? : . [ ] ( ) { } , # @ ^ | ~> ..`
- **`lang` 不是词法概念**，schema 层通过 `expr` 是否存在决定；不要引入。

### 3.3 语法（Pratt）

结合力（数字越大越紧）：`or 10` · `and 20` · 比较/`in` 30 · `& 40` · `+ - 50` · `* / % 60`
· 一元 `- + not` 70 · 后缀 `.`（map）`[`（filter/index）`(`（call）90 · `..` 45

- 调用/下标/路径统一在**中缀循环的后缀位置**处理（早期版本只在 prefix 处理函数调用，
  导致 `$not(miss)`、`a[0]` 报"多余内容"）。
- **先判函数归属（ROW_FUNCS / BATCH_ONLY），再 emit 实参**：否则 `$map(xs, $string)`
  会报出误导性的 "$string 需要 ctx 绑定" 而不是 "$map 不可逐行编译"。

### 3.4 逐行可编译函数白名单（`ROW_FUNCS`）

字符串：`$string $uppercase $lowercase $trim $pad $contains $split $join $substring
$substringBefore $substringAfter $replace $length $base64encode $base64decode
$encodeUrlComponent $decodeUrlComponent $formatNumber $formatBase`
数值：`$number $abs $floor $ceil $round $sqrt $power $sum`(仅限字面量数组) `$min $max`(同)
布尔/其他：`$boolean $not $exists $type`
数组（仅限字面量数组）：`$count $append $reverse $sort $distinct`

### 3.5 必须拒绝（`BATCH_ONLY` → `Unsupported`）

需要整批数据：`$sum $max $min $average $sort $reverse $distinct $shuffle $reduce $each
$sift $keys $values $lookup $merge $spread $map $filter $single $position`
非确定：`$now $millis $random $eval`
其他结构：`..` 区间、正则字面量、`function(){}` 高阶字面量、`$` 位置绑定 `#`、join 绑定 `@`、
变换操作符 `~>` / `|...|`、Reduce `{k:v}` 路径阶段、Sort `^`、分组

**一律抛出，绝不静默降级。**

### 3.6 下标（Lead 裁决，2026-10-17）

**背景**：jx 曾把**动态下标**编成 `("call", "$ix", [base, idx], loc)`。这有两处契约问题：

1. `$ix` 不在 §1 的节点表里，也不在 §3.4 白名单里；
2. 运行期 `_ix` 是 **`$indexOf`**（`dcg/runtime.py:311` `def _ix(s, sub): return s.find(...)`），
   真正的下标 helper 是 **`_ix_get`**（`dcg/runtime.py:427`）。
   两者**语义不同却只差一个字符**，是持久命名歧义。

**事实更正（Lead，2026-10-17，勿再引用错误版本）**：Lead 曾据此推断
「`$ix` 会被朴素映射成 `_ix`，使 `a[b]` 静默变成字符串查找」。**该推断不成立**：
实测 `dcg/codegen.py:287-289` / `:329` 把动态下标路由到了 `_ix_get`，**从未**路由到 `_ix`。
因此这**不是**静默错值缺陷，仅**命名歧义 + §1 节点表不完备**。
（本契约保留此更正，防止后续再把它当 bug 传播。）

**裁决（三条，全部强制执行）**

1. **常量下标**（`$.5`、`$.a`、`hour[2]`）继续用 §1 既有的
   `("index", base, ("lit", k, loc), loc)`，codegen 翻译成 Python 下标访问。
   `a[0]`、`hour[2]`、`$.5.a.1` 均属此列，**当前 jx 行为正确，保持不变**。
2. **动态下标**（`base[expr]`，`expr` 非字面量）**禁止**经过 `("call", ...)` 伪装成函数。
   走 §1 既有的 `("index", base, <idx IR>, loc)` —— 它就是"任意 IR 作下标"的节点，
   codegen 对 `idx` 不是 `("lit", ...)` 的情况翻译成下标的**undefined 安全形式**，
   即调用运行期下标 helper `_ix_get`（不得调用 `_ix`）。
   若 codegen 选择为动态下标单独生成内联表达式，则必须与 `_ix_get` 语义逐条一致。
3. **命名防撞**：运行期 `_ix` = `$indexOf`，与"下标"**语义无关**，任何人不得把下标路由到它。
   建议 jx/codegen 侧统一把下标 helper 记作 `$ix_get` 以避免歧义。

**测试要求**：必须有一例动态下标（如 `` `$.5.a[$i]` `` 或 `$.a[$i]`）的端到端测试，
断言取到的是**行内元素**而不是 `-1`/`find` 结果。

### 3.7 反引号段里的字段访问（Lead 裁决，2026-10-17；实现已修）

**背景（历史缺陷，已关闭）**：`analyze_py_segment` 的早期实现只认 `$.ident`，**不认数字段**，
于是下面三例在段内偏移处报 `Unsupported`：

```
analyze_py_segment('$.a + 1')                    -> Unsupported（旧）
analyze_py_segment('$.5.a.1')                    -> Unsupported（旧，段内偏移 1）
analyze_py_segment('str($.5.a.1) + str(hourB)')  -> Unsupported（旧，段内偏移 5）
```

这直接打掉了 M0/CAP 工作负载：真实生产 schema 的 ext 就是
`` `str($.5.a.1) + str(hourB)` `` 这种写法（见 §7 与 `bench/_workload.py`），迁移后必须能跑。
**现状（已修，`dcg/jx/compiler.py::analyze_py_segment`，`:122`）**：

```
analyze_py_segment('$.a + 1')                    -> ('$.a + 1', ['a'])
analyze_py_segment('$.5.a.1')                    -> ('$.5.a.1', ['5.a.1'])
analyze_py_segment('str($.5.a.1) + str(hourB)')  -> (..., ['5.a.1', 'hourB'])
analyze_py_segment("'hourB' + str($.5.a.1)")     -> (..., ['5.a.1'])   # 字符串字面量内的裸名不登记
```

**裁决**

1. 反引号段的字段引用 = **`$.` + 链**，链的每一段是
   标识符（`[A-Za-z_][A-Za-z0-9_]*`）或十进制下标（`[0-9]+`）。
   合法：`$.a`、`$.5`、`$.5.a`、`$.5.a.1`、`$.5.a.1.b`。
   **单段链（`$.data`）也合法**：它是一条取值链，不是裸名（曾把它当裸名报
   `裸名 'data' 不是本次 schema 声明的字段`，已修）。
2. `free_field_names` 的元素是**字段引用文本**，两种形态都合法：
   - `$.`-链的**链文本**（如 `"5.a.1"`，**不含**前导 `$.`）；
   - **裸名**（如 `"hourB"`），即本 schema 已声明字段的局部引用。

   **理由（Lead 复核，2026-10-17）**：CAP 的真实 ext 是
   `str($.5.a.1) + str(hourB)`（`bench/_workload.py`），其中 `hourB` 是**本 schema 先声明的字段**。
   若裸名不进 `free_field_names`，CAP 编不出来。

   因此 §4.3 原先「段内裸名不在 `free_field_names` 中 → `SchemaError`」这句**含义更正为**：
   **`free` 里的裸名若不在 schema 已声明字段集 → `SchemaError` 并列出可用字段名**。
   该检查**只能落在 codegen**：`dcg/jx` 是纯函数、没有 schema（§3.1），无权判定成员资格。
3. codegen 把链解析成 §3.6 的 `("index", ...)` 链（从 `root` 起），
   与 JSONata 路径表达式**共用同一套解析与同一个解析结果**。
   因此反引号段里的 `$.5.a.1` 与 JSONata 的 `$.5.a.1` 编译成**同一段代码**，不得两套实现。
4. `_PY_FIELD.replace` 之后仍必须是单个 Python 表达式，
   `ast.parse(..., mode="eval")` 必须通过。

**最小测试**：`` `str($.5.a.1) + str(hourB)` `` 必须编译成功且端到端结果与手写参照等价；
`$.a.b`（两层标识符符号路径）仍按 §6.1 拒绝。

---

## 4. `dcg/codegen.py` —— 生成器

### 4.1 公开面

```python
def generate(schema: dict, *, cython: bool = False, inline_optional: bool = True) -> str:
    """schema -> 完整 Python 模块源码（可写盘、可读）。确定性：同 schema 同输出。"""

def build(schema: dict, out_dir: str, *, name: str | None = None,
          overwrite: bool = False) -> dict:
    """写 gen/<name>.py + manifest；返回 manifest dict（含 sha1、行数、字符数、产物路径）。"""

def gen_name(schema: dict) -> str:
    """确定性模块名 = schema.name（人类可读）；禁止 random。sha1 记入 manifest 做校验。"""
```

**命名裁决（Lead，冲突项 1）**
- **文件名 = `gen/<schema.name>.py`**，优先人类可读与可 diff（契约 D3）。
- **确定性由 sha1 校验保证**，不是靠随机名：
  - manifest 记录 `schema_sha1`（对 canonical JSON 序列化取 sha1）
  - build 时若 `gen/<name>.py` 已存在**且内容与本次生成不同**：
    - `overwrite=False`（默认）→ 报错 `SchemaError`，提示 schema 已变更
    - `overwrite=True` → 覆盖
  - 目的：**同一 name 下 schema 变更不会被静默陈旧化**。
- **来源自证**：`schema_sha1` 记在 manifest 里（产物正文不写 docstring，见 §8 O-5）。

**输出形状裁决（Lead，冲突项 2）**

| `shape` | `run(rows)` 返回 | 说明 |
|---|---|---|
| `"list"`（默认） | `list` of row | 每行是一个 list（或 `single` 时展平为该行本身） |
| `"dict"` | `dict{字段名: [每行值]}` | **列式**；禁止运行期物化列式 dict（`tuple(doc.items())` 式写法） |
| `"index"` | `dict{行下标: [行值]}` | 行下标为 int；禁止 `itertools.chain` 式运行期拼接 |
| `"rows"` | `list` of `dict{字段名: 值}` | **行式**；键序 = 字段声明序。等价于旧版 `protocol: False`（2026-10-17 为迁移对等新增） |

`output.single=true` 时，`list`/`rows` 形状直接返回**单条结果**（`list` 为该行本身，
`rows` 为该行的 dict）而非整表；`dict`/`index` 形状下 `single` 无意义（schema 校验期报错）。

### 4.2 生成码的形状（唯一形态，无 compact 变体）

```python
# gen/<name>.py —— 自动生成，请勿手改（CAP 负载实测产物，149 字符）

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

- 产物**只在真的用到 runtime helper 时**才在第一行发 `from dcg.runtime import *`
  （CAP/M0 全部内联成裸运算，连一行 import 都不需要）。
- `py{}` 多语句块的辅助函数（`def _pyN(...)`）定义在 `run()` **之外**，每块只发一次。

**硬规则（每条都有对应的 snapshot 测试）**
1. **全局行首解包**：同一 schema 里所有被引用的 `$.x` / `$.5.a` 前缀，在行首**只取一次**。
   这是 M0 实测唯一既缩短代码又提性能的改动（1.33×）。
2. **零恒等语句**：不得生成 `datas = datas`、`x = None` 之类的桩。
   旧版每次 parse 会多编译一份 118 字符空桩，v2 禁止。
3. **零只用于 return 的中转**：不得生成 `_buf = {'doc': []}; _doc = _buf['doc']` 这类中间层，
   也不得生成 `_ap = _o.append` 式别名（CPython 3.13 实测收益为 0，见 §7.1）。
4. **输出形状编译期固定**：`shape=list` 直接构造 `list`；
   `shape=dict` 生成 `dict` 字面量（**禁止**运行期物化）；
   `shape=index` 直接构造并避免 `itertools.chain`；
   `shape=rows` 在**尾部**包一层行 dict 推导式（行字面量仍是 list —— 不改 `body()`，
   因为 `_unbind_trivial` 会把 dict 键引号内的字段名一起替换坏）。
5. **行级 `try/except` 移出循环**。
6. **类型约束编译期分支**：`type` 是**声明**，不是**转换**（见下方裁决）。
   **不得**生成 isinstance 分派链。
7. **`optional: true` 才生成守卫**：每个 optional 字段**恰好一次** undefined 判断，
   内联形如 `X=None if _t is _U else _t`；必然存在的字段零判断。
8. **变量名确定性**：`_` + 字段名的可读短名（冲突时加数字后缀），由字段**声明顺序**决定。
   禁止 `random`。
9. **Cython 模式**：同源加类型标注，`cdef object` 声明局部量；不得改变语义
   （形态恒等于「纯 Python 产物 + 每局部一行 `cdef object`」）。

**`type` 语义裁决（Lead，冲突项 3）**

`type` 是**声明（assertion）**，不是**转换（coercion）**：只用于类型标注与校验，
**不插入运行时转换**，值由输入数据直通。

| 角色 | 行为 | 本次选择 |
|---|---|---|
| 声明/断言 | 只用于类型标注（Cython `cdef`）与校验；**不插入运行时转换** | ✅ **采用** |
| 转换/强制 | 无条件插入 `int(...)`/`float(...)` | ❌ 不采用（改变语义且增成本） |

- `Cython` 模式下，`type` 用于 `cdef` 声明，这是它的主要价值。
- 若某字段**需要**转换，在 `expr` 里显式写 `` `int($.x)` `` —— 转换是表达式层的事。
- 未来的 `"coerce": true` 选项（无条件转换）**不在本次范围**，需要时单独立项。
- 理由：无条件插 `int()` 会对已是 int 的输入产生额外开销（实测每次函数调用 +50ns），
  属于 §7 的 5% 预算里不该浪费的预算。

**产物字符数目标（Lead，冲突项 3 连带裁决）**

CLI 与 §4.2 的最大明文源码只对**指定的工作负载 schema** 有意义，因此把口径钉成两个具体负载，
不设笼统上限（两个负载都由 `bench/_workload.py` 定义，验收门与基准测试共享同一批对象）：

| 工作负载 | 定义 | 旧版实测字符数 | v2 上限 | v2 实跑 |
|---|---|---|---|---|
| **CAP** | 4 字段：`$.5.a.1` / `$.5.a.2` / `ext str($.5.a.1)+str(hourB)` / `ext hourA+hourB`，`filter=true` | 445 | **≤180** | **149** |
| **M0** | 8 字段：`$.5.a` / `$.5.a.1` / `$.5.a.2` / `$.2` + 3 个 ext + 动态下标 | 537 | **≤210** | **195** |

（旧版实测：两者都是 `compile()` **调用两次** —— 一次 118/117 字符的空桩，一次真正的产物。
v2 必须只编译一次且无空桩。）

**真实目标不是字符数，而是「零冗余」六条**（字符数只是代理指标）：
1. 每个被引用的下标前缀在行首只取一次（不重复 `R[5]['a']`）
2. 零恒等赋值、零为 return 而生的中转变量
3. 无 isinstance 分派链
4. 无冗余类型转换（见上）
5. 输出形状编译期固定，无运行期判断
6. 每次 `build` 只 `compile` 一次，无空桩

`tests/test_codegen_snapshot.py` 必须对**六条零冗余**逐条断言；字符数上限作为**附加**断言
（CAP ≤180、M0 ≤210），不要反向用字符数当主判定。

### 4.3 与 jx 的衔接

`schema.out.fields[f].expr` → `jx.compile_expr(src)` → IR → codegen 的
`emit(node) -> str` 返回 **Python 表达式源码**（内联形式，如 `_ad(_a, _ml(_b, 2))`）。

- 默认 `inline_optional=True`：`optional` 字段的守卫**内联**进表达式。
- `"py"` 节点：`source` 原样嵌入，但需先校验 `ast.parse(source, mode="eval")` 通过
  （build 期），并把 `free_field_names` 映射为生成局部名。**每个 `("field", name, loc)`
  都要查 schema 字段集**：未声明即 `SchemaError` 并列出可用字段名（防止 `int`/`math`
  与字段名静默撞车；§3.7 裁决 2）。名单元素含**链文本与裸名**两种形态，见 §3.7。
- `"pyblock"` 节点：生成 `<stmts_source>` 的**命名辅助函数** `def _pyN(f1, f2, ...)`，定义在
  `run()` 之外，调用处传已解包字段；形状与禁止事项见 §1。`ast.parse(stmts_source, mode="exec")`
  在 build 期校验，且必须恰好含一个 `return`。**禁止** lambda/IIFE（lambda body 只能是
  表达式，多语句必然 `SyntaxError` —— 已实测）。
- `"rfunc"` 节点 → 生成 `Registry` 查表调用，或按 `qualname` 直接 import。

#### 4.3.1 编译期内联（Lead 裁决，2026-10-17，依据「`type` 是声明不是转换」）

**运行期 KPI 优先**（用户约束：不在乎编译期、只关心运行期）。codegen 在 build 期**可证安全时**
必须内联，不得一律走 helper 调用：

| 字段/节点情形 | 产物形态 | 理由 |
|---|---|---|
| `optional` 非真 且 `type ∈ {int64,int32,float64,str,bool}` | **裸运算/内建**：`a+b`、`str(x)` | 类型是声明 ⇒ 值不可能 undefined、必是数值；无需 `is _U`、无需 `_ad`/`_S` |
| `optional: true` | **内联守卫**：`X=None if _t is _U else _t` | 每个 optional 字段恰好一个 undefined 判断（rule7 计数口径） |
| `type == "any"` 或类型无法静态判定 | helper 调用（`_ad`/`_S`/…） | 不确定时的语义安全网 |

实测依据（历史探针，脚本已随旧版实现一并移除；结论由 `bench/bench_row.py` 与 `evidence/accept.py`
的 §5 门持续复验）：CAP 负载 200k 行、3 次最小值、四变体输出逐字相等 ——
手写参照 0.3873 µs/行 ｜ helper 形态 0.7154（**1.85x**）｜ 内联裸运算 **0.2622（0.68x，比手写快 32%）**
｜ 内联 + 一个 optional 守卫 0.4523（1.17x）。目标：v2/hand ≤ **1.05x**。

**边界**：内联只改*产物形态*，不改变 §4.4 的 undefined 六条语义 —— 语义只在"值真可能 undefined"
的地方花钱。`cython=True` 的产物（每局部一行 `cdef object`）**不计入** §4.2 的 CAP/M0 字符预算；
该形态必须恒等于「纯 Python 产物 + 每局部一行 `cdef object`」，并有测试固定。

**与 §1/§4.4 的关系（避免读成矛盾）**：上表对 `("bin", ...)` 节点同样生效 —— 当两侧操作数
都静态可证安全时，`("bin","+",a,b)` 直接发 `a+b` 而**不**发 `_ad(a,b)`；`("index", base, ("lit",k))`
在 base 声明为 `list` 时直接发 `base[k]` 而**不**发 `_ix_get(base,k)`（`_ix_get` 保留给动态下标
与 `a[0]==a` 标量语义）。§4.4 的"每个运算符一个专用函数"约束的是 **helper 实现方式**
（不许 dict-of-lambda 派发），不是"每个运算符都必须走一次函数调用"。helper 名以
`dcg/runtime.py` 的 `SOURCE`/`HELPERS` 实际键名为准（`_ad`/`_S`/`_ml` 等；旧文里的 `_add` 是示意）。

### 4.4 `dcg/runtime.py`

undefined 语义 helper 的**唯一实现处**，同时是生成码的内联来源（`HELPERS` / `SOURCE` 各 48 项，
`SOURCE` 由源码派生，不再手抄）。

```python
class _Undef: __slots__ = ()
UNDEF = _Undef()                    # 单例，身份比较；产物里短名 `_U = UNDEF`
def is_u(v) -> bool: return v is UNDEF
# 每个运算符一个专用函数（不分派表！M0 实测派发脚手架占守卫成本 89%）
def _ad(a, b): ...
def _cc(a, b): ...                  # undefined 当空串
def _eq(a, b): ...                  # 任一侧 undefined -> False
def _lt(a, b): ...                  # 任一侧 undefined -> UNDEF
def _and(a, b): ...  _or(a, b): ...  _not(a): ...
# 行函数
def _S(s): ...  _up  _lo  _tr  _num  _bool  _ab  _fl  _ce  _rd
def _ln  _ct  _sp  _jn  _ss  _ix  _ar  _ob
```

产物通过 `from dcg.runtime import *` 拿到 helper 与**同一个** `UNDEF` 单例（`__all__` 56 个名字，
含全部下划线 helper 与 `_U`）；`SOURCE_NEEDS_MATH`（`_fl`/`_ce`/`_sq`）用到才生成 `import math as _m`。

**undefined 六条语义（M0 前已用官方实现逐例校验，必须逐条保留）**
1. `&` 的 undefined **不传播**，当空串：`'x' & miss == 'x'`
2. `or` 的 undefined **参与运算**：`miss or true == True`
3. `=`/`!=` 遇 undefined 返回 `False`：`miss = miss == False`
4. 大小比较遇 undefined 返回 **undefined**：`miss < 5 == undefined`
5. 数组字面量**跳过** undefined：`[miss, a] == [1]`
6. 对象字面量**丢弃** undefined 键：`{'k': miss} == {}`
7. 额外：`null` 参与算术是**错误**（不是 undefined）；`$not(miss) == undefined`；
   `a[0]` 对标量也成立（`a[0] == a`）

---

## 5. `dcg/__init__.py` 公开面

```python
from .schema import validate, SchemaError
from .codegen import generate, build, gen_name
from .jx import compile_expr, Unsupported, Registry

__all__ = ["validate", "SchemaError", "generate", "build", "gen_name",
           "compile_expr", "Unsupported", "Registry"]
```

`dcg/jx/__init__.py` 必须再导出 `compile_expr, Unsupported, Registry`。

**入口模块（不属于运行期公开面）**：`dcg/build.py` 是 `python -m dcg.build` 的入口（只转发到
`codegen._main`，保证 `dcg/` 里没有第二份生成逻辑）；`dcg/migrate.py` 是**旧版 describe → v2
schema** 的迁移工具（`python -m dcg.migrate <旧版导出文件> -o out.json [--report r.md]`），
供生产迁移一次性使用，不参与运行期。

---

## 6. 明确的能力边界（写进 README，且要有测试）

1. **深路径 `a.b.c` 的 JSONata 逐行走访不支持**。判据（Lead 裁决 2026-10-17，**已由
   `compile_expr` 强制执行**）：从 `root` 起，链条中出现 **≥2 个符号段**即拒绝。
   - 允许：`$.name`（1 个符号段）、`$.5.a.1`（符号段只有 `a`）、`$.5`（纯数值）
   - 拒绝：`$.a.b`、`$.5.a.1.b`（≥2 个符号段）
   多层结构靠 `"fields"` 嵌套表达。**注意区分数值段与符号段**：`$.5.a` 里的 `5` 是数值下标，
   不计入符号段。实测报错：`深路径 [a,b] 不可逐行编译（v2 不支持 JSONata 逐行走访（契约 §6.1），请用嵌套 fields）`。
2. **裸标识符 = 字段**（见 §1 裁决）。`hourA + hourB` 合法，引用本 schema 已声明的字段；
   schema 中不存在的裸名必须在 **build 期**报错并列出可用字段名，不得拖到运行期 `NameError`。
3. **整批语义一律拒绝**（§3.5）。
4. **反引号 = 任意 Python 执行，不是沙箱**。build 期不执行代码，只 `ast.parse` 校验。
5. **形状不可运行期切换**：要 list 与 dict 两种输出就 build 两个产物。

---

## 7. 验收基准方法学（钉死，禁止改动）

- 同机、同数据、同进程；`gc.collect()` 后计时。
- 主指标：`µs/行`、`行/秒`；必须报**比值**（产物 / 手写参照），绝对值只作参考。
  （历史的三方对照含旧版引擎；旧版已从仓库删除，现为二方对照。）
- 基准数据形状与 M0 一致：200k 行 × 8 字段，行形状
  `(code:str, a:int, b:int, c:int, d:int, payload:{'a':[4]})`。
- 目标（M0 派生，非承诺）：
  - 产物字符数：CAP **≤180**、M0 **≤210**（量**整模块**，与本文件 §4.2 同口径）
  - **主判定是比值**（v2/hand ≤ **1.05x**），不是绝对值：跨会话绝对值漂移实测 +22%，
    未 settle 首轮有 50% 顺序伪影；禁止跨会话绝对值相减。
  - 静态检查（九条规则、字符上限）**只是形态回归门**：实测曾出现全绿而产物是错的
    （行被摊平 + optional 守卫反向）⇒ **行为 e2e 才是主判定**。

### §7.1 测量方法学（2026-10-17 推翻并重建，Lead 裁决）

**背景：wall-clock 在本机没有 5% 分辨率。** 实测标定（Lead，历史修订 `A40457511277`）：把
**同一个函数**（逐字相同的 `ref_tight` 形态）在交替 slot 中测 15 轮 —— 单变体自身 spread
**39.3% / 67.7%**（复跑 82.8% / 97.0%），两个 slot 算出的"比值"落在 **0.974（median）/ 1.020（min）
/ 1.168（max）**。原因：本机在任何时刻都有 5–9 个并发 python 进程（多个 agent 并行写代码），
抢占瞬态压过效应量。⇒ **`1.05x` 这个 5% 阈值在 wall-clock 口径下不可判定。**
历史记录中所有 wall-clock 的 `v2/hand`（1.065 / 1.117 / 1.169 与 0.901 / 0.937 / 1.000）
**互相矛盾且不可引用**；W2 窗口（`evidence/row_v2.json`）保留为**已发生事实的历史证据**，
不作为终值。

**主口径（新）：CPU 时间。** `time.process_time()` 只计本进程消耗，不受其他 agent 抢占影响。
- 每次测量内跑 **K 遍**（K=8；摊销 Windows `process_time` ~15.6 ms 粒度：200k 行 × ~0.7 µs
  ≈ 0.14 s/遍 ⇒ K=4 量化 ~2.8%、K=8 ~1.4%）；
- 各变体 **interleaved** 交替测量（不是分块顺序测）；
- 取 **min of reps（reps=7）**；**同时报各变体自身 reps 的 spread**（min-of-N 系统性低估涨落）；
- 唯一实现在 `bench/bench_row.py::measure_cpu` / `measure_interleaved_cpu`，**禁止第二份实现**；
- JSON 必须记 `k` 与 `reps`（可复现性优先）。
- ⇒ **静默窗口要求作废**：CPU 口径天然免疫并发抢占。（仍要求测量前后各取一次 sha1 并断言
  `revision_stable_during_run`。）

**噪声底标定（强制，每轮必跑）。** 把**同一个 callable 测两次**（A/A），用与正式测量相同的
interleaved slot 顺序，跑 **≥3 个 block**，报**最差** `|ratio-1|`；JSON 存 `noise_floor_blocks`。

**判决规则（取代"取更保守一方"）：**
```
margin = |ratio - limit|            # limit = 1.05
if blocks_disagree_in_sign     -> verdict = INCONCLUSIVE   # ratio 在 limit 两侧来回跨
elif margin < noise_floor      -> verdict = PASS/FAIL, confidence = "WITHIN_NOISE"
else                           -> verdict = PASS/FAIL, confidence = "RESOLVED"
```
`noise_floor` 是 A/A 实测的**分辨率**（首轮标定 0.0506、同变体自身 spread 28.2%；
当前复跑 **0.0114**，见 `evidence/noise_floor.json`），它**不是**一道全局否决门：只有当
**待判定的 margin 本身小于该分辨率**时才降级为 `WITHIN_NOISE`。（Lead 初版规则写成
`noise_floor > 0.05 → INCONCLUSIVE`，会让一个分辨率 ~5% 的 harness 永远无法判定一个 5% 的目标
—— 已修正。）
`EFFICIENCY_RATIO` 行加 `confidence=`；JSON 加 `confidence` 与 `margin_over_limit`。
**`evidence/accept.py` 的 §5 门按 `verdict` 判**（机器接口保持二元），但 `confidence` 为
`WITHIN_NOISE` / `INCONCLUSIVE` 时**判 FAIL 并在 reason 中原样引用 `confidence` 与 `margin`**：
`ACCEPTABLE_SKIPS` 只留给"harness 不存在"这类真缺席，**"测了但分不清"不算通过**。

**效应量报阈。** 只报 ≥3% 的差异。Lead 的 2×2 消融（CPU 口径，K=4、3 block、200k 行）：
base 0.6641 µs/行 ｜ **`_ap=_o.append` 预绑定 0.6641（效应精确为 0）** ｜ `if R:` 守卫 0.6836（+2.9%）。
⇒ **裁决 R2：`_ap` 不落地**（旧版产物确有 `doc_…_append = doc_….append`，但 CPython 3.13 的
`LOAD_METHOD → CALL_METHOD` 特化解释器使其无收益；且会命中 `bench/codegen_rules.py:191-199`
与 `tests/test_codegen_snapshot.py:202` 两处规则③形态判据）。规则③保留形态判据（不发射 prebind
即无误伤）；其**本意**是按**引用次数**判：别名仅在赋值后被读 ≤1 次并转发 ⇒ 违例；被调用或返回 ⇒ 合法。
**Lead 曾据 wall-clock 声称预绑定回收 5.5%，该结论已撤回。**

**另一条被撤回的结论：输出列表预分配。** 早期单字段口径的「E 预分配 1.06×」**不可外推**到多字段行。
真实 8 字段形态实测：`append`（预绑定）**0.4459 µs/行** ｜ `预分配+enumerate`
0.4688（1.051×）｜ `预分配+range` 0.4755（1.066×）⇒ **预分配更慢**，三者输出逐字节相同。
`_o+=[...]` 比 `append` 慢约 1.5×（建临时 list + `INPLACE_ADD`），且它正是 P0/D1 缺陷的根因。

**复现证据**：`bench/probe_noise_floor.py`（三段：A/A 噪声底标定 / 2×2 消融 /
真产物 vs 冻结手写参照），产出 `evidence/noise_floor.json`。用法
`python -X utf8 bench/probe_noise_floor.py --n 200000 --k 8 --reps 7 --blocks 3`。

---

## §8 已知落差（冻结时点 `dcg/codegen.py A4A3D4794457`，2026-10-17，Lead 登记）

本节如实登记**契约与实现的差异**。冻结期内不修（避免在验证进行中改动被测量对象），
按严重度排序。**O-1 / O-1b / O-2 / O-7 四条由 Lead 与独立验证者各自复现**
（Lead：`bench/probe_known_gaps.py`；独立验证：`bench/probe_contract8_gaps.py` →
`evidence/v2_contract8_gaps.txt`、`bench/probe_schema_error_paths.py` →
`evidence/v2_schema_error_paths.txt`、`bench/probe_deferred_errors.py` →
`evidence/v2_deferred_errors.txt`）。**O-1 与 O-7 属"静默"类（错值/放行不报）**，本项目对这类
缺陷最不能容忍，但**本轮已知情、已文档化**（`README.md` §9 逐条对应）。

| # | 症状 | 严重度 | 出处 | 现状 |
|---|---|---|---|---|
| O-1 | **`value` 路径里的空段被静默吞掉**：`"$..a"`/`"$.a..b"`/`"$.a."`/`"$...a"` 全部被接受并等价于 `"$.a"` / `"$.a.b"` | 🔴 静默错值 | `dcg/codegen.py` 的 `value` 路径解析（与 `dcg/jx/paths.py` 是两套实现） | 契约 §3.5 把 `..` 列为**结构拒绝**。**Lead 2026-10-17 实测（复现脚本原文）**：`value="$..a"` → `_o.append([R['a']])`，实跑 `run([{'a': 7}]) → [[7]]`；`value="$.a..b"` → `R['a']['b']`；`value="$.a."` → `R['a']`；`value="$...a"` → `R['a']`。唯一被正确拒绝的是 `"$.*"`（`SchemaError: out.fields.x.value: value 路径非法: '$.*'（剩余 '*'）`）。⇒ **路径里打错字不会报错，只会静默取到别处的值**。**修法**：`value` 路径解析遇空段（`..`、尾随 `.`）即 `SchemaError`。 |
| O-1b | **`expr` 侧的 `$..a` 被拒绝，但消息虚构了一个源文本里不存在的区间；且 `validate()` 放行** | 🟡 可诊断性 | `dcg/jx/policy.py:118-119`、`dcg/schema.py` | **两个入口消息一致、列号存在**：`compile_expr('$..a')` 与 `generate(expr='$..a')` 都返回 `Unsupported: 第 2 列: 区间 a..b 不可逐行编译（需要序列语义）`。问题在于消息里那个 `a..b` 是 `policy.py:119` 的 `"range": ("区间 a..b", "需要序列语义")` **固定标签**，而用户源文本是 `$..a`——**读者会以为自己写错了区间表达式**。另：`validate()` 对含 `$..a` 的 schema **不报错**，拒绝推迟到 `generate`（并进 O-7）。**不改也不影响正确性**（它是拒绝，不是错值）。 |
| O-7 | **`validate()` 不是完整闸门：整批函数与反引号语法错能通过 validate，行为推迟到 generate** | 🟠 静默放行 | `dcg/schema.py`（validate 偏松）vs `dcg/jx/policy.py`（编译期才严） | `validate()` 通过但 `generate()` 抛 `Unsupported`：`$sum(...)`（整批函数）、反引号语法错 `` `1 + ``。⇒ 用户拿到 "validate OK" 后会以为 schema 合法，实际在 `generate` 才炸。**修法**：把整批函数/反引号语法预检挪进 `validate()`。（原先的 `{"type":"list"}` 缺 `items`、`{"type":"object"}` 缺 `fields` 两条**已修**：`items`/`fields` 现为必填并报 `SchemaError`。） |
| O-2 | **`post` / `trace` 被 schema 接受但产物零代码；`post` 的校验远弱于其报错消息** | 🟠 静默无效 | `dcg/schema.py` 收键、`dcg/codegen.py` 未发射 | 实测：`bytes(带 post) == bytes(不带 post)` **True**（74 字符对 74 字符）；`trace=True` 同理。`post` 的校验**只做「str 或 None」**：`'$.a + 1'`、`` '`$.a`' ``、`'$sum($.a)'`、`None` **全部放行**，只有 `123` 被拒且消息写 `必须是 "pkg.mod.fn" 形式的字符串`——**消息夸大了实际校验强度**。迁移器仍记 `mapped`（否则静默丢语义），README §4.3 注明"mapped 但未接线"。 |
| O-3 | **`items` 生成单元素数组、不映射行数据** | 🟡 语义落差 | `dcg/schema.py::_norm_field`（`:155-160`）、`dcg/codegen.py` `_ar()` | 契约 §2 措辞像"嵌套数组"。实际 `{"type":"list","items":{"type":"int64","value":"$.0"}}` → `_ar(R[0])`，每行产一个单元素数组。`items` 无取值键时报 `value/const/expr…一个都没给`。 |
| O-4 | **`$.a.b` 深路径：`expr` 侧拒绝、`value` 侧任意深度接受** | 🟡 口径落差 | `dcg/jx/compiler.py`（expr）/ `dcg/codegen.py`（value） | `value: "$.a.b.c"` → `R['a']['b']['c']`（要求行是 dict）。契约 §6.1 针对的是**表达式层**（`expr`/反引号），`value` 侧无此限制，且这是**已文档化的能力**（README §3.3 的 `` `$.a.b.c.d` `` → `R['a']['b']['c']['d']`）。**但数据平铺时会运行期崩**：`run([{'a': 7}])` → `TypeError: 'int' object is not subscriptable`。README §9 的 O-4 登记。 |
| O-5 | **产物正文不写 `schema_sha1`（契约 §4.1 要求「docstring 必须记录」）** | 🟡 接口落差 | `dcg/codegen.py::module`（`:780`，不发 docstring）；`build()` 的 manifest 记 sha1 | 实跑产物以 `def run(_d):` 开头，无 docstring、无 `sha1` 字样（`"sha1" in generate(...) == False`）。**来源自证靠 manifest**：`manifest["schema_sha1"]` + 同名不同内容的 `overwrite=False` 拒绝。若要求产物自证，需要在字符预算与自证之间再裁一次。 |
| O-8 | **没有 `shape="document"`：一篇「异构多段文档」不能由**一个** v2 schema 表达** | 🟡 迁移形态落差 | `dcg/schema.py:19`（`SHAPES` 只有 list/dict/index/rows）、`dcg/codegen.py` `finish()` | 旧版 `struct` 可以是一篇多段文档（行式列表 + 逐行标量列表 + 顶层常量 + 嵌套子文档混在一个 dict 里），v2 一次只发一个行列表。**结果仍然可以一致**，但迁移形态是「多个 v2 产物 + 一层组装」，由调用方按段落拼装（逐段对等曾由 `tests/test_v1_ex2_document.py` 固定；该测试与组装脚本已随旧版一起从仓库清理，可从 git 历史 `4d688a7` 取回）。⇒ **可以迁移，但不能声称 v2 单个 schema 覆盖该能力**；这也是迁移报告里 `manual` 项的成因之一。 |

**已关闭项（保留登记以便追溯）**：原 O-5（对等样例文档过期）随旧版样例文档一起删除，样例与
断言已并入 `tests/test_e2e_flight.py` 与 `examples/`；**编号 O-5 现指「产物正文不写 `schema_sha1`」**
（见上表）。O-6（文档状态快照过期）随本次 `DESIGN.md` 重写关闭。

**另记两条已修但值得留档的历史**（均属"静态全绿而行为错"）：
(a) 产物哨兵曾编成字面量 `_U=0`，CPython 小整数缓存使合法值 `0` 被判成 undefined（静默错值）；
(b) `{"type":"list","items":{...}}` 曾直接崩 `TypeError: string indices must be integers`，
且**全仓无测试覆盖**所以长期全绿。这两条是 `evidence/accept.py` 扩成"真 exec 产物跑数据 + 独立
oracle"的直接原因，也是本文件 §7 末尾"静态检查只是形态回归门"那条纪律的来源。
