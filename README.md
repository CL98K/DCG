# DCG

> **DCG 是一个运行时代码生成器：用 JSON schema + JSONata 子集描述“怎么转”，在 build 期编译成固化的 Python 模块 `gen/<name>.py`，运行期直接运行，无解释器开销。**

DCG（Data Config Generator）的转换逻辑写在 schema 里：取值、路径、表达式、输出形态；编译期展开成只干这件事的 Python 模块，模块只有一个入口 `run(rows)`。运行期不再解释任何表达式，**执行效率来自“编译期展开、运行期无解释开销”**。

- **冻结契约**：[DCG 接口实现细节文档.md](<DCG 接口实现细节文档.md>)（schema 规格、表达式语义、公开 API、能力边界、错误信息格式）
- **设计与取舍**：[DCG 整体设计方案与架构文档.md](<DCG 整体设计方案与架构文档.md>)（整体设计方案、架构、关键决策与能力边界）
- **JSONata 语法**：[JSONata 语法.md](<JSONata 语法.md>)（分类手册 + 54 个函数 + 234 条可执行语料）
- **验收与证据**：[evidence/](evidence/)（`accept.py` 16 项门禁 + 冻结的 JSON/TXT）

**实现状态**：全量 `pytest` **744 个测试（740 passed / 4 skipped，跳过的是非确定 helper 对拍）**；`accept.py` **16 PASS / 0 FAIL / 0 SKIP**；
JSONata 端到端支持 **54 个函数**（A 类行内 45 / B 类高阶 6 / C 类非确定 4，`$sort` 同属 A/B）；
产物字符预算 CAP **149/180**、M0 **195/210**；与手写参照的 CPU 时间比 **1.019 ≤ 1.05**（中位轮 0.978）。

本文的示例都实际执行过；可执行副本见
[examples/readme_blocks.py](examples/readme_blocks.py)（`python -X utf8 examples/readme_blocks.py`）。
为便于阅读，部分输出做了截取或合并（只保留关键行），完整输出以实跑为准。

---

## 0. 适配场景

**主场景：任意数据结构转换。** 把一种数据结构按固定规则转成另一种——扁平行、嵌套行、
按行分组的多产物、字段改名与重组、按表达式取值——规则用 JSONata 子集写在 schema 里，
编译成模块后运行期零解释开销。

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
manifest: {'name': 'order', 'schema_sha1': 'e3cea33b94d60bbce46fb45feab2a38d9a29ee79', 'artifact': 'build\\gen\\order.py', 'trace_artifact': None, 'lines': 8, 'chars': 122, 'cython': False}
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

完整规格见 [DCG 接口实现细节文档.md §2](<DCG 接口实现细节文档.md>)。顶层结构：

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

对照实测（三个字段分别用 `value` / `const` / `expr`，输入 `[[1],[2]]`）：

```python
"a": {"type": "int64", "value": "$.0"},      # R[0]
"b": {"type": "int64", "const": 42},         # 常量 42
"c": {"type": "int64", "expr": "b + 1"},     # 裸标识符 b 指的是字段 b
```

```text
产物: _o.append([R[0],b,(b+1)])
run([[1],[2]]):        [[1, 42, 43], [2, 42, 43]]
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
  if R:
   _t1=_ix_get(R,0)
   a=None if _t1 is _U else _t1
   _o.append([a,R[1]])
 return _o

run([[7, 8]]):         [[7, 8]]
run([[7]]):            'IndexError: list index out of range'   # 非 optional 的 $.1 缺数据 = 抛错
```

注意 `if R:`：**空行（`[]`）在进字段求值前就被整行滤掉**，所以 `run([[]])` 返回 `[]` 而不是报错。
`optional` 只保护它自己那个字段（缺值时给 `None`），不改变行级过滤。

### 2.4 嵌套：显式 `fields`

```text
nested 产物: _R0=R[0]; _o.append([_ob([('name',_R0['name']),('age',_R0['age'])]),R[1]])
run 输出:    [[{'name': 'n1', 'age': 7}, ['x', 'y']]]
```

`fields` 只能配 `type:"object"`，且与三键互斥：

```text
SchemaError: out.fields.who.fields: 只有 type="object" 才能用 "fields"
SchemaError: out.fields.who.fields: 只有 type="object" 才能用 "fields"
```

### 2.5 实现现状：`items` 只是"按声明的类型走一遍"

契约措辞是"嵌套数组 = `type:"list"` 带 `items`"，但**当前实现把 `items` 当作嵌套字段
规格来生成一个一元素数组**，并不把行数据映射成数组的元素列表：

```json
"tags": {"type": "list", "value": "$.0"}                       // 原样引用，推荐
"tags": {"type": "list", "items": {"type": "int64", "value": "$.0"}}   // 产物 _ar(R[0])，每行一个单元素数组
```

```text
items demo 产物: _o.append([_ar(R[0])])
run([[7,8]]):  [[[7]]]
{"type":"list","items":{"type":"str"}}                     -> SchemaError: out.fields.tags.items: value/const/expr 必须恰好提供一个（一个都没给）
{"type":"object","value":"$.0","fields":{...}}             -> SchemaError: out.fields.who.fields: 只有 type="object" 才能用 "fields"
{"type":"list","value":"$.0","items":{...}}                -> SchemaError: out.fields.tags: "items" 与 value/const/expr 互斥
```

**这是与 DCG 接口实现细节文档.md §2 措辞的一处实现落差**（已登记在 §9 的剩余边界），写 schema 时请按上面两种可用形态来用。

