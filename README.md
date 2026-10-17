# DCG

**一份 JSON schema，换一个定制 Python 模块。**

DCG（Data Config Generator）把一个声明式的 JSON schema 编译成一个只能干这件事的
Python 模块 `gen/<name>.py`，入口是 `run(rows)`。

- **冻结契约**：[CONTRACT.md](CONTRACT.md)（schema 规格、表达式语义、公开 API、能力边界、§8 已知落差）
- **设计与取舍**：[DESIGN.md](DESIGN.md)（D1–D10 决策与架构叙述）
- **JSONata 子集**：[docs/jsonata_syntax.md](docs/jsonata_syntax.md)（216 条可执行例样）
- **验收与证据**：[evidence/](evidence/)（`accept.py` 16 项门禁 + 冻结的 JSON/TXT）

本文里**每一个代码块都实际跑过**，输出是原样粘贴的。可执行副本见
[examples/readme_blocks.py](examples/readme_blocks.py)（`python -X utf8 examples/readme_blocks.py`）。

> 当前实测：`pytest tests/` → **696 passed**；`python -X utf8 evidence/accept.py` →
> **16 PASS / 0 FAIL / 0 SKIP**。
> 引擎修订锚点（sha1[:12]）：`dcg/codegen.py` `A4A3D4794457`、`dcg/runtime.py` `CD760A6FBA57`、
> `dcg/schema.py` `2A2BC9880CFB`、`dcg/ir.py` `1C9F537A400D`、`dcg/jx/compiler.py` `E516185DF2C3`。
> §6 的性能终值锚定同一修订（`evidence/row_v2.json` 的 `revision_stable_during_run=true`）。

---

## 0. 适配场景

DCG 适合下面这类工作。不在表里的场景，先确认是否真的需要"编译成模块"这一层。

### 0.1 适合的场景

| 场景 | 为什么适合 | 实据 |
| --- | --- | --- |
| 行式数据转换要固化 | 一套声明式 schema 编译成只干这件事的模块，逻辑随产物走，运行期不再解释 | 产物入口只有 `run(rows)`；`gen/<name>.py` 只 `from dcg.runtime import *` |
| 转换逻辑要可评审、可 diff | schema 是唯一输出契约，改一行 schema 就是一次可评审的差异；产物稳定可复现 | 同 schema 两次 `generate()` 字节相同；契约见 [CONTRACT.md](CONTRACT.md) |
| 性能敏感的行式管道 | 编译期把代码铺开，热路径没有解释开销 | §6：`v2 / hand` 配对中位 1.000×（上限 1.05×，PASS） |
| 旧版 describe 配置要迁移 | 静态改写并逐项报告无法自动迁移的项，不假装成功 | §7：`dcg/migrate.py`，报告分 `mapped / derived / info / manual / unsupported` 五档 |
| 部署环境不允许第三方依赖 | 产物零第三方依赖，单文件可带进离线或受限环境 | `pyproject.toml` 的 `dependencies = []`；§8 仓库结构 |
| 需要 JSONata 子集做逐行表达式 | 有可执行的语法子集与 216 条例样，超出子集一律显式报错而不是静默降级 | [docs/jsonata_syntax.md](docs/jsonata_syntax.md) |
| 数据形态固定、字段在编译期已知 | 输出形态、变量名、助手函数在编译期全部确定，产物可以按 schema 审计 | §2 schema 规格；§4 输出形态 |

### 0.2 不适合的场景

| 场景 | 会碰到什么 |
| --- | --- |
| 通用 ETL / 查询引擎（join、分组、多输入） | 没有这些算子；`output.shape="index"` 只是 `{行号: 行}`，不是分组/合并 |
| 运行期切换行为（MODE 开关、动态字段） | 输出形态、变量名、助手函数在编译期全部固定，要两种形态就 build 两个产物 |
| JSONata 全量语义、整批/递归/高阶函数 | 只支持逐行子集，整批语义一律显式拒绝（§5 能力边界） |
| 异构整篇文档输出 | 没有 `shape="document"`，需要"多产物 + 一层组装"（§9 O-8） |
| 把不可信 schema 当沙箱 | 反引号内联段是任意 Python 执行 |
| 需要"算出来但不输出"的中间变量层 | 没有这种机制；中间值要内联进表达式，或接受它成为输出字段 |
| 只想写几行脚本、不打算固化产物 | 直接用 Python/pandas 更省事；DCG 的成本在 schema 与契约 |

