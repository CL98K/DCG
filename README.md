# DCG v2

**一份 JSON schema，换一个定制 Python 模块。**

DCG（Data Config Generator）把一个声明式的 JSON schema 编译成一个只能干这件事的
Python 模块 `gen/<name>.py`，入口是 `run(rows)`。

- **冻结契约**：[CONTRACT.md](CONTRACT.md)（schema 规格、表达式语义、公开 API、能力边界）
- **设计与取舍**：[DESIGN.md](DESIGN.md)（11 章，D1–D10 决策与 v1 缺陷复盘）
- **性能证据**：[bench/bench_compare.md](bench/bench_compare.md)、[bench/bench_examples.md](bench/bench_examples.md)、`evidence/`
- **v1 逐例对照**：[docs/examples_v1_to_v2.md](docs/examples_v1_to_v2.md)

本文里**每一个代码块都实际跑过**，输出是原样粘贴的。可执行副本见
[examples/readme_blocks.py](examples/readme_blocks.py)（`python -X utf8 examples/readme_blocks.py`）。

> 状态：v2 是 v1（`dcg.py` + `format.py`）的重写，**不保证向下兼容**（D1）。
> 当前引擎修订：`dcg/codegen.py` sha1 `433E4D35A1DB`、`dcg/ir.py` `1C9F537A400D`、
> `dcg/runtime.py` `CD760A6FBA57`、`dcg/schema.py` `D186F1663A58`、`dcg/jx/compiler.py` `9C5B2C8128A0`。
> **注意**：§6 / §9 里 W3 性能终值与 `evidence/row_v2.json` 锚的是**更早的修订
> `dcg/codegen.py A40457511277` / `dcg/schema.py F4CE36DA9C9A`**；其后为「六例结果对等」
> 改了 `dcg/codegen.py`（`pyseg` 单段链）与 `dcg/schema.py`（新增 `shape="rows"`），
> 两者只**增能力**、未改既有产物的字节（`pytest tests/` 的产物快照全绿可证）。

---

## 0. 定位：它是什么，不是什么

| 是 | 不是 |
| --- | --- |
| 编译期生成**行式**转换代码（schema 即输出契约） | 通用 ETL / 查询引擎 |
| 产物**零第三方依赖**（只 `from dcg.runtime import *`） | 运行期可配置的解释器（没有 MODE 开关） |
| 同 schema 两次 `generate()` **字节相同** | 沙箱：反引号内联段是任意 Python 执行 |
| 输出形态、变量名、助手函数在编译期全部固定 | JSONata 全量实现（只支持逐行子集，整批语义一律拒绝） |
| 反引号 `\`...\`` 是唯一的扩展点（D5/D6） | v1 的 `ext` / `_field` / `Template` 机制（D5/D7 已删除） |

v2 相对 v1 的三个结构性变化：

1. **`$`/`#` 前缀闸门删除**：`"a"` 永远是常量字符串，路径必须写 `"$.a"`（v1 靠前缀决定"值还是路径"）。
2. **嵌套必须显式 `fields`**：v1 的"list 里出现 dict 就当嵌套"特例删除。
3. **输出形态编译期定型**：v1 在运行期 `tuple(doc.items())` / `itertools.chain` 决定形状；v2 由 `output.shape` 在生成期决定，且**不可运行期切换**。

---

## 1. 快速开始

```python
import dcg

schema = {
    "name": "order",
    "output": {"shape": "list"},
    "out": {"type": "object", "fields": {
        "sku":    {"type": "str",     "value": "$.0"},
        "qty":    {"type": "int64",   "value": "$.1"},
        "price":  {"type": "float64", "value": "$.2"},
        "amount": {"type": "float64", "expr": "qty * price"},
    }},
}

manifest = dcg.build(schema, "build", overwrite=True)   # 写 build/gen/order.py
code = dcg.generate(schema)                             # 产物源码
ns = {}
exec(compile(code, "<gen>", "exec"), ns)                # 编译期只 compile 一次
print(ns["run"]([["A-1", 2, 3.5], ["B-2", 1, 10.0]]))   # 跑数据
print(manifest)
```

实跑输出：

```text
def run(_d):
 _o=[]
 for R in _d:
  if R:
   qty=R[1]
   price=R[2]
   _o.append([R[0],qty,price,(qty*price)])
 return _o

[['A-1', 2, 3.5, 7.0], ['B-2', 1, 10.0, 10.0]]
manifest: {'name': 'order', 'schema_sha1': 'e3cea33b94d60bbce46fb45feab2a38d9a29ee79', 'artifact': 'build\\gen\\order.py', 'lines': 8, 'chars': 122, 'cython': False}
```

命令行等价物（`-m dcg.build`；加 `--stdout` 只打印源码、不落盘；`-m dcg.codegen` 是同一个入口，
但会带一条 `runpy` 警告）：

```bash
$ python -X utf8 -m dcg.build examples/flight.json -o build_tmp
name: flight
schema_sha1: d3854bda78f963a4499caebab984759c2716a6b4
artifact: build_tmp\gen\flight.py
lines: 9
chars: 203
```

`build()` 返回的 manifest 键：`name` / `schema_sha1`（规范化 schema 的 sha1）/ `artifact` / `lines` / `chars` / `cython`。
目标文件已存在且内容不同时，必须显式 `overwrite=True`，否则 `SchemaError`（防静默覆盖）。

---

## 2. schema 规格

完整规格见 [CONTRACT.md §2](CONTRACT.md)。顶层结构：

```json
{
  "name": "flight",
  "input":  {"source": "index"},
  "output": {"shape": "list", "single": false, "filter": false, "post": null, "trace": false},
  "out":    {"type": "object", "fields": {"...": {"type": "...", "value": "..."}}}
}
```

| 顶层键 | 必填 | 说明 |
| --- | --- | --- |
| `name` | ✅ | `[A-Za-z_][A-Za-z0-9_]*`，决定产物名 `gen/<name>.py` |
| `input.source` | | `"rows"`（默认，`_d` 就是行序列）/ `"index"`（产物第一行 `_d=_d['index']` 解包一层） |
| `output` | ✅ | 缺了直接报 `SchemaError: output: 缺少必填键`；`{}` 合法并被规范化 |
| `out` | ✅ | 必须是 `{"type":"object","fields":{...}}`，字段名同样是标识符 |

`output` 的规范化结果（`dcg.validate()` 会把 schema 转成这个形状）：

```text
output:{} 规范化后:        {'shape': 'list', 'single': False, 'filter': True, 'post': None, 'trace': False}
```

### 2.1 取值三键：`value` / `const` / `expr`（恰好一个）

| 键 | 含义 | 例 | 编译期行为 |
| --- | --- | --- | --- |
| `value` | 行内**路径** | `"$.0"`、`"$.a.b"`、`"$.5.a.1"` | 常量链折叠：`R[0]`、`R['a']['b']` |
| `const` | 常量 | `42`、`"x"`、`[1,2]` | 原样进产物 |
| `expr` | 表达式（JSONata，可含反引号内联 Python） | `"qty * price"`、`` "`len($.1)`" `` | 逐行编译成内联表达式 |

三键互斥是**校验期强制**的：