### 2.6 schema 就是输出契约

v2 **没有**"算出来但不输出"的机制（旧版的 `show:false` / 临时变量层在这里没有对应物）。
`out.fields` 里的每个字段都会出现在输出行里，顺序即位置语义。

旧版字段形状的报错会主动提示迁移：

```text
SchemaError: out.fields.a: 未知键: ext, name（疑似旧版字段形状；请用 python -m dcg.migrate 迁移）
```

---

## 3. 表达式语法

### 3.1 符号表

| 写法 | 含义 | 例 / 实跑结果 |
| --- | --- | --- |
| `$` | 当前行（整个行对象） | `value: "$"` → `_o.append([R])` |
| `$.名` / `$.数字` | 路径取字段（`.`） | `$.sku` → `R['sku']`；`$.5.a.1` → `R[5]['a'][1]` |
| `名称` | **裸标识符 = 已声明字段** | `qty * price` → `(qty*price)` |
| `$.名(...)` | 函数调用（A / B / C 三类共 54 个，见 §3.5） | `$uppercase($.sku)` → `_up(R[0])`；`$sum($.xs)` → `_sm(_xs)` |
| `function($x){ ... }` | 函数字面量（只作为 B 类高阶函数的回调） | `$map($.xs, function($x){ $x * 2 })` → `_mp(_xs,lambda x,_i1:_ml(x,2))` |
| `` `...` `` | 反引号：内联 **Python** 表达式 | `` `str($.5.a.1) + str(hourB)` `` |
| `#` / `@` / `~>` / `..` / 正则 / `^` / `\|...\|` / `{k:v}` / `$eval` | 位置绑定、join 绑定、变换、范围、正则、排序、分组、运行期求值 | **一律 `Unsupported`**（DCG 接口实现细节文档 §3.9） |

逐行函数白名单（DCG 接口实现细节文档 §3.4）摘要见 §3.5；三类函数共 54 个，全部在 `dcg/jx/policy.py` 里按名字归类。

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

### 3.5 支持度总览（全部实测）

| 类别 | 数量 | 函数 / 构造 |
| --- | --- | --- |
| A 类·行内函数 | 45 | 字符串 19：`$string $uppercase $lowercase $trim $pad $contains $split $join $substring $substringBefore $substringAfter $replace $length $base64encode $base64decode $encodeUrlComponent $decodeUrlComponent $formatNumber $formatBase`；数值 10：`$number $abs $floor $ceil $round $sqrt $power $sum $min $max`；布尔/类型 4：`$boolean $not $exists $type`；数组与对象 12：`$count $append $average $reverse $sort $distinct $merge $keys $values $lookup $spread $single` |
| B 类·高阶函数 | 6 | `$map $filter $reduce $each $sift $sort`（回调必须是函数字面量 `function($x[, $i]){ ... }`；`$position()` 只在回调体内可用） |
| C 类·非确定函数 | 4 | `$now() $millis() $random() $shuffle(...)`（结果不可复现；时钟/随机源可替换，见 §3.7） |
| 永久拒绝 | — | `$eval` 与语法构造：区间 `a..b`、正则 `/re/`、位置绑定 `#`、join 绑定 `@`、变换 `~>`/`\|...\|`、排序 `^`、分组 `{k:v}`、序列过滤 `[.pred]` |

实跑统计：

```text
A 类行内函数:               45
B 类高阶函数:               ['$each', '$filter', '$map', '$reduce', '$sift', '$sort']
C 类非确定函数:              ['$millis', '$now', '$random', '$shuffle']
合计（$sort 同属 A/B，去重）:   54
永久拒绝构造:                ['bind_join', 'bind_pos', 'eval', 'group', 'path', 'range', 'reduce', 'regex', 'sort', 'transform']
```

拒绝消息带列号，不降级（原样输出）：

```text
'$eval($.a)':          'Unsupported: 第 6 列: $eval 不可逐行编译（运行期求值任意表达式 = 运行期解释器入口 + 注入面）'
'$map($.a, $string)':  'Unsupported: 第 5 列: $map 不可逐行编译（函数实参必须是函数字面量 function($x){...}）'
'1..5':                'Unsupported: 第 2 列: 区间 a..b 不可逐行编译（需要序列语义）'
'/ab/':                'Unsupported: 第 1 列: 正则字面量 不可逐行编译（契约 §3.5 要求拒绝）'
'$position()':         'Unsupported: 第 10 列: $position() 不可逐行编译（只在函数字面量体内可用（序列上下文））'
'#.hour.3.2':          'Unsupported: 第 1 列: 位置绑定 # 不可逐行编译（契约 §3.5 要求拒绝）'
'@$x':                 'Unsupported: 第 1 列: join 绑定 @ 不可逐行编译（契约 §3.5 要求拒绝）'
'1 ~> $string':        'Unsupported: 第 3 列: 变换操作符 ~> / |...| 不可逐行编译（契约 §3.5 要求拒绝）'
'`1 +`':               'Unsupported: 第 1 列: 反引号段 Python 语法错误 不可逐行编译（invalid syntax（段内偏移 0）—— 必须是单个 Python 表达式）'
```

自己的逐行函数仍可通过 `Registry` 注册，名字**必须**带 `$`：

```text
Registry().fn("double", abs)  -> ValueError: Registry.fn: 名字必须形如 "$myfn"，收到 'double'
r = Registry(); r.fn('$double', abs)
compile_expr('$double($.0)', registry=r) -> ('rfunc', 'builtins.abs', [...], (1, 8))
```