### 0.3 写 schema 前要先接受的四条约束

1. **路径必须显式以 `$` 开头**：`"a"` 永远是常量字符串，路径必须写 `"$.a"`——
   没有"靠前缀猜这是值还是路径"的规则。
2. **嵌套必须显式 `fields`**："list 里出现 dict 就当嵌套"这类隐式特例不存在。
3. **输出形态编译期定型**：`output.shape` 在生成期决定产物结构，**不可运行期切换**；
   要两种形态就 build 两个产物。
4. **没有"算出但不输出"的机制**：`out.fields` 里的每个字段都会进输出行；
   想让中间值不出现，就在表达式里把它内联掉。


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
SchemaError: out.fields.who: "fields" 与 value/const/expr 互斥
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
{"type":"object","value":"$.0","fields":{...}}             -> SchemaError: out.fields.who: "fields" 与 value/const/expr 互斥
{"type":"list","value":"$.0","items":{...}}                -> SchemaError: out.fields.tags: "items" 与 value/const/expr 互斥
```

**这是与 CONTRACT.md §2 措辞的一处实现落差**（已登记，见 §9 的 O-3），写 schema 时请按上面两种可用形态来用。

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
| `$.名(...)` | 逐行白名单函数 | `$uppercase($.sku)` → `_up(R[0])` |
| `` `...` `` | 反引号：内联 **Python** 表达式 | `` `str($.5.a.1) + str(hourB)` `` |
| `#` / `@` / `~>` / `..` / 正则 / `function(){}` | 旧版的位置绑定、join 绑定、变换、范围、高阶字面量 | **一律 `Unsupported`**（契约 §3.5） |

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
Registry().fn("double", abs)  -> ValueError: Registry.fn: 名字必须形如 "$myfn"，收到 'double'
r = Registry(); r.fn('$double', abs)
compile_expr('$double($.0)', registry=r) -> ('rfunc', 'builtins.abs', [...], (1, 8))
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

### 4.3 `post` / `trace`：目前只做格式校验，未接线

```text
post='pkg.mod.fn' 产物 == 无 post 产物: True
SchemaError: output.post: 必须是 "pkg.mod.fn" 形式的字符串，得到 'f'
trace=true 产物 == 无 trace 产物: True
```

也就是说 `post`（整批后处理）与 `trace`（调试开关）**当前产物里没有任何代码**，
只有 `post` 的字符串形状校验。计划中的语义见 CONTRACT.md §2/§4.1；落地前请勿依赖（见 §9 的 O-2）。

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
| 深路径 `$.a.b`（**expr/JSONata 侧**） | ❌ 拒绝，要求用嵌套 `fields` | `Unsupported: 第 2 列: 深路径 [a,b] …` |
| 深路径（**`value` 侧**） | ⚠️ **接受任意深度**（`$.a.b.c` → `R['a']['b']['c']`），代价是行必须是 dict；**空段被吞掉**：`$..a` / `$.a.` / `$.a..b` 被静默当成 `$.a` / `$.a` / `$.a.b`（见 §9 O-1） | `run([{'a':{'b':{'c':42}}}]) -> [[42]]` |
| 反引号 `$.` 链 | ✅ **任意深度**（`$.a.b.c.d` → `R['a']['b']['c']['d']`） | `compile_expr` 实跑 |
| 整批语义 `$sum $map $sort $reduce $now $millis …` | ❌ 一律 `Unsupported`，不降级 | 拒绝清单 §3.5 |
| 反引号内联 Python | ⚠️ **任意 Python 执行，不是沙箱**；build 期只 `ast.parse` 不执行 | `__import__("os").getcwd()` 通过编译 |
| 运行期切换 shape | ❌ 不支持，要为不同形态**分别 build** 两个产物 | `output.shape` 编译期决定产物结构 |
| `post` / `trace` | ⚠️ 未接线（见 §4.3） | 产物字节相同 |
| `items` 嵌套数组 | ⚠️ 只按类型生成一元素数组（见 §2.5） | `_ar(R[0])` |
| 类型化列输入（Cython） | 可选第二产物，不是默认 | `cython=True` 产物只多 `cdef object` 行 |
| 异构整篇文档（多段混排） | ❌ 一个 schema 只发一个行列表；要靠「多产物 + 一层组装」 | 见 §9 O-8 |