```text
SchemaError: out.fields.a: value/const/expr 必须恰好提供一个（给了 2 个: value、const）
SchemaError: out.fields.a: value/const/expr 必须恰好提供一个（一个都没给）
```

对照实测（`value` + `const` + `expr` 各一个字段，输入 `[[1],[2]]`）：

```text
run([[1],[2]]):        [[1, 42, 43], [2, 42, 44]]
```

### 2.2 `type` 是声明，不是转换

`type` 只用于编译期分支（是否内联、Cython 的 `cdef` 类型、可选值守卫），**不会插入任何
运行时转换**。要转换就在表达式里写出来（`int($.x)` / `` `int($.0)` ``）。

```text
no_coerce 产物: _o.append([R[0],R[1]])
run([[123,'abc']]): [[123, 'abc']]
```

### 2.3 `optional`：唯一会生成 undefined 守卫的开关

`optional: true` 表示"这一行可能没有这个值"，产物里会出现 `_U`（`dcg.runtime.UNDEF`）身份判断；
**不加 `optional` 时缺数据不是 undefined，而是真实抛错**。

```text
from dcg.runtime import *
def run(_d):
 _o=[]
 for R in _d:
  _t1=_ix_get(R,0)
  a=None if _t1 is _U else _t1
  _o.append([a,R[0]])
 return _o

run([[7]]):            [[7, 7]]
run([[]]):             'IndexError: list index out of range'
```

### 2.4 嵌套：显式 `fields`

```text
nested 产物: _R0=R[0]; _o.append([_ob([('name',_R0['name']),('age',_R0['age'])]),...])
run 输出:    [[{'name': 'n1', 'age': 7}, ['x', 'y']]]
```

`fields` 只能配 `type:"object"`，且与三键互斥：

```text
SchemaError: out.fields.who: "fields" 与 value/const/expr 互斥
SchemaError: out.fields.who: 只有 type="object" 才能用 "fields"
```

### 2.5 实现现状：`items` 只是"按声明的类型走一遍"

契约措辞是"嵌套数组 = `type:"list"` 带 `items`"，但**当前实现把 `items` 当作嵌套字段
规格来生成一个一元素数组**，并不把行数据映射成数组的元素列表：

```json
"tags": {"type": "list", "value": "$.0"}                       // 原样引用，推荐
"tags": {"type": "list", "items": {"type": "int64", "value": "$.0"}}   // 产物 _ar(R[0])，每行一个单元素数组
```

```text
items demo 产物: _ar(R[0])
run([[7,8]]):  [[[7]]]
{"type":"list","items":{"type":"str"}}                     -> SchemaError: out.fields.tags.items: value/const/expr 必须恰好提供一个（一个都没给）
{"type":"object","value":"$.0","fields":{...}}             -> SchemaError: out.fields.who: "fields" 与 value/const/expr 互斥
{"type":"list","value":"$.0","items":{...}}                -> SchemaError: out.fields.tags: "items" 与 value/const/expr 互斥
```

**这是与 CONTRACT.md §2 措辞的一处实现落差**（已记录，见 §9），写 schema 时请按上面两种可用形态来用。

### 2.6 schema 就是输出契约

v2 **没有**"算出来但不输出"的机制（v1 的 `show:false` / `_field` 临时变量在这里没有对应物）。
`out.fields` 里的每个字段都会出现在输出行里，顺序即位置语义。

v1 形状的报错会主动提示迁移：

```text
SchemaError: out.fields.a: 未知键: ext, name（疑似 v1 字段形状；请用 python -m dcg.migrate 迁移）
```

---

## 3. 表达式语法

### 3.1 符号表

| 写法 | 含义 | 例 / 实跑结果 |
| --- | --- | --- |
| `$` | 当前行（整个行对象） | `value: "$"` → `_o.append([R])` |
| `$.名` / `$.数字` | 路径取字段（`.`） | `$.sku` → `R['sku']`；`$.5.a.1` → `R[5]['a'][1]` |
| `名称` | **裸标识符 = 已声明字段** | `qty * price` → `(qty*price)` |
| `$.名(...)` | 逐行白名单函数 | `$uppercase($.sku)` → `_up(R[0])` |
| `` `...` `` | 反引号：内联 **Python** 表达式 | `` `str($.5.a.1) + str(hourB)` `` |
| `#` / `@` / `~>` / `..` / 正则 / `function(){}` | v1 的位置绑定、join 绑定、变换、范围、高阶字面量 | **一律 `Unsupported`**（契约 §3.5） |

逐行函数白名单（契约 §3.4）摘要：字符串 `$string $uppercase $lowercase $trim $pad $contains
$split $join $substring $substringBefore $substringAfter $replace $length $base64encode
$base64decode $encodeUrlComponent $decodeUrlComponent $formatNumber $formatBase`；数值 `$number
$abs $floor $ceil $round $sqrt $power $sum(仅字面量数组) $min $max`；布尔/类型 `$boolean $not
$exists $type`；数组（仅字面量）`$count $append $reverse $sort $distinct`。

### 3.2 编译期 IR（`dcg.compile_expr`）

```text
'$.hourA + hourB':     ('bin', '+', ('index', ('root', (1, 1)), ('lit', 'hourA', (1, 2)), (1, 2)), ('field', 'hourB', (1, 11)), (1, 9))
'$uppercase($.sku)':   ('call', '$uppercase', [('index', ('root', (1, 12)), ('lit', 'sku', (1, 13)), (1, 13))], (1, 11))
'`str($.5.a.1) + str(hourB)`': ('py', 'str($.5.a.1) + str(hourB)', ['5.a.1', 'hourB'], (1, 1))
```

IR 是纯元组，最后一项永远是位置 `(line, col)`；`('field', 名字, loc)` 表示裸标识符按字段解析，
未知裸名在 **build 期**报错并列出可用字段。

### 3.3 反引号内联 Python

- 内容必须是**单个 Python 表达式**（`ast.parse(mode="eval")` 校验，编译期只 `parse` 不 `exec`）。
- 段内可用 `$.` 链（**任意深度**，如 `$.a.b.c.d`）与裸字段名（裸名必须是本 schema 已声明字段）。
- 字符串字面量里的裸名不会被当成字段（`` `'hourB' + str($.5.a.1)` `` 只登记 `5.a.1`）。
- `__import__` / `open` 之类**不会被拦**——这是能力，也是风险（见 §5）。

```text
compile_expr('`__import__("os").getcwd()`') -> ('py', '__import__("os").getcwd()', [], (1, 1))
compile_expr('`1 +`')                        -> Unsupported: 第 1 列: 反引号段 Python 语法错误 不可逐行编译（invalid syntax（段内偏移 0）—— 必须是单个 Python 表达式)

深度链实测：
compile_expr('`$.a.b.c.d`') -> ('py', '$.a.b.c.d', ['a.b.c.d'], (1, 1))
产物: _o.append([R['a']['b']['c']['d']])
run([{'a': {'b': {'c': {'d': 41}}}}]) -> [[41]]
```

### 3.4 完整表达式示例

```text
exprs 产物: _o.append([_up(R[0]),((_R1+'-')+str(_R2)),_R2 * 2 + len(_R1)])
run([['abc','xy',21]]): [['ABC', 'xy-21', 44]]
```