### 3.6 批量 / 高阶函数（A/B 类）：编译期编成 lambda

A/B 类函数与函数字面量已经端到端可用：`function($x){...}` 在编译期展开成产物里的 Python `lambda`，
运行期没有解释器、没有逐行回调开销。函数体内还可以写 `py{}` 多语句块（块内只读原始行、不能引用回调参数），示例见 `JSONata 语法.md` §12.3。

```python
schema = {
    "name": "agg",
    "output": {"shape": "rows"},
    "out": {"type": "object", "fields": {
        "total":   {"type": "float64", "expr": "$sum($.xs)"},
        "doubled": {"type": "any",     "expr": "$map($.xs, function($x){ $x * 2 })"},
        "big":     {"type": "any",     "expr": "$filter($.xs, function($x){ $x > 2 })"},
        "top":     {"type": "int64",   "expr": "$max($.xs)"},
        "label":   {"type": "str",     "expr": "$join($map($.xs, function($x){ $string($x) }), '+')"},
    }},
}
code = dcg.generate(schema)
ns = {}
exec(compile(code, "<gen>", "exec"), ns)
print(code)
print(ns["run"]([{"xs": [1, 2, 3]}]))
```

实跑输出：

```text
from dcg.runtime import *
def run(_d):
 _o=[]
 for R in _d:
  if R:
   _xs=_ix_get(R,'xs')
   _o.append([_sm(_xs),_mp(_xs,lambda x,_i1:_ml(x,2)),_fi(_xs,lambda x,_i1:_gt(x,2)),_mx(_xs),_jn(_mp(_xs,lambda x,_i1:_S(x)),'+')])
 return [{k:r[i]for i,k in enumerate(['total','doubled','big','top','label'])}for r in _o]

run:                   [{'total': 6, 'doubled': [2, 4, 6], 'big': [3], 'top': 3, 'label': '1+2+3'}]
```

带比较器的 `$sort` 与 `$position()`（回调第二位置参数是下标）：

```text
run:                   [[[1, 2, 3], [0, 1]]]
```

### 3.7 非确定函数：结果不可复现，产物字节可复现

`$now / $millis / $random / $shuffle` 从运行期注入的时钟与随机源取值，**不用环境变量开开关**；
替换成固定源后，同一份产物可以复跑出确定结果（性能/证据场景这么做）：

```python
import dcg.runtime as rt

class FixedClock:
    now_ms = staticmethod(lambda: 1700000000000)

class FixedRandom:
    random = staticmethod(lambda: 0.5)
    shuffle = staticmethod(lambda xs: list(reversed(xs)))

rt.CLOCK = FixedClock()
rt.RANDOM = FixedRandom()
# 之后 run(...)：$millis() -> 1700000000000、$random() -> 0.5、$shuffle([1,2,3]) -> [3,2,1]
```

```text
run:                   [[1700000000000, 0.5, [3, 2, 1]]]
产物字节可复现:               True
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

`shape="rows"` 对应旧版 describe 里 `protocol: false` 的逐行 dict 输出（键序 = 字段声明序）；
它与 `"dict"` 的差别是**容器方向**：`dict` 是 `{字段: 整列}`，`rows` 是 `[{字段: 值}, ...]`。

`shape="dict"` 的产物形状（注意尾部换了容器，行内仍是位置列表）：

```text
def run(_d):
 _o=[]
 for R in _d:
  if R:
   _o.append([R[0],R[1]])
 return{k:[r[i]for r in _o]for i,k in enumerate(['sku','qty'])}
```

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

旧版的 `filter` 是**字段名**（按该字段真值过滤），v2 的 `filter` 是**布尔**（行级真值）——
同名不同义，是迁移时的头号陷阱（见 §7.4）。

### 4.3 `post` / `trace`：已接线

`post="pkg.mod.fn"` 在**组装完成后**对整个结果恰好调用一次（按需 import，不进逐行循环）：

```python
schema = {
    "name": "p",
    "output": {"shape": "list", "post": "json.dumps"},
    "out": {"type": "object", "fields": {"k": {"type": "int64", "const": 1}}},
}
# 产物开头：import importlib as _il / _pt='json.dumps' / _ps=... / _xp=getattr(...)
# run([[0]]) -> '[[1]]'（字符串）
```

实跑输出：

```text
import importlib as _il
_pt='json.dumps'
_ps=_pt.rfind('.')
_xp=getattr(_il.import_module(_pt[:_ps]),_pt[_ps+1:])
def run(_d):
 _o=[]
 for R in _d:
  if R:
   _o.append([1])
 return _xp(_o)

run:                   '[[1]]'
type:                  'str'
SchemaError: output.post: 必须是 "pkg.mod.fn" 形式的字符串，得到 'f'
```

`trace=true` 额外生成一个**独立调试产物** `gen/<name>.trace.py`（全局 `TRACE` 追加 `(原始行, 输出行)`），
正式产物与 `trace=false` **逐字节相同**：

```text
manifest:              {'name': 't', 'schema_sha1': 'd54b2e92e3d6b134372175c1e61f4a7408fb0e9f', 'artifact': 'out\\gen\\t.py', 'trace_artifact': 'out\\gen\\t.trace.py', 'lines': 6, 'chars': 71, 'cython': False}
TRACE=[]
def run(_d):
 _o=[]
 for R in _d:
  if R:
   _o.append([1])
   TRACE.append((R,_o[-1]))
 return _o