**"不降级"是刻意的**：`Unsupported` 意味着"这份 schema 在 v2 做不到"，而不是"悄悄换成慢的实现"。
旧版里那种"出错就被吞掉、结果静默丢行"的行为，在 v2 一律变成带位置的显式报错。

---

## 6. 性能

### 6.1 终值（CPU 口径，可引用）

判定口径（契约 §7）：**同机、同数据、同进程内的比值** `v2 / 手写参照 ≤ 1.05×`；绝对 µs/行不可跨会话比较。
计时用 `time.process_time()`（只计本进程 CPU 消耗），**不受并发抢占影响**——旧的"静默窗口"要求已作废。

```text
EFFICIENCY_RATIO v2_over_hand=1.000 limit=1.05 verdict=PASS confidence=RESOLVED
                 margin_over_limit=0.0500 noise_floor=0.0114 k=8 cpu_reps=7 cpu_rounds=3 n=200000
```

| 轮次 | hand µs/行 | v2 µs/行 | v2 cython µs/行 | **v2/hand（配对中位）** | 判定 |
| --- | --- | --- | --- | --- | --- |
| R1 | 0.8398 | 0.8398 | 0.7520 | **1.0000** | PASS |
| R2 | 0.8984 | 0.9082 | 0.8105 | **0.9903** | PASS |
| R3 | 0.9082 | 0.9277 | 0.8398 | **0.9898** | PASS |
| **worst-of（判决）** | — | — | — | **1.0000** | **PASS / RESOLVED** |

口径细节：K=8 遍摊销 ~15.6 ms 计时粒度、变体**交替**测量、7 reps/轮、共 3 轮；主聚合 = **每个 rep 内按变体配对的比值中位**，
判决取 **worst-of-rounds**。A/A 噪声底 **0.0114**，判决 margin **0.0500 ≈ 4.4×** 噪声底；
`round_verdicts=["PASS","PASS","PASS"]`、`rounds_agree=true`、`blocks_disagree_in_sign=false`。
（比值是配对中位，所以不完全等于上表两列绝对值的商。）

**三条诚实注记（必须与数字一起引用）：**

1. **只引用比值，绝对 µs 不可引用**：三轮 hand 绝对值 0.8398 → 0.9082（约 8% 漂移，机器负载在测量期间变化），
   绝对列仅作标定。
2. **分辨率上限 ≈1%**：K=8、n=200000 → 单次测量 = 1.6e6 行遍，`process_time()` 网格 15.625 ms ⇒ 每行 ≈0.0098 µs（≈1.1%）。
   判决 margin 是噪声底的 4.4 倍，故 `RESOLVED` 成立，但**不得据此宣称小于 1% 的改进**。
3. **旧的 wall-clock 数字已作废**：本机常有并发 python 进程，wall-clock **没有 5% 分辨率**
   （同一函数交替 15 轮，自身 spread 实测 39%–97%）。凡是用 wall-clock 得出的"通过/不通过"结论都不作数。