### 3.5 拒绝清单（全部实测，原样消息）

| 表达式 | 实测消息 |
| --- | --- |
| `$.a.b.c` | `Unsupported: 第 2 列: 深路径 [a,b,c] 不可逐行编译（v2 不支持 JSONata 逐行走访（契约 §6.1），请用嵌套 fields）` |
| `$sum(hour)` | `Unsupported: 第 5 列: $sum 不可逐行编译（仅支持字面量数组参数，其余需整批数据求值）` |
| `$now()` | `Unsupported: 第 5 列: $now 不可逐行编译（非确定求值）` |
| `$map(hour, function($v) { $v })` | `Unsupported: 第 12 列: function(){} 高阶字面量 不可逐行编译（需整体求值）` |
| `#.hour.3.2` | `Unsupported: 第 1 列: 位置绑定 # 不可逐行编译（契约 §3.5 要求拒绝）` |
| `@$x` | `Unsupported: 第 1 列: join 绑定 @ 不可逐行编译（契约 §3.5 要求拒绝）` |
| `1 ~> $string` | `Unsupported: 第 3 列: 变换操作符 ~> /\|...\| 不可逐行编译（契约 §3.5 要求拒绝）` |
| `$notARowFunc($.0)`（不带 registry） | `Unsupported: 第 13 列: $notARowFunc 不可逐行编译（未知函数（不在契约 §3.4 逐行白名单内））` |

自己的逐行函数要通过 `Registry` 注册，名字**必须**带 `$`：

```text
Registry.fn("double", ...)  -> ValueError: Registry.fn: 名字必须形如 "$myfn"，收到 'double'
reg.fn('$double', lamb); compile_expr('$double($.0)', registry=reg) -> ('rfunc', '...<lambda>', [...], (1, 8))
```

---

## 4. 输出形态

`output.shape` 四选一，**编译期固定**（D8）。同一个 2 字段 schema + `[['A-1',2],['B-2',1]]`：

| `shape` | 返回 | 实跑结果 |
| --- | --- | --- |
| `"list"`（默认） | 行列表（每行是位置列表） | `[['A-1', 2], ['B-2', 1]]` |
| `"dict"` | 列式：`{字段: [每行的值]}` | `{'sku': ['A-1', 'B-2'], 'qty': [2, 1]}` |
| `"index"` | `{行号: 行}` | `{0: ['A-1', 2], 1: ['B-2', 1]}` |
| `"rows"` | 行式：每行一个 `{字段: 值}` | `[{'sku': 'A-1', 'qty': 2}, {'sku': 'B-2', 'qty': 1}]` |

`shape="rows"` 是 v1 `protocol: False` 的对等物（v1 生成码 `dict(zip(keys, temp))`），
键序 = 字段声明序。它与 `"dict"` 的差别是**容器方向**：`dict` 是 `{字段: 整列}`，
`rows` 是 `[{字段: 值}, ...]`。

### 4.1 `single`

- `shape="list"` + `single=true` → 返回**单行**（不是行列表）：`['A-1', 2]`。
- `shape="rows"` + `single=true` → 返回**首个行 dict**（不是行 dict 列表）：`{'sku': 'A-1', 'qty': 2}`。
- `shape="dict"|"index"` + `single=true` → **校验期**报错：

```text
SchemaError: output.single: shape='dict' 下 single=true 无意义：该形状返回的是整列/整表，不是单行（§4.1）
```

### 4.2 `filter`：行级真值，不是字段过滤

`filter` 是**布尔**（默认 `true`）。`true` 时产物里每行前有 `if R:`——空行/假值行被丢弃：

```text
输入 [[], [1,2], [], [3]]（一个 const 字段）
filter=true:           [[1], [1]]
filter=false:          [[1], [1], [1], [1]]
```

v1 的 `filter: 'Flight_ID'`（**字段名**）在 v2 没有对应物；同名不同义是迁移时的头号陷阱（见 §7）。

### 4.3 `post` / `trace`：目前只做格式校验，未接线

```text
post='pkg.mod.fn' 产物 == 无 post 产物: True
SchemaError: output.post: 必须是 "pkg.mod.fn" 形式的字符串，得到 'f'
trace=true 产物 == 无 trace 产物: True
```

也就是说 `post`（整批后处理，v1 的 `func`）与 `trace`（v1 的 `debug`）**当前产物里没有任何代码**，
只有 `post` 的字符串形状校验。计划中的语义见 CONTRACT.md §2/§4.1；落地前请勿依赖。

### 4.4 `input.source`

`"index"` 会让产物第一行解包一层（对应 v1 `datas: "$.index"`）：

```text
input.source=index 产物: def run(_d): / _d=_d['index'] / ...
run({'index':[['a'],['b']]}): [['a'], ['b']]
```

---

## 5. 能力边界（诚实清单）

| 能力 | 现状 | 证据 |
| --- | --- | --- |
| 深路径 `$.a.b`（**expr/JSONata 侧**） | ❌ 拒绝，要求用嵌套 `fields` | `Unsupported: 第 2 列: 深路径 [a,b,c] …` |
| 深路径（**`value` 侧**） | ⚠️ **接受任意深度**（`$.a.b.c` → `R['a']['b']['c']`），代价是行必须是 dict；**空段被吞掉**：`$..a` / `$.a.` / `$.a..b` 被静默当成 `$.a` / `$.a` / `$.a.b`（见 §9 O-1） | `run([{'a':{'b':{'c':42}}}]) -> [[42]]` |
| 反引号 `$.` 链 | ✅ **任意深度**（`$.a.b.c.d` → `R['a']['b']['c']['d']`） | `compile_expr` 实跑 |
| 整批语义 `$sum $map $sort $reduce $now $millis …` | ❌ 一律 `Unsupported`，不降级 | 拒绝清单 §3.5 |
| 反引号内联 Python | ⚠️ **任意 Python 执行，不是沙箱**；build 期只 `ast.parse` 不执行 | `__import__("os").getcwd()` 通过编译 |
| 运行期切换 shape | ❌ 不支持，要为不同形态**分别 build** 两个产物 | `output.shape` 编译期决定产物结构 |
| `post` / `trace` | ⚠️ 未接线（见 §4.3） | 产物字节相同 |
| `items` 嵌套数组 | ⚠️ 只按类型生成一元素数组（见 §2.5） | `_ar(R[0])` |
| 类型化列输入 | 可选第二产物（Cython 路径），不是默认 | `cython=True` 产物只多 `cdef object` 行 |

**"不降级"是刻意的**：`Unsupported` 意味着"这份 schema 在 v2 做不到"，而不是"悄悄换成慢的实现"。
v1 在 `TRYE_MODE=True` 下遇到这类情况会静默丢数据（见 §7.3）。

---

## 6. 性能

### 6.1 终值（CPU 口径，可引用）

判定口径（契约 §7）：**同机、同数据、同进程内的比值** `v2 / 手写参照 ≤ 1.05×`；绝对 µs/行不可跨会话比较。
计时用 `time.process_time()`（只计本进程 CPU 消耗），**不受并发抢占影响**——静默窗口要求已作废。