run:                   [[1], [1]]
TRACE:                 [(['A-1'], [1]), (['B-2'], [1])]
```

上面打印的是 `out/gen/t.trace.py` 的源码；正式产物 `out/gen/t.py` 只有 71 字符、不含 `TRACE` 这两行，与 `trace=false` 时逐字节相同。

### 4.4 `input.source`

`"index"` 会让产物第一行解包一层（对应旧版 `datas: "$.index"`）：

```text
input.source=index 产物: def run(_d): / _d=_d['index'] / ...
run({'index':[['a'],['b']]}): [['a'], ['b']]
```

---

## 5. 能力边界（诚实清单）

| 能力 | 现状 | 证据 |
| --- | --- | --- |
| 深路径（**expr / 反引号 / `value` 全侧**） | ✅ **任意深度**（`$.a.b.c.d` → `R['a']['b']['c']['d']`），不要求用嵌套 `fields` | `compile_expr` 实跑 / `tests/` |
| 批量 / 高阶 / 非确定函数 | ✅ 54 个（A 45 / B 6 / C 4，`$sort` 同属 A/B） | §3.5、`examples/jx/12_funcs.jx` |
| 前向引用 / 依赖求值 | ✅ 声明顺序任意；计算按字段依赖拓扑序（同层按声明序 tie-break），输出仍按声明序；环在 build 期报 `SchemaError` | `tests/test_codegen_snapshot.py` |
| `post` / `trace` | ✅ 已接线（见 §4.3） | 产物实跑 |
| `value` 路径的空段 | ⚠️ **空段被静默吞掉**：`"$..a"` / `"$.a."` / `"$.a..b"` 被静默当成 `"$.a"` / `"$.a"` / `"$.a.b"` | `dcg.validate` 实跑（见 §9） |
| 反引号内联 Python | ⚠️ **任意 Python 执行，不是沙箱**；build 期只 `ast.parse` 不执行 | `__import__("os").getcwd()` 通过编译 |
| `py{}` 多语句块 | ✅ 支持：语句序列定稿为 `run()` 之外的辅助函数；块内只读原始行、不参与字段依赖图 | DCG 接口实现细节文档 §3.8、`JSONata 语法.md` §11 |
| 运行期切换 shape | ❌ 不支持，要为不同形态**分别 build** 两个产物 | `output.shape` 编译期决定产物结构 |
| `items` 嵌套数组 | ⚠️ 只按类型生成一元素数组（见 §2.5） | `_ar(R[0])` |
| 类型化列输入（Cython） | 可选第二产物，不是默认 | `cython=True` 产物只多 `cdef object` 行 |
| 异构整篇文档（多段混排） | ❌ 一个 schema 只发一个行列表；要靠「多产物 + 一层组装」 | 见 §9 |
| 非确定函数结果 | ⚠️ 每次运行不同（产物字节可复现）；证据需注入固定时钟/随机源 | `rt.CLOCK` / `rt.RANDOM` |

**"不降级"是刻意的**：`Unsupported` 意味着"这份 schema 做不到"，而不是"悄悄换成慢的实现"。
旧版里那种"出错就被吞掉、结果静默丢行"的行为，一律变成带位置的显式报错。

已知但**未修复**的 4 条表达式缺陷（语言语义对齐问题，不影响产物形态与性能口径；逐条复现与正确语义见 `JSONata 语法.md` §14.1）：

* `math.floor($.a * 1.2)` → 运行期 `NameError: math`（生成码没有 `import math`）；
* `$.m[0]`（`m` 为 `null`）→ 抛 `TypeError`，按契约应返回 `null`；
* `$round(2.5)` → 得 `2`，JSONata 语义应为 `3`（Python 内置是银行家舍入）；
* `$count('abc')` → 得 undefined，JSONata 语义应为 `1`（非数组按单个元素计）。

---

## 6. 性能

### 6.1 终值（CPU 口径，可引用）

判定口径（契约 §7）：**同机、同数据、同进程内的比值** `v2 / 手写参照 ≤ 1.05×`；绝对 µs/行不可跨会话比较。
计时用 `time.process_time()`（只计本进程 CPU 消耗），**不受并发抢占影响**——旧的"静默窗口"要求已作废。

```text
EFFICIENCY_RATIO v2_over_hand=1.019 limit=1.05 verdict=PASS confidence=RESOLVED
                 margin_over_limit=0.0308 noise_floor=0.0278 k=8 cpu_reps=7 cpu_rounds=3 n=200000
                 aggregate=paired-median median_ratio=0.978 margin_median=0.0720 min_reps_v2_over_hand=1.034