- `cython=True`：同轮绝对值 0.7520 / 0.8105 / 0.8398（约 0.90× / 0.90× / 0.92× hand），**不计入 CAP/M0 字符预算**（契约 §4.2）。
- 锚定 revision：`dcg/codegen.py A4A3D4794457`（run 前后 `revision_stable_during_run=true`）。
- 证据：[evidence/row_v2.json](evidence/row_v2.json)（`provisional=false`，窗口 `CPU-2026-10-17T22:17:27+08:00`）、
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
| `func: "pkg.mod.fn"`（整批后处理） | `output.post`（**未接线**，见 §4.3） | `mapped` |
| `debug: true` | `output.trace: true`（**未接线**） | `mapped` |
| `mode` / `data` 键 | 无对应（产物形状由 `output` 决定） | `info` |

### 7.4 需要人工确认什么（报告里有 `manual` / `info` 的，逐条看）

| 旧版写法 | 为什么不能自动定 | 手改建议 |
| --- | --- | --- |
| `filter: "字段名"`（按字段过滤） | v2 的 `filter` 是布尔，**同名不同义**；工具只能保守写 `filter:false` | ① 把该条件写进 `expr`，让它参与输出 ② 用 `output.post`（未接线）③ 显式接受 `filter:true`（只丢空行） |
| 字段 `show: false` | v2 没有"算出但不输出" | ① 把表达式**内联**到引用它的字段（首选）② 接受它成为正式输出字段 ③ 拆成两次 build |
| `struct` 有多个组（`docs`/`docsx`/`ext`…） | 组边界/顺序靠人工核对；工具会把各组字段摊平进同一层并报一条 `manual` | 逐组核对这些字段的顺序/归属是否与旧版输出一致 |
| `index: {...}`（分组/合并规则） | v2 的 `shape:"index"` 是 `{行号: 行}`，与分组/合并无关 | 删掉；需要分组语义就改用多产物 + 自己组装 |
| 字段**重名** | v2 字段名唯一；工具自动改名 `name_2` 并逐条报告 | 确认旧版到底用的是哪一个 |
| 裸名引用**未声明**的字段 | v2 要求裸名必须是本 schema 声明的字段 | 补声明该字段，或把别名展开成 `$.` 路径 |
| **前向引用**（后面的字段引用先声明的字段） | v2 要求先声明后用（否则运行期 `NameError`） | 把被引用的字段挪到前面，或内联成表达式 |
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

共 **125 个文件 / 约 1.0 MB**（不含 `__pycache__` / `.pytest_cache`）。`★` = 改动这里会影响验收结论。