```text
EFFICIENCY_RATIO v2_over_hand=0.985 v2_over_v1=0.957 limit=1.05 verdict=PASS confidence=RESOLVED margin=0.0646
```

| 轮次 | hand | v1 | v2 | v2cy | **v2/hand** | v2/v1 | 判定 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| R1 | 1.3867 | 1.3770 | 1.3574 | 1.2500 | **0.9789** | 0.9858 | PASS |
| R2 | 1.2402 | 1.3086 | 1.3086 | 1.2500 | **0.9710** | 0.9620 | PASS |
| R3 | 0.9082 | 0.9082 | 0.9082 | 0.8594 | **0.9854** | 0.9568 | PASS |
| **worst-of（判决）** | — | — | — | — | **0.9854** | **0.9568** | **PASS / RESOLVED** |

口径细节：K=8 遍摊销 ~15.6 ms 计时粒度、变体**交替**测量、7 reps/轮、共 3 轮；主聚合 = **每个 rep 内按变体配对的比值中位**，
判决取 **worst-of-rounds**。A/A 噪声底 **0.0142**，判决 margin **0.0646 ≈ 4.6×** 噪声底；
`round_verdicts=["PASS","PASS","PASS"]`、`rounds_agree=True`、`blocks_disagree_in_sign=False`。

**三条诚实注记（必须与数字一起引用）：**

1. **只引用比值，绝对 µs 不可引用**：三轮绝对时间 1.39 → 0.91 µs/行（35% 漂移，机器负载在测量期间下降），
   绝对列仅作标定。
2. **分辨率上限 ≈1%**：K=8、n=200000 → 单次测量 = 1.6e6 行遍，`process_time()` 网格 15.625 ms ⇒ 每行 ≈0.0098 µs（≈1.1%）。
   判决 margin 是噪声底的 4.6 倍，故 `RESOLVED` 成立，但**不得据此宣称小于 1% 的改进**。
3. **旧 wall-clock 数字互相矛盾、已作废**：`1.065 / 1.117 / 1.169`（FAIL）与 `0.901 / 0.937 / 1.000`（PASS）
   都是噪声——本机常有 5–9 个并发 python 进程，wall-clock **没有 5% 分辨率**（同一函数交替 15 轮，自身 spread 39%–97%）。
   历史上的 W2 `FAIL` 是**测量方法**的失败，不是代码的失败，已被本轮 W3 取代。

- `cython=True`：同轮 **0.859 µs/行**（绝对值同样不可引用），**不计入 CAP/M0 字符预算**（契约 §4.2）。
- 锚定 revision：`dcg/codegen.py A40457511277`（run 前后 7 个文件 sha1 逐字节一致）。
- 证据：[bench/bench_compare.md](bench/bench_compare.md) §3.2e-final、`evidence/row_v2.json`（`provisional=false`，
  窗口 `CPU-2026-10-17T01:40:37+08:00`）、`evidence/row_v2_w3_stdout.txt`、
  `evidence/accept_final.txt`（**16 PASS / 0 FAIL / 0 SKIP**）。复现命令：`python -X utf8 bench/row_v2.py --frozen --n 200000`。

### 6.2 确定性结论（不受计时噪声影响）

| 证据 | 数字 | 锚点 |
| --- | --- | --- |
| 产物字符数 CAP（4 字段，v1-faithful 写法） | v1 **445**（+118 字符空桩）→ v2 **157**（PASS，−64.7%） | `bench/bench_compare.md`，codegen sha1 `2DC5FCB4…` |
| 产物字符数 M0（8 字段） | v1 **537**（+117 空桩）→ v2 **195**（PASS，−63.7%；内联前是 283 FAIL） | 同上 |
| 产物 `compile()` 次数 | v1 **2**（含一次 118 字符空桩）→ v2 **1** | 同上 |
| 确定性 | 同 schema 两次 `generate()` **字节相同** | 同上（规则⑧） |
| 行为等价 | v1 例1 的迁移产物 `run()` 输出与 v1 冻结输出**逐行相等** | [examples/flight.json](examples/flight.json) + `evidence/examples/v1_examples_frozen.json` |
| Cython 同源 | `cython=True` 产物 = 纯 Python 源码 + 每个局部一行 `cdef object`，语义不变 | `dcg.generate(s, cython=True)` 实测（仅多 5 行） |

上表的字符数采集于 codegen sha1 `2DC5FCB4…`（`bench/bench_compare.md` 在采集前后复核过 sha1 未变）；
在最终修订 `A40457511277` 上的等价复核同样记录在该文件里，引用时请连锚点一起引。

本项目自测的实跑数据（同一冻结修订上重取）：`examples/flight.json`（v1 例1 迁移产物）
**9 行 / 203 字符**，`compile()` 一次，喂 `examples/flight_rows.json` 的 10 行得到
`[[0,1,2,[1,2,3]], 1, 2, 0, '12', 3, 3, 125, 5]`，与 v1 冻结输出 `result['doc']` **逐行相等**。

---

## 7. 从 v1 迁移

### 7.1 用法

```bash
# 迁移：读 v1 describe 导出文件，写 v2 schema + 结构化报告
$ python -X utf8 -m dcg.migrate examples/flight_v1.json -o examples/flight.json \
      --report examples/flight_migration_report.json

# 加 --check 立刻自检（validate + generate），--build-dir 再真构建一次
$ python -X utf8 -m dcg.migrate examples/adcm_v1.json -o examples/adcm_v2.json \
      --report examples/adcm_v1_report.json --check
```

| 参数 | 说明 |
| --- | --- |
| `INPUT` | v1 describe（`{"struct": {"doc": [...]}, ...}`），也接受包在 `{"describe": …}` / `{"schema": …}` 里的形状 |
| `-o/--out` | v2 schema 输出路径；默认 `<输入 stem>.v2.json`；`-o -` 走 stdout |
| `--report FILE` | 结构化报告（JSON）落盘 |
| `--name` | 覆盖产物名；默认取 describe 里的 `name`，否则取输入文件名并去掉 `_v1` 后缀 |
| `--check` | 额外 `dcg.validate()` + `dcg.generate()` |
| `--build-dir DIR` | 再调一次 `dcg.build()`（配合 `--overwrite`） |
| `--quiet` | 只在 stderr 留一行汇总 |

**退出码**：`0` 全部自动迁移；`3` 已产出 schema 但**存在 manual/unsupported 项**（必须人工确认）；
`1` 输入读不了、自检失败、或没有任何可迁移字段（此时**报告照常输出**）。

报告是给人和测试共用的稳定结构（`Report.to_dict()`）：

```json
{"source": "...", "name": "...", "needs_manual": true,
 "counts": {"mapped": 6, "derived": 14, "info": 0, "manual": 0, "unsupported": 0},
 "items": [{"kind": "mapped", "path": "out.fields.hourA", "v1": "...", "v2": "...",
            "reason": "...", "suggestion": "..."}]}
```

`kind` 五档：`mapped`（直接对应）/ `derived`（推导出的 v2 写法，如 `#.a.1.2` → `a[1][2]`）/
`info`（无需动作的提示，如 `_field` 别名展开、`const` 是取值开关）/ `manual`（必须手改）/
`unsupported`（v2 做不到）。

### 7.2 例1 端到端验收（全自动）