```

| 轮次 | hand µs/行 | v2 µs/行 | **v2/hand（配对中位）** | 判定 |
| --- | --- | --- | --- | --- |
| R1 | 0.8691 | 0.8984 | **1.0192** | PASS |
| R2 | 0.8691 | 0.8506 | **0.9300** | PASS |
| R3 | 0.8691 | 0.8403 | **0.9780** | PASS |
| **worst-of（判决）** | — | — | **1.0192** | **PASS / RESOLVED** |

口径细节：K=8 遍摊销 ~15.6 ms 计时粒度、变体**交替**测量、7 reps/轮、共 3 轮；主聚合 = **每个 rep 内按变体配对的比值中位**，
判决取 **worst-of-rounds**。A/A 噪声底 **0.0278**，判决 margin **0.0308 ≈ 1.1×** 噪声底；
稳健聚合用中位轮 `median_ratio=0.9780`，`margin_median=0.0720 ≈ 2.6×` 噪声底，故 `RESOLVED`；
`round_verdicts=["PASS","PASS","PASS"]`、`rounds_agree=true`、`blocks_disagree_in_sign=false`。
（比值是配对中位，所以不完全等于上表两列绝对值的商。）

**三条诚实注记（必须与数字一起引用）：**

1. **只引用比值，绝对 µs 不可引用**：本轮三项绝对 µs 仅作标定（hand 三轮均为 0.8691），
   跨会话/跨机器比较无意义。
2. **分辨率上限 ≈1%**：K=8、n=200000 → 单次测量 = 1.6e6 行遍，`process_time()` 网格 15.625 ms ⇒ 每行 ≈0.0098 µs（≈1.1%）。
   中位轮判决 margin 是噪声底的 2.6 倍，故 `RESOLVED` 成立，但**不得据此宣称小于 1% 的改进**。
3. **旧的 wall-clock 数字已作废**：本机常有并发 python 进程，wall-clock **没有 5% 分辨率**
   （同一函数交替 15 轮，自身 spread 实测 39%–97%）。凡是用 wall-clock 得出的"通过/不通过"结论都不作数。

- `cython=True`：同轮绝对值 0.8203（约 0.94× hand），**不计入 CAP/M0 字符预算**（契约 §4.2）。
- 锚定 revision：`dcg/codegen.py 05F907B43EF5`（run 前后 `revision_stable=true`）。
- 证据：[evidence/row_v2.json](evidence/row_v2.json)（`provisional=false`，窗口 `CPU-2026-10-19T18:01:11+08:00`）、
  `evidence/row_v2_v2only_stdout.txt`、`evidence/accept_final.txt`（**16 PASS / 0 FAIL / 0 SKIP**）。
  复现命令：`python -X utf8 bench/row_v2.py --frozen --n 200000`。

### 6.2 确定性结论（不受计时噪声影响）

| 证据 | 数字 |
| --- | --- |
| 字符预算 CAP（4 字段工作负载，上限 180） | **149 / 180** ✅ |
| 字符预算 M0（8 字段工作负载，上限 210） | **195 / 210** ✅ |
| 产物 `compile()` 次数 | **1**（没有空桩、没有第二次编译） |
| 确定性 | 同 schema 两次 `generate()` **字节相同** |
| 端到端等价 | [examples/flight.json](examples/flight.json) → **9 行 / 203 字符**；喂 `examples/flight_rows.json` 的 10 行，`row0` = `[[0, 1, 2, [1, 2, 3]], 1, 2, 0, '12', 3, 3, 125, 5]` |
| Cython 同源 | `cython=True` 产物 = 纯 Python 源码 + 每个局部一行 `cdef object`，语义不变 |

字符预算由 `evidence/accept.py` 的 `char budget` 门实时从 `bench/_workload.py` 取工作负载复算，
不是写死在文档里的历史数字。

批量 / 高阶 / 非确定、前向引用、`post` / `trace` 落地后按同一口径复跑：

| 复跑项 | 结果 |
| --- | --- |
| `py -m pytest tests/` | **740 passed / 4 skipped**（744 collected） |
| `py evidence/accept.py` | **16 PASS / 0 FAIL / 0 SKIP** |
| CAP / M0 字符预算 | **149 / 180**、**195 / 210** |
| 效率（配对中位，CPU 口径） | `v2_over_hand=1.019 ≤ 1.05`（中位轮 `0.978`），`noise_floor=0.0278`，`k=8`，`n=200000` |

---

## 7. 从旧版 describe 迁移

### 7.1 用法

`dcg/migrate.py` 把旧版 describe（JSON 导出）静态改写成 v2 schema，并**逐项报告**所有无法自动迁移的项：
原因 + 手改建议。任何被改写语义、被忽略、或无法表达的键都会进报告，绝不静默丢弃。

```bash
# 迁移：读旧版 describe 导出文件，写 v2 schema + 结构化报告
$ python -X utf8 -m dcg.migrate old_describe.json -o v2_schema.json \
      --report migration_report.json

# 加 --check 立刻自检（validate + generate），--build-dir 再真构建一次
$ python -X utf8 -m dcg.migrate old_describe.json -o v2_schema.json \
      --report migration_report.json --check --build-dir build --overwrite
```

| 参数 | 说明 |
| --- | --- |
| `INPUT` | 旧版 describe（`{"struct": {"doc": [...]}, ...}`），也接受包在 `{"describe": …}` / `{"schema": …}` 里的形状 |
| `-o/--out` | v2 schema 输出路径；默认 `<输入 stem>.v2.json`；`-o -` 走 stdout |
| `--report FILE` | 结构化报告（JSON）落盘 |
| `--name` | 覆盖产物名；默认取 describe 里的 `name`，否则取输入文件名并去掉 `_v1` 后缀 |
| `--indent` | JSON 缩进（默认 2） |
| `--check` | 额外 `dcg.validate()` + `dcg.generate()` |
| `--build-dir DIR` | 再调一次 `dcg.build()`（配合 `--overwrite`） |
| `--quiet` | 不打印文本报告（只写 `--report`） |

**退出码**：`0` 全部自动迁移；`3` 已产出 schema 但**存在 manual/unsupported 项**（必须人工确认）；
`1` 输入读不了、自检失败、或没有任何可迁移字段（此时**报告照常输出**）。

报告是给人和测试共用的稳定结构（`Report.to_dict()`）：

```json
{"source": "...", "name": "...", "needs_manual": true,
 "counts": {"mapped": 6, "derived": 8, "info": 1, "manual": 1, "unsupported": 0},
 "items": [{"kind": "mapped", "path": "out.fields.hourA",
            "v1": "旧版写法", "v2": "v2 写法", "reason": "为什么", "suggestion": "怎么手改"}]}