```text
DCG/
├─ README.md                  本文档（§9 与 CONTRACT.md §8 逐条对应）
├─ CONTRACT.md                冻结契约：§7.1 计时口径、§8 已知落差 O-1..O-8
├─ DESIGN.md                  v2 设计决策与权衡（架构叙述取自此文，状态数字以 README 为准）
├─ pyproject.toml             打包与 pytest 配置（注意 addopts="-q"；`dev` extra 里的
│                             `jsonata` 只用于开发期语义对照，运行时零依赖）
├─ .gitattributes             `* text=auto eol=lf`
├─ .gitignore
├─ LICENSE                    Apache-2.0
│
├─ dcg/                       v2 引擎（零第三方依赖；产物只依赖 dcg.runtime）
│  ├─ __init__.py             公开面恰好 8 个符号（见上表）
│  ├─ schema.py   ★           validate / 规范化 / SchemaError
│  ├─ ir.py                   冻结的 IR 节点常量
│  ├─ codegen.py  ★           schema → Python 源码（核心，45 KB）
│  ├─ runtime.py  ★           产物运行期 helpers（产物第一行 from dcg.runtime import *）
│  ├─ build.py                build() 落盘产物 + manifest（CLI: python -m dcg.build）
│  ├─ migrate.py              旧版 describe → v2 schema 迁移器（44 KB，见 §7）
│  └─ jx/                     JSONata 前端
│     ├─ lexer.py / parser.py   词法 / 语法
│     ├─ compiler.py            AST → IR（36 KB）
│     ├─ paths.py               路径段解析
│     ├─ policy.py              逐行可编译性策略（§3.5 拒绝清单的来源）
│     └─ registry.py            $自定义函数注册表
│
├─ tests/                     696 passed
│  ├─ test_schema.py          schema 校验契约（含四形状、取值三键互斥）
│  ├─ test_codegen_snapshot.py ★  产物逐字节快照 + 零冗余规则
│  ├─ test_e2e_flight.py      ★  flight 端到端：产物行为、四种 shape、optional/filter、Cython 同源
│  ├─ test_sentinel_integrity.py  undefined 是独立对象（0 != undefined）
│  ├─ test_jx_conformance.py  冻结 conformance 例样（50 通过 / 3 拒绝）
│  ├─ test_jx_examples.py     docs/jsonata_syntax.md 的 216 条例样逐条可执行
│  ├─ test_jx_policy.py       拒绝清单与策略（含"运行期绝不 import jsonata"）
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
│  └─ frozen/conformance.json 冻结的 50 例 + 3 拒绝
│
├─ evidence/                  采集产物与验收（41 个文件）
│  ├─ accept.py    ★          静态验收门（16 项，读 bench/evidence 的 JSON）
│  ├─ accept_final.txt        终值：16 PASS / 0 FAIL / 0 SKIP
│  ├─ row_v2.json ★           效率终值（v2-only，PASS / RESOLVED）
│  ├─ row_v2_v2only_stdout.txt  该次窗口的完整 stdout
│  ├─ noise_floor.json        A/A 噪声底
│  ├─ pytest_final_v2only.txt 696 passed 的冻结日志
│  ├─ v2_*.txt                探针原始输出（§9 每条落差的证据）
│  └─ pytest_gate_trap/       证明 -q/-qq 会吞摘要行的对照实验
│
├─ docs/
│  └─ jsonata_syntax.md       216 条 JSONata 例样（正常 / 未实现 / 缺陷 / 已修复）
│
└─ examples/
   ├─ flight.json     ★        规范示例 schema（§1 / §6.2 的端到端用它）
   ├─ flight_rows.json         配套 10 条数据（{"index": [...]}）
   ├─ flight_migration_report.json   一次真实迁移的报告留档
   ├─ flight_generated_{list,dict,index}.py / flight_generated_cython.pyx   实际产物
   ├─ adcm_v2.json             真实航班数据的部分迁移结果（exit 3）
   ├─ readme_blocks.py         本文所有 Python 代码块的实跑副本
   └─ jx/01..15*.jx            216 条例样的可执行来源（15 个文件）
```

生成产物只依赖 `dcg.runtime`（"零第三方依赖"，不是单文件自包含）：

```python
from dcg.runtime import *      # 产物第一行
```

---

## 9. 已知落差（与 [CONTRACT.md §8](CONTRACT.md) 逐条对应）

编号、症状、严重度与契约 §8 一致；**以下均为当前修订实测仍未修的项**（在最终修订
`dcg/codegen.py A4A3D4794457` 上逐条复现过）。修法写在契约里，修之前不要依赖这些行为。

其中 **O-1 与 O-7 属"静默"类（错值 / 放行不报）**——本项目最不能容忍的一类，逐条登记如下：