```text
$ python -X utf8 -m dcg.migrate examples/flight_v1.json -o examples/flight.json \
      --report examples/flight_migration_report.json --check --build-dir build --overwrite
[migrate] 迁移报告 -> examples/flight_migration_report.json
[mapped]      output.shape
              v1  : protocol=True
              v2  : shape="list"
              原因: v1 protocol=True 产出 list of row；protocol=False 产出每行 tuple(doc.items()) 的 dict（运行期构造）
              建议: v2 的 dict 形状是**列式** {字段: [每行值]}，与 v1 的逐行 dict 不同；需要逐行 dict 时 v2 没有对应形状
[derived]     output.filter
              v1  : filter=''
              v2  : filter=false
              原因: v1 的 filter 为空 = 不过滤；v2 的 filter 是布尔（默认 true = 丢弃 falsy 行），语义不同名同义
[mapped]      input.source
              v1  : datas='$.index'
              v2  : source="index"
              ...（省略中间 17 条 items）
[derived]     struct.doc[8](ext)
              v1  : #.hour.3.2 + #.hour.3.1
              v2  : hour[3][2] + hour[3][1]
[derived]     struct.doc
              v1  : {"doc": [字段...]}
              v2  : out.fields 行内展开（本层）
汇总: mapped=6  derived=14  info=0  manual=0  unsupported=0  -> 全部自动
[migrate] v2 schema -> examples/flight.json（994 字节）
[check] dcg.validate() OK（规范化后的字段数=9）
[check] dcg.generate() OK（产物 203 字符）
[check] dcg.build() OK -> {"artifact": "build\\gen\\flight.py", "chars": 203, "cython": false, "lines": 9, "name": "flight", "schema_sha1": "d3854bda78f963a4499caebab984759c2716a6b4"}
EXIT=0
```

产物（9 行 / 203 字符）与 v1 冻结输出逐行相等：

```text
def run(_d):
 _d=_d['index']
 _o=[]
 for R in _d:
  _a=R[5]['a']
  _a1=_a[1]
  _a2=_a[2]
  _o.append([_a,_a1,_a2,R[2],str(_a1) + str(_a2),_a1 + _a2,_a2 + _a1,_a[2] + 123,_a[3][2] + _a[3][1]])
 return _o
```

对应的 `report.items`（共 **20** 条：`mapped` 6 + `derived` 14）摘要：`output.shape`、`output.filter`、
`input.source`、9 个字段的 `mapped`/`derived`、`struct.doc` 行内展开、`type` 映射 `int→int64`、
5 条 `ext` → 反引号段、`#.hour.3.2 + #.hour.3.1` → `hour[3][2] + hour[3][1]`。

`examples/` 下的配套文件：

| 文件 | 用途 |
| --- | --- |
| [examples/flight_v1.json](examples/flight_v1.json) | v1 输入（`format.py:589-603` 的**例1** describe 逐字导出） |
| [examples/flight.json](examples/flight.json) | 迁移产物（v2 schema，54 行） |
| [examples/flight_migration_report.json](examples/flight_migration_report.json) | 迁移报告（21 条 items） |
| [examples/flight_rows.json](examples/flight_rows.json) | 例1 的 10 行数据（`{"index": [...]}`），用来跑产物验证等价 |
| [examples/adcm_v1.json](examples/adcm_v1.json) / [adcm_v2.json](examples/adcm_v2.json) / [adcm_v1_report.json](examples/adcm_v1_report.json) | 例5（真实 ACDM 航班数据）的"部分迁移"演示：`exit 3` |
| [examples/readme_blocks.py](examples/readme_blocks.py) | 本文所有代码块的可执行副本 |

### 7.3 能自动迁移什么

| v1 | v2 | 报告 kind |
| --- | --- | --- |
| `struct` / `docs` 下的字段列表 | `out.fields` | `mapped` / `derived`（`struct.doc` 记一条"行内展开（本层）"） |
| 字段 `value: "$.5.a.1"` | `{type, value:"$.5.a.1"}`（`$[` → `$.`） | `mapped` |
| 字段 `value: "#.hour.3.2 + #.hour.3.1"` | `expr: "` + `hour[3][2] + hour[3][1]` + `"`（`#.` → 裸字段下标） | `derived` |
| 字段 `ext: "str($.5.a.1) + str(hourB)"` | `expr: "` + `str($.5.a.1) + str(hourB)` + `"` | `derived` |
| 字段 `type: "int"/"float"/"str"/"bool"/"list"/"dict"` | `int64/float64/str/bool/list/object`（无 type → `any`） | `derived` |
| 字段 `const` / `atom`（**取值开关**，不是数据常量） | 无对应 | `info` |
| 字段 `show: true` | 无对应（默认就是输出） | `info` |
| 字段 `show: false` | 无对应（v2 没有"算出但不输出"） | `manual` |
| 字段 `alias`（输出别名） | 无对应：`out.fields` 的键名就是输出名 | `info` |
| `_field` 别名（`_field: {"_houseA": "$.1"}`） | **就地展开**到引用处（`ext: "_houseA"` → `` `$.1` ``） | `derived`；未被引用的别名记 `info`；逗号多路径 → `unsupported` |
| `datas: "$.index"` | `input.source: "index"` | `mapped` |
| `datas: ""`（空） | `input.source: "rows"`（默认） | `info` |
| 其他 `datas` 路径 | 不支持（只认 `rows`/`index`） | `manual` |
| `protocol: true` / `false` | `output.shape: "list"` / `"rows"`（**行式**，v1 对等物） | `mapped` |
| `single: true` + `protocol: true` | `output.single: true` | `mapped` |
| `single: true` + `protocol: false` | `output.single: true`（`shape="rows"` + `single` 合法，返回首个行 dict） | `mapped` |
| `filter: ""`（不过滤） | `output.filter: false` | `derived` |
| `filter: "字段名"`（**按字段过滤**） | 保守写 `filter:false`，语义未迁移 | `manual` |
| `func: "pkg.mod.fn"`（整批后处理） | `output.post` | `mapped`（但 post 尚未接线，见 §4.3） |
| `func` 是一段源码 | 必须改成可导入的 `"pkg.mod.fn"` | `manual` |
| `debug: true` | `output.trace: true` | `mapped`（但 trace 尚未接线，见 §4.3） |
| `index: {...}`（分组/合并规则） | 无对应（v2 的 `shape:"index"` 是 `{行号: 行}`） | `manual` |
| `mode` / `data` 键 | 无对应（产物形状由 `output` 决定） | `info` |
| 未知字段键 | 不在 `FieldSpec` 白名单里 | `manual` |

### 7.4 无法自动迁移项的完整清单（重点）

#### A. v2 刻意没有对应物 → `manual`（必须手改）