```

`kind` 五档：`mapped`（直接对应）/ `derived`（推导出的 v2 写法，如 `#.a.1.2` → `a[1][2]`）/
`info`（无需动作的提示，如别名展开）/ `manual`（必须手改）/ `unsupported`（v2 做不到）。
报告 item 里两个键名就叫 `v1` / `v2`，分别装旧版原文与 v2 写法。

### 7.2 一个最小可复现例（实跑，退出码 3）

下面这份最小 describe（`output` 全自动、一处 `show:false`、一处 `alias`）实跑结果是
（**节选**：只贴前后的关键项与汇总，中间项省略；文件路径按示例命令改写）：

```text
$ python -X utf8 -m dcg.migrate old_describe.json -o v2_schema.json \
      --report migration_report.json --check
[migrate] 迁移报告 -> migration_report.json
[mapped]      output.shape
              v1  : protocol=True
              v2  : shape="list"
              原因: protocol=True 产出 list of row；protocol=False 产出每行一个 dict
[mapped]      input.source
              v1  : datas='$.index'
              v2  : source="index"
              原因: datas "$.index" 指向帧的 index 列表 -> v2 input.source="index"
[derived]     struct.doc[2](ext)
              v1  : str($.1) + code
              v2  : `str($.1) + code`
              原因: 旧版 ext 是一段 Python 源码；v2 用 expr 的反引号内联 Python 承接
[manual]      struct.doc[4](show=false)
              v1  : show=False
              原因: show=false 表示「算出但不输出」；v2 刻意没有这种机制（schema 即输出契约）
              建议: 三选一：(a) 把该表达式内联到引用它的字段里（首选）；(b) 接受它作为正式输出字段；(c) 拆成两次 build
[info]        struct.doc[5](alias)
              v1  : "ax"
              原因: v2 没有 alias：out.fields 的键名就是输出名
汇总: mapped=6  derived=8  info=1  manual=1  unsupported=0  -> 需要人工复核
[migrate] v2 schema -> v2_schema.json（700 字节）
[check] dcg.validate() OK（规范化后的字段数=6）
[check] dcg.generate() OK（产物 150 字符）
EXIT=3
```

产出的 schema 形状（节选）：

```json
{"name": "demo",
 "input": {"source": "index"},
 "output": {"shape": "list", "single": false, "filter": false, "post": null, "trace": false},
 "out": {"type": "object", "fields": {
   "code": {"type": "str",   "value": "$.0"},
   "qty":  {"type": "int64", "value": "$.1"},
   "tag":  {"type": "str",   "expr": "`str($.1) + code`"},
   "sum":  {"type": "int64", "expr": "`qty[0] + qty[1]`"}}}}
```

仓库里 [examples/flight.json](examples/flight.json) 与
[examples/flight_migration_report.json](examples/flight_migration_report.json) 是一次真实迁移留在仓库里的
**产物样例**（输入 describe 不随仓库分发）。

### 7.3 工具能自动做什么

| 旧版 | v2 | 报告 kind |
| --- | --- | --- |
| `struct` / `docs` 下的字段列表 | `out.fields`（本层行内展开） | `mapped` / `derived` |
| 字段 `value: "$.5.a.1"` | `{type, value:"$.5.a.1"}`（`$[` → `$.`） | `mapped` |
| 字段 `value: "#.hour.3.2 + #.hour.3.1"` | `` expr: "`hour[3][2] + hour[3][1]`" ``（`#.` → 裸字段下标） | `derived` |
| 字段 `ext: "str($.5.a.1) + str(hourB)"` | `` expr: "`str($.5.a.1) + str(hourB)`" `` | `derived` |
| 字段 `type: "int"/"float"/"str"/"bool"/"list"/"dict"` | `int64/float64/str/bool/list/object`（无 type → `any`） | `derived` |
| `_field` 别名（`{"_houseA": "$.1"}`） | **就地展开**到引用处（`ext: "_houseA"` → `` `$.1` ``） | `derived`；未引用的别名记 `info` |
| `datas: "$.index"` | `input.source: "index"` | `mapped` |
| `datas: ""`（空） | `input.source: "rows"`（默认） | `info` |
| `protocol: true` / `false` | `output.shape: "list"` / `"rows"`（逐行 dict，同构） | `mapped` |
| `single: true` | `output.single: true`（`dict`/`index` 下 v2 校验期报错） | `mapped` |
| `filter: ""`（不过滤） | `output.filter: false` | `derived` |
| `func: "pkg.mod.fn"`（整批后处理） | `output.post`（整批后处理，见 §4.3） | `mapped` |
| `debug: true` | `output.trace: true`（额外调试产物，见 §4.3） | `mapped` |
| `mode` / `data` 键 | 无对应（产物形状由 `output` 决定） | `info` |

### 7.4 需要人工确认什么（报告里有 `manual` / `info` 的，逐条看）