| # | 症状 | 严重度 | 出处 | 现状 / 影响 |
| --- | --- | --- | --- | --- |
| O-1 | **`value` 路径里的空段被静默吞掉**：`"$..a"` / `"$.a..b"` / `"$.a."` / `"$...a"` 全部被接受，分别等价于 `"$.a"` / `"$.a.b"` / `"$.a"` / `"$.a"` | 🔴 静默错值 | `dcg/codegen.py` 的 `value` 路径解析（与 `dcg/jx/paths.py` 是两套实现） | 契约 §3.5 把 `..` 列为结构拒绝。实测 `value="$..a"` → `_o.append([R['a']])`（`run([{'a': 7}])` → `[[7]]`）；唯一被正确拒绝的是 `"$.*"`（`SchemaError: … value 路径非法: '$.*'（剩余 '*'）`）。⇒ **路径里打错字不会报错，只会静默取到别处的值**。修法：`value` 路径解析遇空段即 `SchemaError`。 |
| O-1b | `expr` 侧的 `$..a` 被拒绝，但**消息虚构了一个源文本里不存在的区间**；且 `validate()` 放行 | 🟡 可诊断性 | `dcg/jx/policy.py:119`、`dcg/schema.py` | `compile_expr('$..a')` 与 `generate(expr='$..a')` 都返回 `Unsupported: 第 2 列: 区间 a..b 不可逐行编译（需要序列语义）`；那个 `a..b` 是策略表里的**固定标签**，而用户源文本是 `$..a`——读者会以为自己写错了区间表达式。另：`validate()` 对含 `$..a` 的 schema 不报错，拒绝推迟到 `generate`（并进 O-7）。**它是拒绝、不是错值。** |
| O-2 | **`post` / `trace` 被 schema 接受但产物零代码；`post` 的校验远弱于其报错消息** | 🟠 静默无效 | `dcg/schema.py` 收键、`dcg/codegen.py` 未发射 | `bytes(带 post) == bytes(不带 post)` **True**（79 字符对 79 字符）；`trace=true` 同理。`post` 只做「str 或 None」校验：`'$.a + 1'`、`` '`$.a`' ``、`'$sum($.a)'`、`None` **全部放行**，只有 `123` 被拒而消息写 `必须是 "pkg.mod.fn" 形式的字符串`——**消息夸大了实际校验强度**。迁移器仍记 `mapped`，否则会静默丢语义（见 §4.3）。 |
| O-3 | `items` 生成单元素数组、不映射行数据 | 🟡 语义落差 | `dcg/schema.py:156/160`、`dcg/codegen.py` `_ar()` | 契约 §2 措辞像"嵌套数组"；实际每行产一个单元素数组（见 §2.5）。 |
| O-4 | 深路径 `$.a.b`：`expr` 侧拒绝、`value` 侧任意深度接受 | 🟡 口径落差 | `dcg/jx/compiler.py`（expr）/ `dcg/codegen.py`（value） | 契约 §6.1 针对的是**表达式层**（`expr`/反引号），`value` 侧无此限制：`value: "$.a.b.c"` → `R['a']['b']['c']`（要求行是 dict），且这是**已文档化能力**（§3.3 的 `` `$.a.b.c.d` ``）。**但数据平铺时会运行期崩**：`run([{'a': 7}])` → `TypeError: 'int' object is not subscriptable`。 |
| O-7 | **`validate()` 不是完整闸门**：多种非法 schema 通过 `validate` | 🟠 静默放行 | `dcg/schema.py`（validate 偏松）vs `dcg/codegen.py`（generate 才严） | 实测 `validate()` 通过、`generate()` 才抛 `Unsupported` 的有：`$sum($.0)`（整批函数）、反引号语法错 `` `1 + ``。⇒ 用户拿到 "validate OK" 可能到 `generate` 才炸。**修法**：把整批函数、反引号语法预检挪进 `validate()`。 |
| O-8 | **没有 `shape="document"`：旧版的「异构文档输出」不能由**一个** v2 schema 表达** | 🟡 迁移形态落差 | `dcg/schema.py:19`（`SHAPES = ("list","dict","index","rows")`）、`dcg/codegen.py` `finish()` | 旧版 `struct` 可以是一篇多段文档（行式列表 + 逐行标量列表 + 顶层常量 + 嵌套子文档混在一个 dict 里）。v2 一次只发一个行列表；`validate()` 对 `shape="document"` 直接报 `SchemaError: output.shape: 必须是 ('list','dict','index','rows') 之一`。**结果仍然可以一致**，但迁移形态是「多个 v2 产物 + 一层组装」。⇒ **可以迁移，但不能声称 v2 单个 schema 覆盖该能力**。 |

**另有两条"文档过期"类落差（O-5 / O-6），指向的历史文档已随旧版一起删除**，契约 §8 保留其原文作为历史记录；
它们不影响引擎行为，本文不再复述。发现新的落差，请按 [CONTRACT.md](CONTRACT.md) §5/§6 的边界先报、再登记到 §8，
不要静默调和。