| # | v1 写法 | 原因 | 手改建议 |
| --- | --- | --- | --- |
| A1 | `_field` 别名层 | v2 没有"临时变量"层，schema 即输出契约；别名只在被引用时有意义 | 迁移器已就地展开；**未被任何字段引用的别名会消失**——确认它本来就是死代码 |
| A2 | `index: {"key": "merge", "number": ""}` | v2 的 `output.shape="index"` 是 `{行号: 行}`，与 v1 的分组/合并无关 | 删掉；若需要分组语义，改用 `output.post`（未接线，见 §4.3）或两次 build |
| A3 | 字段 `show: false` | v1 = "算出来但不输出"；v2 没有这个机制 | ① 把该表达式**内联**到引用它的字段（首选）② 接受它成为正式输出字段 ③ 拆成两次 build |
| A4 | `filter: "字段名"` | v1 按该字段真值过滤；v2 `filter` 是布尔 | ① 在 `out.fields` 里先声明该字段，再用 `expr` 表达过滤条件 ② 用 `post`（未接线）③ 显式接受 `filter: true`（只丢空行） |
| A5 | `struct` 有多个组（`docs`/`docsx`/`ext`…） | v1 的展开规则是隐式的（`format.py:501-527`），组边界/顺序需人工核对 | 迁移器把每组字段摊平进同一层 `out.fields`，并加一条 `manual`：`多组 struct（N 个顶层键: …）`；逐组核对这些字段的顺序/归属是否与 v1 输出一致 |
| A6 | 字段**重名**（v1 后者覆盖前者） | v2 字段名唯一 | 迁移器自动重命名为 `name_2` 并逐条报告；确认 v1 到底用的是哪一个 |
| A7 | 裸名引用**未声明**的字段（如 `key`、`hourZ`） | v1 只在 `FREE_MODE=True` 下能展开 `_field` 裸名；v2 要求裸名必须是本 schema 声明的字段 | 补声明该字段，或把别名展开成 `$.` 路径 |
| A8 | **前向引用**（`hourY` 引用后声明的 `hourX`） | v1 的 mapping 是全局的；v2 要求先声明后用（否则运行期 `NameError`） | 把被引用的字段**挪到前面**，或把它内联成表达式 |
| A9 | 未知字段键（`alias` 等） | v2 的 `FieldSpec` 只认 `type/optional/value/const/expr/fields/items` | 按报告逐条删除或改写 |

#### B. 引擎/工具确实做不到 → `unsupported`（必须换写法）

| # | v1 写法 | 实测原因 | 手改建议（已实测） |
| --- | --- | --- | --- |
| B1 | f-string 伪路径 `value: "$.data.f'AIRPORTIATA_{len(IATA)}'"` | 引擎的路径解析器不认 f-string 段 | ① 先声明 `data`（`type:"object", value:"$.data"`），再用反引号 `` `data['AIRPORTIATA_' + str(len(data['AIRPORTIATA']))]` `` → 实测输出 `'PEK'`；② 逐键字段 + `$exists(IATA_3) ? IATA_3 : IATA` → 实测 `'PEK'`（JSONata 侧 `data.AIRPORTIATA_2` 反而会被拒：`深路径 $[AIRPORTIATA_2]`） |
| B2 | 非数字下标 `value: "$.a[$x]"` | 引擎报 `value 路径下标非法: [$x]` | 用 expr/反引号；反引号里 `$.data['...']` 动态下标会报"裸名不是声明字段"，需先声明 `data` 再写 `data['...']` |
| B3 | `value: "$.*"` / `"$..a"` / 空段 | `$.*` 报 `value 路径非法: '$.*'（剩余 '*'）`；**`$..a` 被静默当成 `R['a']`**（潜在陷阱） | 显式写出字段名 |
| B4 | `_field` 里逗号多路径（`"IATAS": "$.A_1,$.A_2,$.A_3"`） | 该别名**被引用时**才报 `unsupported`（v1 允许一个别名映射多路径，v2 无别名层）；未被引用时只记 `info` | 拆成多个字段，或用反引号段显式拼 |

#### C. 语义差异：迁移器**能转但要人确认**（不报错，只是值对等/容器不同）

1. **`protocol: false`** → v2 `shape="rows"`（逐行 `{字段: 值}` dict，**同构**）。历史上这里曾是「v2 没有逐行 dict 形状」，2026-10-17 为迁移对等补齐了 `rows`。
2. **`filter` 同名不同义**（见 A4）。
3. **深路径 `value`**（`$.a.b`）：v2 的 `value` 侧接受任意深度，但要求**行是 dict**；用 list 行会在运行期 `TypeError`。`expr` 侧则拒绝深路径，必须改用嵌套 `fields`。
4. **字段顺序 = 输出位置**：v1 靠 `_field` 的插入顺序，v2 靠 `out.fields` 的声明顺序，迁移器保持原顺序，但组间顺序需人工核对。
5. **`type` 不转换**：v1 在 Cython 路径里带 `cdef` 类型声明，v2 的 `type` 只是编译期断言，不做运行期 `int()`/`float()`。

### 7.5 六例迁移实测一览

对 `format.py:576-816` 的 6 个 `describe` 样例（各有冻结的 v1 真实输出）全文跑迁移器：

| 例子 | mapped | derived | info | manual | unsupported | 退出码 | 结论 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 例1 `flight` | 6 | 14 | 0 | 0 | 0 | 0 | 全自动，产物与 v1 **逐行相等** |
| 例2 多组 struct | 26 | 72 | 16 | 19 | 0 | 3 | 迁移器报 19 条 manual（4 个未声明裸名、8 条重名、1 条多组、6 条前向引用）；**结果已证明可等价**：整篇 11 个顶层段落用「12 个 v2 产物 + 6 个常量 + 组装层」与 v1 逐值相等（`bench/compose_v1_document.py`） |
| 例3 `_field` 别名 | 2 | 8 | 0 | 0 | 0 | 0 | 全自动（v1 侧需 `FREE_MODE=True` 才有正确输出） |
| 例4 `protocol=False` | 6 | 6 | 0 | 0 | 0 | 0 | 全自动；`protocol=False` → `output.shape="rows"`，与 v1 逐行 dict **完全等价** |
| 例5 真实 ACDM | 3 | 3 | 9 | 1 | 1 | 3 | 动态键可用反引号段表达（`` `$.data['AIRPORTIATA_' + str(len($.data.AIRPORTIATA))]` ``），与 v1 结果一致；`filter` 语义仍需人工确认 |
| 例6 `index` + `show:false` | 1 | 25 | 13 | 2 | 0 | 3 | `index` 键与 `show:false` 需手改（见 A2/A3） |

例5 的部分迁移产物可以直接跑：

```text
SchemaError 之外（迁移器报告 unsupported=1）：
out.fields = {Flight_ID: {type any, value "$.data.ID"}, Task_Type: {type any, value "$.data.TASK_TYPE.0"}}
generate 146 字符；run([{'data': {'ID': 'FL001', 'TASK_TYPE': ['ARR','DEP']}}]) -> [['FL001', 'ARR']]
```

### 7.6 已知 v1 缺陷（迁移器只**点名**，不复现其行为）

| 缺陷 | 位置 | 现象 |
| --- | --- | --- |
| 默认模式缺 `filter` 键 → 裸 `KeyError: 'filter'` | `format.py:551` | describe 不写 `filter` 时直接崩（不是 `SchemaError`） |
| `_field` 裸名只在 `FREE_MODE=True` 展开 | v1 `Format.FREE_MODE` | 默认 `FREE_MODE=False` → `NameError`；配 `TRYE_MODE=True` 则**静默丢行** |
| `index` 形状从未跑通 | `dcg.py:138` 模板用 `itertools.chain`，但 `format.py` 从未 import | 无条件 `NameError: name 'chain' is not defined` |
| `CYTHON_MODE=True`（v1 默认）在例2/3/4 抛错 | v1 Cython 路径按字段名 `cdef`，例2 里 `hour` 声明了两次 | `TypeError: Expected list, got int\|tuple` |