| 旧版写法 | 为什么不能自动定 | 手改建议 |
| --- | --- | --- |
| `filter: "字段名"`（按字段过滤） | v2 的 `filter` 是布尔，**同名不同义**；工具只能保守写 `filter:false` | ① 把该条件写进 `expr`，让它参与输出 ② 用 `output.post`（整批后处理）③ 显式接受 `filter:true`（只丢空行） |
| 字段 `show: false` | v2 没有"算出但不输出" | ① 把表达式**内联**到引用它的字段（首选）② 接受它成为正式输出字段 ③ 拆成两次 build |
| `struct` 有多个组（`docs`/`docsx`/`ext`…） | 组边界/顺序靠人工核对；工具会把各组字段摊平进同一层并报一条 `manual` | 逐组核对这些字段的顺序/归属是否与旧版输出一致 |
| `index: {...}`（分组/合并规则） | v2 的 `shape:"index"` 是 `{行号: 行}`，与分组/合并无关 | 删掉；需要分组语义就改用多产物 + 自己组装 |
| 字段**重名** | v2 字段名唯一；工具自动改名 `name_2` 并逐条报告 | 确认旧版到底用的是哪一个 |
| 裸名引用**未声明**的字段 | v2 要求裸名必须是本 schema 声明的字段 | 补声明该字段，或把别名展开成 `$.` 路径 |
| **前向引用**（后面的字段引用先声明的字段） | 本设计**允许**任意声明顺序：声明顺序只决定输出结构，计算按依赖关系（旧版要求先声明后用） | 无需调整；生成器按依赖重排求值顺序 |
| `alias` / `const` / `atom` / `show:true` | v2 无对应（`const`/`atom` 在旧版是取值开关，不是数据常量） | 按报告提示改字段名或删键 |
| 未知字段键 | 不在 `FieldSpec` 白名单里（`type/optional/value/const/expr/fields/items`） | 逐条删除或改写 |

### 7.5 工具不做什么

| 不做 | 说明 |
| --- | --- |
| 不执行旧版代码 | 迁移是**静态改写**：不跑旧版 handler、不采样真实数据，语义差异只靠规则推导 |
| 不猜 `filter` 字段名 | 同名不同义的地方一律 `manual`，不冒进映射 |
| 不生成 `shape="document"` | v2 没有这个形状；旧版的"异构整篇文档"必须靠**多产物 + 一层组装**（见 §9 O-8） |
| 不接线 `post` / `trace` | 只做键映射，产物里没有代码（O-2）；用了等于没接 |
| 不静默丢弃 | 任何被忽略/改写/无法表达的项都会出现在报告里（这是本工具的硬约束） |
| 不迁移旧版表达式全集 | `#.`/`@`/`~>`/正则/高阶函数等结构一律 `unsupported`，需要改写成 v2 表达式或反引号段 |
| 不保证 `exit 0` 就是语义等价 | `exit 0` 只代表"没有需要人改的项"；等价性要自己用数据比对（§6.2 的端到端门就是干这个的） |

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

共 **226 个文件**（不含 `__pycache__` / `.pytest_cache` / `.git`）。`★` = 改动这里会影响验收结论。

```text
DCG/
├─ README.md                  本文档（§9 验证结果与剩余边界）
├─ DCG 接口实现细节文档.md         冻结接口：§1 IR、§2 schema、§3 表达式、§4 代码生成、§5–§8
├─ DCG 整体设计方案与架构文档.md   整体设计方案与架构（架构叙述取自此文，状态数字以 README 为准）
├─ pyproject.toml             打包与 pytest 配置（注意 addopts="-q"；`dev` extra 里的
│                             `jsonata` 只用于开发期语义对照，运行时零依赖）
├─ .gitattributes             `* text=auto eol=lf`
├─ .gitignore
├─ LICENSE                    Apache-2.0
│
├─ dcg/                       引擎（零第三方依赖；产物只依赖 dcg.runtime）
│  ├─ __init__.py             公开面恰好 8 个符号（见上表）
│  ├─ schema.py   ★           validate / 规范化 / SchemaError
│  ├─ ir.py                   冻结的 IR 节点常量
│  ├─ codegen.py  ★           schema → Python 源码（核心）
│  ├─ runtime.py  ★           产物运行期 helpers（产物第一行 from dcg.runtime import *）
│  ├─ build.py                build() 落盘产物 + manifest（CLI: python -m dcg.build）
│  ├─ migrate.py              旧版 describe → schema 迁移器（见 §7）
│  └─ jx/                     JSONata 前端
│     ├─ lexer.py / parser.py   词法 / 语法
│     ├─ compiler.py            AST → IR
│     ├─ paths.py               路径段解析
│     ├─ policy.py              函数能力表与可编译性策略（A/B/C 与永久拒绝）
│     └─ registry.py            $自定义函数注册表
│
├─ tests/                     744 tests（740 passed / 4 skipped）
│  ├─ test_schema.py          schema 校验契约（含四形状、取值三键互斥）
│  ├─ test_codegen_snapshot.py ★  产物逐字节快照 + 零冗余规则
│  ├─ test_e2e_flight.py      ★  flight 端到端：产物行为、四种 shape、optional/filter、Cython 同源
│  ├─ test_sentinel_integrity.py  undefined 是独立对象（0 != undefined）
│  ├─ test_jx_conformance.py  冻结 conformance 例样（50 通过 / 3 拒绝）
│  ├─ test_jx_examples.py     234 条 JSONata 例样逐条可执行
│  ├─ jx_corpus.py            语料解析 + 真实执行层（测试与文档生成共用）
│  ├─ gen_jsonata_manual.py   JSONata 语法.md 生成器（分类示例逐条校验）
│  ├─ test_jsonata_manual.py  JSONata 语法.md 与渲染结果逐字节一致
│  ├─ test_jx_policy.py       函数能力表（54 个）与拒绝策略（含"运行期绝不 import jsonata"）
│  ├─ test_jx_py.py           反引号内联 Python 表达式
│  └─ test_jx_py_block.py     反引号内的多语句块
│
├─ bench/                     基准、探针与冻结证据（29 个文件）
│  ├─ bench_row.py ★          §7.1 唯一计时实现（measure_cpu / measure_interleaved_cpu）
│  ├─ row_v2.py    ★          效率窗口：跑 3 轮 CPU 口径并写 evidence/row_v2.json
│  ├─ _workload.py ★          共享工作负载 M0/CAP + 手写参照 ref_tight
│  ├─ codegen_rules.py        零冗余规则的机器判定
│  ├─ run_accept_gate.py      evidence/accept.py 的驱动
│  ├─ run_verify.py / quiet_window.py   验证窗口编排
│  ├─ cy_ceiling.py / cy_ceiling2.py    Cython 路径上限探索
│  ├─ probe_*.py              19 个一次性探针（复现 §9 落差与历史实验）
│  └─ frozen/conformance.json 冻结的 50 例 + 3 拒绝（永久拒绝构造）
│
├─ evidence/                  采集产物与验收（41 个文件）
│  ├─ accept.py    ★          静态验收门（16 项，读 bench/evidence 的 JSON）
│  ├─ accept_final.txt        终值：16 PASS / 0 FAIL / 0 SKIP
│  ├─ row_v2.json ★           效率终值（v2-only，PASS / RESOLVED）
│  ├─ row_v2_v2only_stdout.txt  该次窗口的完整 stdout
│  ├─ noise_floor.json        A/A 噪声底
│  ├─ pytest_final_v2only.txt 历史 696 passed 冻结日志（当时版本）
│  ├─ v2_*.txt                历史探针原始输出（冻结，见 §9）
│  └─ pytest_gate_trap/       证明 -q/-qq 会吞摘要行的对照实验
│
└─ examples/
   ├─ flight.json     ★        规范示例 schema（§1 / §6.2 的端到端用它）
   ├─ flight_rows.json         配套 10 条数据（{"index": [...]}）
   ├─ flight_migration_report.json   一次真实迁移的报告留档
   ├─ flight_generated_{list,dict,index}.py / flight_generated_cython.pyx   实际产物
   ├─ adcm_v2.json             真实航班数据的部分迁移结果（exit 3）
   ├─ readme_blocks.py         本文所有 Python 代码块的实跑副本
   └─ jx/01..15*.jx            234 条例样的可执行来源（15 个文件；01–15，含 12_funcs.jx）
```