v2 对前两条的原则性回答是：**非法输入一律给带 JSON 路径的 `SchemaError`，绝不静默降级**。

---

## 8. 公开 API 与仓库结构

```python
dcg.__all__ == ["validate", "SchemaError", "generate", "build", "gen_name",
                "compile_expr", "Unsupported", "Registry"]      # 恰好 8 个
```

| 符号 | 签名 / 说明 |
| --- | --- |
| `validate(schema)` | 校验 + 规范化（返回深拷贝；路径转 tuple、`output` 补默认值） |
| `SchemaError` | `ValueError` 子类，带 `.path` / `.msg`，`str(e)` = `"<path>: <msg>"` |
| `generate(schema, *, cython=False, inline_optional=True)` | 返回产物源码；`cython=True` 时同源 + `cdef object` 行 |
| `build(schema, out_dir, *, name=None, overwrite=False)` | 落盘 `out_dir/gen/<name>.py`，返回 manifest |
| `gen_name(schema)` | 产物名 = `schema["name"]`（无随机） |
| `compile_expr(src, *, loc_base=(1,1), registry=None)` | JSONata/反引号 → IR（纯函数） |
| `Unsupported` | 拒编译异常，`str()` 形如 `第 12 列: $sum 不可逐行编译（…）` |
| `Registry` | `fn(name, callable)` / `get(name)`，名字必须形如 `$myfn` |

### 8.1 目录结构

共 204 个文件 / 约 1.9 MB（不含 `__pycache__`）。`★` = 改动这里会影响验收结论。

```text
DCG/
├─ README.md                  本文档（§9 与 CONTRACT.md §8 逐条对应）
├─ CONTRACT.md                验收契约：§7.1 计时口径、§8 已知落差 O-1..O-7
├─ DESIGN.md                  v2 设计决策与权衡
├─ PLAN_v2.md                 v2 实施计划（§12 的部分性能结论已被 §6 推翻）
├─ pyproject.toml             pytest 配置（注意 addopts="-q"，再叠 -q 会吞摘要行）
├─ .gitignore
│
├─ dcg.py          [v1 引擎·只读参照] v1 的路径取值与代码生成
├─ format.py       [v1 引擎·只读参照] v1 的 describe→handler；本文所用例1 在 :589-623
├─ jsonata_poc.py  [PoC]              JSONata 可行性原型，未接入 v2
│
├─ dcg/                       v2 引擎（零第三方依赖；产物只依赖 dcg.runtime）
│  ├─ __init__.py             公开面恰好 8 个符号（见上表）
│  ├─ schema.py               validate / 规范化 / SchemaError
│  ├─ ir.py                   IR 节点常量
│  ├─ codegen.py ★            schema → Python 源码（核心，44 KB）
│  ├─ runtime.py ★            产物运行期 helpers（产物第一行 from dcg.runtime import *）
│  ├─ build.py                build() 落盘产物 + manifest
│  ├─ migrate.py              v1 describe → v2 schema 迁移器（44 KB）
│  └─ jx/                     JSONata 前端
│     ├─ lexer.py / parser.py   词法 / 语法
│     ├─ compiler.py            AST → IR（36 KB）
│     ├─ paths.py               路径段解析
│     ├─ policy.py              逐行可编译性策略（§3.5 拒绝清单的来源）
│     └─ registry.py            $自定义函数注册表
│
├─ tests/                     730 passed
│  ├─ test_schema.py          schema 校验契约
│  ├─ test_codegen_snapshot.py★ 产物逐字节快照 + 九条 codegen 规则
│  ├─ test_e2e_flight.py      例1 端到端（与 v1 冻结输出逐行比对）
│  ├─ test_jx_conformance.py / test_jx_examples.py / test_jx_policy.py
│  ├─ test_jx_py.py / test_jx_py_block.py   反引号内联 Python
│  ├─ test_sentinel_integrity.py
│  ├─ test_v1_ex2_document.py 例2 异构文档：v2 多产物 + 组装层 vs v1 冻结产物逐值
│  ├─ test_v1bridge_collision.py  dcg.py 与 dcg/ 同名遮蔽的回归
│  └─ examples/               docs 可追溯性 + v1 六例对等（v1_parity/）
│
├─ bench/                     基准、探针与冻结证据
│  ├─ bench_row.py ★          §7.1 唯一计时实现（measure_cpu / measure_interleaved_cpu）
│  ├─ bench_compare.md        主性能报告（§3.2f = W3 终值）
│  ├─ bench_examples.md       六例基准报告
│  ├─ bench_user_case.py/.md  format.py 例1 的九维对比（补充实验，见该文件 §11）
│  ├─ verify_v1_examples.py ★  v1 format.py 六个注释案例的 v2 对等取证（identical/partial/not）
│  ├─ compose_v1_document.py ★ 例2 异构文档的「多产物 + 组装层」迁移样板（19/19 段逐值相等）
│  ├─ codegen_rules.py        九条 codegen 规则的机器判定
│  ├─ run_accept_gate.py      evidence/accept.py 的驱动
│  ├─ run_verify.py / quiet_window.py / row_v2.py   W3 验证窗口与性能采样
│  ├─ probe_*.py              一次性探针（复现 §8/§9 落差与历史实验，可删）
│  ├─ _v1bridge.py            v1/v2 同进程共存（解决 dcg/ 遮蔽 dcg.py）
│  ├─ _workload.py            共享工作负载 M0/CAP、ref_tight 手写参照
│  └─ frozen/                 conformance.json（50 例 + 3 拒绝）、baseline_v1.txt
│
├─ evidence/                  采集产物与验收
│  ├─ accept.py ★             静态验收门（16 项，读 bench 输出的 JSON）
│  ├─ accept_final.txt        终值：16 PASS / 0 FAIL / 0 SKIP
│  ├─ row_v2.json             W3 性能终值；row_v2_W2.json 是更早 FAIL 的留证
│  ├─ noise_floor.json        A/A 噪声底 + 2×2 消融
│  ├─ user_case_bench.json    例1 九维基准的机器数据
│  ├─ pytest_final_t3.txt     717 passed（六例复核后为 719）
│  ├─ pytest_final_ex2.txt    730 passed（例2 异构文档组装层 + 4 新测试后）
│  ├─ examples/               六例基准的逐次 Run（含 W1 未打 tag 的留证副本）
│  └─ pytest_gate_trap/       证明 -q/-qq 吞摘要行的对照实验
│
├─ docs/
│  ├─ jsonata_syntax.md       216 条 JSONata 例样（正常/未实现/缺陷/已修复）
│  └─ examples_v1_to_v2.md    v1 六例逐例对照 + §10 性能
│
└─ examples/
   ├─ flight_v1.json                   v1 describe 导出（例1）
   ├─ flight.json ★                    v2 等价 schema（§7.2 端到端验收用）
   ├─ flight_rows.json                 例1 的 10 条数据
   ├─ flight_migration_report.json     migrate 的结构化报告
   ├─ flight_generated_{list,dict,index}.py / .pyx   dcg.generate 的实际产物
   ├─ adcm_v1.json / adcm_v2.json / adcm_v1_report.json   例2 迁移
   ├─ readme_blocks.py                 本文所有 Python 代码块的实跑副本
   └─ jx/01..15*.jx                    216 条例样的可执行来源（15 个文件）
```

生成产物只依赖 `dcg.runtime`（"零第三方依赖"，不是单文件自包含）：

```python
from dcg.runtime import *      # 产物第一行
```

---

## 9. 已知落差（与 [CONTRACT.md §8](CONTRACT.md) 逐条对应）

编号、症状、严重度、出处与契约 §8 一致；**冻结期内不修**（避免在验证进行中改动被测量对象，
被测量的 revision 是 `dcg/codegen.py A40457511277`）。
其中 **O-1 / O-1b / O-2 / O-7 四条由 Lead 与 verifier-dev 各自独立复现**
（Lead：`bench/probe_known_gaps.py`；verifier：`bench/probe_contract8_gaps.py`、`bench/probe_schema_error_paths.py`、
`bench/probe_deferred_errors.py`，证据见 `evidence/v2_contract8_gaps.txt`、`evidence/v2_schema_error_paths.txt`、
`evidence/v2_deferred_errors.txt`）。**O-1 与 O-7 属"静默"类（错值 / 放行不报）**——本项目最不能容忍的一类，逐条登记如下。

| # | 症状 | 严重度 | 出处 | 现状 / 影响 |
| --- | --- | --- | --- | --- |
| O-1 | **`value` 路径里的空段被静默吞掉**：`"$..a"` / `"$.a..b"` / `"$.a."` / `"$...a"` 全部被接受，分别等价于 `"$.a"` / `"$.a.b"` / `"$.a"` / `"$.a"` | 🔴 静默错值 | `dcg/codegen.py` 的 `value` 路径解析（与 `dcg/jx/paths.py` 是两套实现） | 契约 §3.5 把 `..` 列为结构拒绝。实测 `value="$..a"` → `_o.append([R['a']])`（`run([{'a': 7}])` → `[[7]]`）；唯一被正确拒绝的是 `"$.*"`（`SchemaError: … value 路径非法: '$.*'（剩余 '*'）`）。⇒ **路径里打错字不会报错，只会静默取到别处的值**。**已知未修，冻结期内不动**（修法：`value` 路径解析遇空段即 `SchemaError`）。 |
| O-1b | `expr` 侧的 `$..a` 被拒绝，但**消息虚构了一个源文本里不存在的区间**；且 `validate()` 放行 | 🟡 可诊断性 | `dcg/jx/policy.py:119`、`dcg/schema.py` | **两个入口消息一致、列号存在**：`compile_expr('$..a')` 与 `generate(expr='$..a')` 都返回 `Unsupported: 第 2 列: 区间 a..b 不可逐行编译（需要序列语义）`。真正的问题是消息里那个 `a..b` 来自 `dcg/jx/policy.py:119` 的 `"range": ("区间 a..b", "需要序列语义")` **固定标签**，而用户源文本是 `$..a`——**读者会以为自己写错了区间表达式**。另：`validate()` 对含 `$..a` 的 schema **不报错**，拒绝推迟到 `generate`（该点也归入 O-7）。**它是拒绝、不是错值。** |
| O-7 | **`validate()` 不是完整闸门**：多种非法 schema 通过 `validate` | 🟠 静默放行 | `dcg/schema.py`（validate 偏松）vs `dcg/codegen.py`（generate 才严） | `validate()` 通过但 `generate()` 抛 `Unsupported`：`$sum(...)`（整批函数）、反引号语法错 `` `1 + ``；**两者都通过、且永不报错**：`{"type":"list"}` 缺必填 `items`、`{"type":"object"}` 缺必填 `fields`（契约 §2 要求必填）。⇒ 用户拿到 "validate OK" 可能到 `generate` 才炸，或生成语义不安全产物。**已知未修，冻结期内不动**（修法：把 `items`/`fields` 必填与表达式预检挪进 `validate()`）。 |
| O-2 | **`post` / `trace` 被 schema 接受但产物零代码；`post` 的校验远弱于其报错消息** | 🟠 静默无效 | `dcg/schema.py` 收键、`dcg/codegen.py` 未发射 | `bytes(带 post) == bytes(不带 post)` **True**（74 字符对 74 字符）；`trace=true` 同理。`post` 只做「str 或 None」校验：`'$.a + 1'`、`` '`$.a`' ``、`'$sum($.a)'`、`None` **全部放行**，只有 `123` 被拒而消息写 `必须是 "pkg.mod.fn" 形式的字符串`——**消息夸大了实际校验强度**。**已知未修，冻结期内不动**（见 §4.3；迁移器仍记 `mapped`，否则会静默丢语义）。 |
| O-3 | `items` 生成单元素数组、不映射行数据 | 🟡 语义落差 | `dcg/schema.py:156/160`、`dcg/codegen.py` `_ar()` | 契约 §2 措辞像"嵌套数组"；实际每行产一个单元素数组，`items` 无取值键时报 `value/const/expr…一个都没给`（见 §2.5）。 |
| O-4 | 深路径 `$.a.b`：`expr` 侧拒绝、`value` 侧任意深度接受 | 🟡 口径落差 | `dcg/jx/compiler.py`（expr）/ `dcg/codegen.py`（value） | 契约 §6.1 针对的是**表达式层**（`expr`/反引号），`value` 侧无此限制：`value: "$.a.b.c"` → `R['a']['b']['c']`（要求行是 dict）。`value` 侧深路径是**已文档化能力**（见 §3.3 的 `` `$.a.b.c.d` ``），但**数据平铺时会运行期崩** `TypeError: 'int' object is not subscriptable`。迁移器按"实现接受"迁移并给 `info`（见 §5）。 |
| O-5 | `docs/examples_v1_to_v2.md` 把例5 整体判为"无 v2 对应" | ⚪ 文档过期 | — | 实测 `$.data.ID` 与 `$.data.TASK_TYPE[0]` 都能迁移并实跑（产物 146 字符），例5 实为"部分 schema + exit 3"（见 §7.5）；该文档不在本工具写域，未改。 |
| O-6 | `DESIGN.md` §0/§12 的状态快照过期 | ⚪ 文档过期 | — | 快照写 11 PASS/5 FAIL、M0 283 字符；现在 `filter` 是行级 `if R:`、`cython=True` 可用、M0 = 195 字符。README 只从 `DESIGN.md` 取架构叙述，不引用其状态行。 |

**另记一条只影响文档的差异**（非引擎落差）：例2 多组 struct 的平铺顺序与
`docs/examples_v1_to_v2.md` 里手写的 `EX2_DOCS` 不完全一致——两侧输入本来就不同，
**不强行对齐**：迁移器给 `manual`（多组 struct）提示，以 v1 实际输出为准逐组核对。

发现新的落差，请按 [CONTRACT.md](CONTRACT.md) §5/§6 的边界先报、再登记到 §8，不要静默调和。