生成产物只依赖 `dcg.runtime`（"零第三方依赖"，不是单文件自包含）：

```python
from dcg.runtime import *      # 产物第一行
```

---

## 9. 验证结果与剩余边界

本次实现完成后，在干净工作树上复跑：

| 验证 | 结果 |
| --- | --- |
| 全量测试 `py -m pytest tests/` | **740 passed / 4 skipped**（744 collected；10 个 `test_*.py`，另 `jx_corpus.py`、`gen_jsonata_manual.py` 为测试辅助，`tests/` 共 12 个 `.py`） |
| JSONata 语料（`examples/jx/*.jx`） | **234 条**，逐条真实执行（解析/执行层 `tests/jx_corpus.py`） |
| 冻结 conformance（`bench/frozen/conformance.json`） | **50/50 编译、3/3 拒绝** |
| 静态验收门禁 `py evidence/accept.py` | **16 PASS / 0 FAIL / 0 SKIP** |
| 字符预算 CAP / M0 | **149/180**、**195/210** |
| 效率（CPU 口径，配对中位） | `v2_over_hand=1.019 ≤ 1.05`（中位轮 0.978），`noise_floor=0.0278`，`k=8`，n=200000 |

仍存在的边界（是设计选择，不是未修缺陷）：

| # | 边界 | 现状 |
| --- | --- | --- |
| 1 | `value` 路径空段 | ⚠️ `"$..a"` / `"$.a."` / `"$.a..b"` 被静默当成 `"$.a"` / `"$.a"` / `"$.a.b"`（`dcg.validate` 实跑放行）；`expr` 侧同类写法被拒绝 |
| 2 | `items` 嵌套数组 | ⚠️ 只按声明类型生成单元素数组（见 §2.5） |
| 3 | 异构整篇文档 | ❌ 一个 schema 只发一个行列表；要「多产物 + 一层组装」（不迁移旧版 `shape="document"`） |
| 4 | 运行期切换 shape | ❌ 形态编译期固定，要为不同形态分别 build |
| 5 | 反引号内联 Python | ⚠️ 任意 Python 执行，不是沙箱；build 期只 `ast.parse` |
| 6 | 非确定函数 | ⚠️ 结果不可复现；产物字节可复现；证据需注入固定时钟/随机源（§3.7） |

| 7 | `$eval` / 语法构造 | ❌ 永久拒绝，不降级（§3.5） |

另：`JSONata 语法.md` §14.1 列了 4 条**仍未修复**的表达式缺陷（`math.floor` 运行期 `NameError`、`$.m[0]` 对 `null` 抛 `TypeError`、`$round(2.5)=2`、`$count('abc')=undefined`），修好时对应用例会从 §14.1 移到 §14.2 回归保护组。

发现新边界的流程：先按 [DCG 接口实现细节文档.md](<DCG 接口实现细节文档.md>) 判断是否属于设计边界，再登记，不静默调和。
