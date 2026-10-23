# DCG

> **DCG 是一个运行时代码生成器：用 JSON schema + JSONata 子集描述“怎么转”，在 build 期编译成固化的 Python 模块 `gen/<name>.py`，运行期直接运行，无解释器开销。**

- **冻结契约**：[DCG 接口实现细节文档.md](<DCG 接口实现细节文档.md>)（schema 规格、表达式语义、公开 API、能力边界、错误信息格式）
- **设计与取舍**：[DCG 整体设计方案与架构文档.md](<DCG 整体设计方案与架构文档.md>)（整体设计方案、架构、关键决策与能力边界）
- **JSONata 语法**：[JSONata 语法.md](<JSONata 语法.md>)（分类手册 + 55 个函数 + 242 条可执行语料）
- **验收与证据**：[evidence/](evidence/)（`accept.py` 14 项门禁 + 冻结的 JSON/TXT）

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

## 1.1 一个复杂例子：多段嵌套 + 列容器 + 位置行

上面的例子只有 4 个字段。这一节给一份真实规模的 schema，把几种嵌套形态一次用全：
`dict` 形状的顶层列、每行一份的具名对象、逐层 `aggregate` 的列容器、以及位置行。
完整可运行副本见 [examples/readme_complex.py](examples/readme_complex.py)。

```python
schema = {
    "name": "flight_pack",
    "input": {"source": "$.index"},
    "output": {"shape": "dict"},
    "out": {"type": "object", "fields": {
        # ── 每行算一遍的叶子 ────────────────────────────────────────
        "flight_id": {"type": "int64", "value": "$.fid"},
        "route":     {"type": "str",   "expr": "`$.dep + '-' + $.arr`"},
        "hours":     {"type": "list",  "value": "$.hours"},
        "total":     {"type": "int64", "expr": "$sum(hours)"},
        "delayed":   {"type": "bool",  "expr": "total > 4"},

        # ── 具名对象：每行一份，不标 aggregate ────────────────────
        "leg": {"type": "object", "fields": {
            "from": {"type": "str",   "value": "$.dep"},
            "to":   {"type": "str",   "value": "$.arr"},
            "span": {"type": "int64", "expr": "total"},
        }},

        # ── 列容器：逐层 aggregate，各子键各成一列 ────────────────
        "stats": {"type": "object", "aggregate": True, "fields": {
            "count": {"type": "int64", "const": 1},
            "span":  {"type": "int64", "expr": "total"},
            "deep":  {"type": "object", "aggregate": True, "fields": {
                "ids":    {"type": "list", "expr": "`[$.fid, $.fid + 1]`"},
                "routes": {"type": "list", "expr": "`[$.dep, $.arr]`"},
            }},
        }},

        # ── 位置行：按声明序出行，undefined 落 None 保位 ───────────
        "pos": {"type": "list", "fields": {
            "id":    {"type": "int64", "value": "$.fid"},
            "span":  {"type": "int64", "expr": "total"},
            "extra": {"type": "any", "value": "$.maybe", "optional": True},
        }},
    }},
}
```

三行输入（第三行多一个 `maybe`，用来验证 optional 保位）：

```python
{"index": [
    {"fid": 101, "dep": "URC", "arr": "PEK", "hours": [1, 2, 3]},
    {"fid": 202, "dep": "KMG", "arr": "SHA", "hours": [3, 3]},
    {"fid": 303, "dep": "HGH", "arr": "CTU", "hours": [1], "maybe": "x"},
]}
```

产物（8 行 / 812 字符）：

```text
from dcg.runtime import *
def run(_d):
 _d=_d['index']
 _o=[]
 _c=_o.append
 for R in _d:
  if R:
   _arr=_ix_get(R,'arr')
   _dep=_ix_get(R,'dep')
   _fid=_ix_get(R,'fid')
   hours=R['hours']
   total=_sm(hours)
   _c([_fid,_dep + '-' + _arr,hours,total,(total>4),{'from':_dep,'to':_arr,'span':total},{'count':1,'span':total,'deep':{'ids':[_fid, _fid + 1],'routes':[_dep, _arr]}},_rl([_fid,total,_ix_get(R,'maybe')])])
 _a=[r[6]for r in _o]
 _a2=[r['deep']for r in _a]
 return {'flight_id':[r[0]for r in _o],'route':[r[1]for r in _o],'hours':[r[2]for r in _o],'total':[r[3]for r in _o],'delayed':[r[4]for r in _o],'leg':[r[5]for r in _o],'stats':{'count':[r['count']for r in _a][0],'span':[r['span']for r in _a],'deep':{'ids':[r['ids']for r in _a2],'routes':[r['routes']for r in _a2]}},'pos':[r[7]for r in _o]}
```

结果：

```text
{"flight_id":[101,202,303],
 "route":["URC-PEK","KMG-SHA","HGH-CTU"],
 "hours":[[1,2,3],[3,3],[1]],
 "total":[6,6,1],
 "delayed":[true,true,false],
 "leg":[{"from":"URC","to":"PEK","span":6},
        {"from":"KMG","to":"SHA","span":6},
        {"from":"HGH","to":"CTU","span":1}],
 "stats":{"count":1,
          "span":[6,6,1],
          "deep":{"ids":[[101,102],[202,203],[303,304]],
                  "routes":[["URC","PEK"],["KMG","SHA"],["HGH","CTU"]]}},
 "pos":[[101,6,null],[202,6,null],[303,1,"x"]]}
```

这份 schema 里有四件事值得单独看：

| 形态 | 字段 | 结果 |
| --- | --- | --- |
| **顶层列** | `flight_id` / `route` / `total` / `delayed` | 每字段一列，`{字段: [每行的值]}` |
| **每行一份的具名对象** | `leg`（没标 `aggregate`） | 一列 具名行：`[{"from":...}, ...]`，不是拆成 `{"from":[...], ...}` |
| **列容器（`aggregate`）** | `stats` → `deep`（两层都标） | 逐层展开成子键列；`count` 是常量，只留一份；`span` 是整列 |
| **位置行** | `pos`（`type:"list"` + `fields`） | 每行一个位置 list，`extra` 缺键落 `null` 而不是缺位 |

`leg` 和 `stats` 的差别只在于你声明了什么，结构上无从推断——所以 `aggregate` 必须显式写：

```text
"leg":   {"type":"object","fields":{...}}                    -> 该键下是「一列具名行」，整列原样输出
"stats": {"type":"object","aggregate":true,"fields":{...}}   -> 该键下是「列容器」，每个子键各成一列
```

`_a` / `_a2` 是编译器为被多路复用的中间列自动起的短名：`stats` 那列要被 `count`、`span`
和 `deep` 三处引用，不提取就会把 `[r[6]for r in _o]` 原地展开三遍；`deep` 那层同理，进而在
`_a2` 里复用 `_a`。这类变量名不需要你操心，schema 里一个字都不用写。

关于 `aggregate` 的完整约束（只能配 `type:"object"` + `fields`，其它组合 `SchemaError`）
与四形状对照见 [DCG 接口实现细节文档.md §2.4](<DCG 接口实现细节文档.md>)。

---

## 2. schema 规格

完整规格见 [DCG 接口实现细节文档.md §2](<DCG 接口实现细节文档.md>)。顶层结构：

```json
{
  "name": "flight",
  "input":  {"source": "$"},
  "output": {"shape": "list", "before": null, "after": null, "trace": false},
  "out":    {"type": "object", "fields": {"...": {"type": "...", "value": "..."}}}
}
```

| 顶层键 | 必填 | 说明 |
| --- | --- | --- |
| `name` | ✅ | `[A-Za-z_][A-Za-z0-9_]*`，决定产物名 `gen/<name>.py` |
| `input.source` | | 行集合在输入数据里的路径，语法同字段 `value`：缺省 / `"$"` = 整份输入就是行集合；`"$.a.b"` = 产物开头 `_d=_d['a']['b']`（裸下标取，缺键直接 `KeyError`） |
| `output` | ✅ | 缺了直接报 `SchemaError: output: 缺少必填键`；`{}` 合法并被规范化 |
| `out` | ✅ | 必须是 `{"type":"object","fields":{...}}`，字段名同样是标识符 |

`output` 的规范化结果（`dcg.validate()` 会把 schema 转成这个形状）：

```text
output:{} 规范化后:        {'shape': 'list', 'before': None, 'after': None, 'trace': False}
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

`type` 只用于编译期分支（裸运算/内建调用能否内联、`$string` 能否内联、是否需要
undefined 守卫），不会插入任何运行时转换。要转换就在表达式里写出来（`int($.x)` /
`` `int($.0)` ``）。八种类型各自的产物与结果见 §2.8；`cython=True` 时局部量一律
`cdef object`，与声明的 type 无关（不改变语义）。

```text
no_coerce 产物: _o.append([R[0],R[1]])
run([[123,'abc']]): [[123, 'abc']]
```

### 2.3 `optional` / `default`：undefined 的两种处置

`optional: true` 表示"这一行可能没有这个值"，产物里会出现 `_U`（`dcg.runtime.UNDEF`）身份判断；
**不加 `optional` 时缺数据不是 undefined，而是真实抛错**。

分两种口径：位置式路径（`$.5.a.1`）、多段路径（`$.a.b`）与声明了具体 `type` 的 `value` 读值缺数据会抛 `KeyError`/`IndexError`；而表达式里的**单段**路径 `$.x` 与 `type: "any"` 的 `value` 读值缺数据是 undefined（按 miss 语义传播，可用 `$exists` 判断），再加 `default` 即可换成具体值。

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

若希望「缺值就给一个默认值」，在字段上加 `default`（JSON 常量，需与 `type` 相容，不能与 `fields` 同用）。产物里固化成一次 `_df(<expr>, <default>)`：值为 undefined 就换成默认值，不再生成 `None` 分支；值是 `null` 或其它正常值时原样返回。

```text
optional+default 产物: _o.append([_df(_ix_get(R,'n'),0),R['x']])
run([{'x': 8}]):        [[0, 8]]      # 缺 'n' -> 默认值 0
run([{'n': 5,'x': 8}]): [[5, 8]]
```

同一片段也可写成（`n` 缺值走默认值、`n` 有值原样输出）：

```text
run([{'x':8}]):        [[0, 8]]
run([{'n':5,'x':9}]):  [[5, 9]]
```

### 2.4 嵌套：只有一个键 `fields`

嵌套**必须显式声明**，而且只有一种写法：`fields`；具体形态由 `type` 决定：

| 意图 | 写法 | 运行期 |
|---|---|---|
| 嵌套具名对象 | `{"type":"object","fields":{...}}` | 带键的 dict |
| 嵌套位置行 | `{"type":"list","fields":{...}}` | 按字段声明顺序的 list，undefined 落 `None` 保位 |

`fields` 只能配 `type:"object"` 或 `type:"list"`，且不能是空对象。不带 `fields` 的列表字段**必须**用
`value` / `expr` 给出来源（如 `{"type":"list","value":"$.tags"}`）；只写 `{"type":"list"}` 不是合法
schema（`SchemaError: value/const/expr 必须恰好提供一个`）。

**数组不在这里声明**：列表值直接用 `value` / `expr` 给出（如 `{"type":"list","value":"$.tags"}`），
DCG 不逐元素重建数组（§2.5）。

```json
"out": {"type": "object", "fields": {
  "doc":  {"type": "object", "fields": {
    "name": {"type": "str",   "value": "$.0"},
    "age":  {"type": "int64", "value": "$.1"}
  }},
  "tags": {"type": "list", "value": "$.2"}
}}
```

```text
def run(_d):
 _o=[]
 for R in _d:
  _o.append([{'name':R[0],'age':R[1]},R[2]])
 return _o

run([['n1', 7, ['x','y']]]):   [[{'name': 'n1', 'age': 7}, ['x', 'y']]]
```

产物里对象可能写成 `_ob([...])`，也可能像上面这样直接写成字典字面量 `{'name':R[0],'age':R[1]}`：字段都能
静态证明不会 undefined 时发字面量，否则回退 `_ob` 由运行时丢键（位置行同理：可证时发列表字面量，否则
`_rl` 把 undefined 落成 `None` 保位）。**两种写法语义完全一致，只是快慢差异**，写 schema 时不需要关心。

嵌套 `fields` 是**独立作用域**：同层字段可按裸名互相引用（前向引用合法），内层可引用外层，
内层同名字段遮蔽外层，外层看不到内层名字；成环报在本层路径上。

内层引用外层：外层字段在产物里就是局部变量，内层直接写裸名即可：

```json
"fields": {
  "city":  {"type": "str",   "value": "$.city"},
  "total": {"type": "int64", "expr": "$sum($.hours)"},
  "doc": {"type": "object", "fields": {
    "doubled": {"type": "int64", "expr": "total * 2"},
    "label":   {"type": "str",   "expr": "city & '-' & $string(doubled)"}
  }}
}
```

```text
 city=R['city']
 total=_sm(_ix_get(R,'hours'))
 _t1=(total*2)
 _o.append([city,total,_ob([('doubled',_t1),('label',_cc((city+'-'),_S(_t1)))])])

run([{'city':'URC','hours':[1,2,3]}]):   [['URC', 6, {'doubled': 12, 'label': 'URC-12'}]]
```

内层同名遮蔽外层，外层不受影响（内层的 `city` 取 `$.alias`，外层的 `city` 仍是 `$.city`）：

```json
"fields": {
  "city": {"type": "str", "value": "$.city"},
  "doc": {"type": "object", "fields": {
    "city": {"type": "str", "value": "$.alias"},
    "seen": {"type": "str", "expr": "city"}
  }},
  "seen": {"type": "str", "expr": "city"}
}
```

```text
run([{'city':'URC','alias':'inner-city'}]):   [['URC', {'city': 'inner-city', 'seen': 'inner-city'}, 'URC']]
```

反过来，外层引用内层裸名不成立——内层名字不泄漏到外层（`doc.x` 与 `y: "x + 1"`）：

```text
SchemaError: out.fields.y.expr: 裸名 'x' 不是本次 schema 声明的字段；已声明: ['doc', 'y']（行内取值请写 $.x 或 $.路径）
```

成环报在本层路径上（下面 `hourE` 用裸名引用同层的 `hourA`/`hourB`）：

```text
def run(_d):
 _o=[]
 for R in _d:
  _t1=R['a']
  _t2=R['b']
  _o.append([_ob([('hourA',_t1),('hourB',_t2),('hourE',_ad(_t1,_t2))])])
 return _o

run([{'a':5,'b':7}]):   [[{'hourA': 5, 'hourB': 7, 'hourE': 12}]]

SchemaError: out.fields.doc.fields: 字段依赖成环: a -> b -> a
```

#### `type: "list"` + `fields`：把对象输出成位置行

对象字段默认用 `type:"object"` + `fields` 输出具名 dict；把 `type` 换成 `"list"` 后按字段声明顺序
输出 list（undefined 落 `None` 保位），用来表达「这一列里每一行是一个位置 list」的结构
（`{"doc": [[...], ...]}`）：

```json
"output": {"shape": "dict"},
"out": {"type": "object", "fields": {
  "doc": {"type": "list", "fields": {
    "hour":  {"type": "list",  "value": "$.5.a"},
    "hourA": {"type": "int64", "value": "$.5.a.1"}}}}}
```

```text
产物: _a=R[5]['a']; _o.append([_a,_a[1]])
run(rows):  {'doc': [[[0, 1, 2, [1, 2, 3]], 1], ...]}
```

`type:"list"` + `fields` 只是该层嵌套的输出表示（具名 dict / 位置 list），不改变 `output.shape`
的返回形态；位置行里 undefined 落成 `None`（保住位置），而不是从行里消失。

`fields` 只能配 `type:"object"` 或 `type:"list"`，与三键互斥：

```text
SchemaError: out.fields.who.fields: 只有 type 为 "object" 或 "list" 才能用 "fields"
```

### 2.5 数组：直接用 `value` / `expr` 给出

数组**不在 schema 里逐元素重建**，也不需要声明元素类型：整个列表由 `value` 引用或由 `expr` 算出，
然后原样传出。要换元素、换顺序、过滤，写在表达式里：

```json
"tags": {"type": "list", "value": "$.tags"}          // 原样引用输入里的列表
"top":  {"type": "list", "expr": "$sort($.tags)"}    // 表达式算出一个新列表
```

```text
产物: tags=R['tags']; top=sorted(...)                // 原样传递 / 直发内建，不逐元素重建
"tags": {"type":"list","items":{"type":"str"}}  -> SchemaError: out.fields.tags: 未知键: items
{"type":"int64","value":"$.0","fields":{...}}   -> SchemaError: out.fields.who.fields: 只有 type 为 "object" 或 "list" 才能用 "fields"
```

嵌套只剩 `fields` 一个键（`object` → 具名 dict，`list` → 位置 list），数组交给 `value` / `expr`。
schema 里没有 `items` 这个键，写它会报「未知键」。

### 2.6 schema 就是输出契约

v2 **没有**"算出来但不输出"的机制（没有隐藏字段或临时变量层）。
`out.fields` 里的每个字段都会出现在输出行里，顺序即位置语义。

未知键一律在 build/validate 期报 `SchemaError`，`path` 指向出错的那一层：

```text
SchemaError: out.fields.a: 未知键: ext, name
```

### 2.7 前向引用：计算按依赖序，输出按声明序

`out.fields` 的**声明顺序**决定输出行的字段顺序；**计算顺序**由字段依赖图决定（拓扑序，同层按声明序
tie-break）。因此后面的字段可以引用前面的字段，前面的字段也可以引用后面的字段：

```text
def run(_d):
 _o=[]
 for R in _d:
  if R:
   a=R['x']
   _o.append([(a*2),a])
 return _o

run([{'x':3}]):        [[6, 3]]
```

依赖成环会在 build 期报错（并给出环链）：

```text
SchemaError: out.fields: 字段依赖成环: a -> b -> a
```

### 2.8 八种 `type`：声明决定什么

`type` 八选一（`int64` / `int32` / `float64` / `str` / `bool` / `list` / `object` / `any`），必填。
它不转换值，但决定编译期怎么选路：数值类型可证 → 裸运算；`str` 可证 → 内建方法
（`code.upper()`）与 `+` 拼接；`$string` 对可证数值发 `str()`；`any` 一律走运行时 helper
并保留 undefined 传播；`object` 与 `list` 都能用 `fields`（具名对象 / 位置行），数组直接由 `value`/`expr` 给出（§2.5）。

八种类型放进同一份 schema 的实测（`output.shape="rows"`，输入一行
`{"n": 7, "small": 3, "f": 1.5, "s": "ab", "xs": [1, 2], "meta": {"city": "URC"}}`）：

| `type` | 字段写法 | 产物片段 | 结果 |
| --- | --- | --- | --- |
| `int64` | `{"type":"int64","value":"$.n"}` | `R['n']` | `7` |
| `int32` | `{"type":"int32","value":"$.small"}` | `R['small']` | `3` |
| `float64` | `{"type":"float64","expr":"$.f * 2"}` | `_ml(_ix_get(R,'f'),2)` | `3.0` |
| `str` | `{"type":"str","value":"$.s"}` | `R['s']` | `'ab'` |
| `str`（可证内联） | `{"type":"str","expr":"$uppercase(code)"}` | `code.upper()` | `'AB'` |
| `bool` | `{"type":"bool","expr":"$.n > 3"}` | `_gt(_ix_get(R,'n'),3)` | `True` |
| `list` | `{"type":"list","value":"$.xs"}` | `R['xs']` | `[1, 2]` |
| `object` | `{"type":"object","fields":{"city":...,"tag":...}}` | `{'city':R['meta']['city'],'tag':'ok'}` | `{'city': 'URC', 'tag': 'ok'}` |
| `any` | `{"type":"any","value":"$.zz"}` | `_ix_get(R,'zz')` | `undefined` |

完整 schema 与产物（输出字段顺序 = 声明顺序）：

```json
{"name":"types8","output":{"shape":"rows"},"out":{"type":"object","fields":{
  "i64":  {"type":"int64",  "value":"$.n"},
  "i32":  {"type":"int32",  "value":"$.small"},
  "f64":  {"type":"float64","expr": "$.f * 2"},
  "code": {"type":"str",    "value":"$.s"},
  "txt":  {"type":"str",    "expr": "$uppercase(code)"},
  "flag": {"type":"bool",   "expr": "$.n > 3"},
  "xs":   {"type":"list",   "value":"$.xs"},
  "meta": {"type":"object","fields":{
      "city":{"type":"str","value":"$.meta.city"},
      "tag": {"type":"str","const":"ok"}}},
  "miss": {"type":"any",    "value":"$.zz"}}}}
```

```text
from dcg.runtime import *
def run(_d):
 _o=[]
 for R in _d:
  if R:
   code=R['s']
   _o.append([R['n'],R['small'],_ml(_ix_get(R,'f'),2),code,code.upper(),_gt(_ix_get(R,'n'),3),R['xs'],{'city':R['meta']['city'],'tag':'ok'},_ix_get(R,'zz')])
 return [{k:r[i]for i,k in enumerate(['i64','i32','f64','code','txt','flag','xs','meta','miss'])}for r in _o]

run: [{'i64': 7, 'i32': 3, 'f64': 3.0, 'code': 'ab', 'txt': 'AB', 'flag': True,
       'xs': [1, 2], 'meta': {'city': 'URC', 'tag': 'ok'}, 'miss': undefined}]
```

同一个表达式，声明类型不同 → 产物不同（类型可证就发裸代码，否则走 helper）：

| 表达式 | 类型可证（如 `i64: int64`） | 不可证 / `any` |
| --- | --- | --- |
| `i64 + 1` | `(i64+1)` | `_ad(miss,1)` |
| `$string(i64)` | `str(i64)` | `_S(miss)` |
| `$uppercase(code)`（`code` 声明为 `str`） | `code.upper()` | `_up(_ix_get(R,'s'))` |
| `code & txt`（两字段都是 `str`） | `(code+txt)` | `_cc(_ix_get(R,'s'),_ix_get(R,'s'))` |
| `i64 = 7` | `(i64==7)` | `_eq(miss,7)` |
| `flag ? i64 : i32` | `(i64 if flag else i32)` | `(miss if flag else 1)`（条件表达式本身是裸的，分支各自按类型走） |

`value` 读值缺数据时，类型决定「抛错」还是「undefined」：

| `value` | 产物 | 缺数据时 |
| --- | --- | --- |
| `{"type":"int64","value":"$.n"}` | `R['n']` | 抛 `KeyError: 'n'` |
| `{"type":"int64","value":"$.m.x"}` | `R['m']['x']` | 抛 `KeyError` |
| `{"type":"any","value":"$.n"}` | `_ix_get(R,'n')` | `undefined`（按 miss 语义传播，可用 `$exists` 判断） |
| `{"type":"any","value":"$.m.x"}` | `_ix_get(_ix_get(R,'m'),'x')` | `undefined` |

加 `optional` / `default` 才生成兜底守卫（见 §2.3）：

```text
 a_opt=None if _t1 is _U else _t1
 _o.append([a_opt,_df(_ix_get(R,'n'),0),_ix_get(R,'zz')])

run([{'q':1}]):  [[None, 0, undefined]]
run([{'n':5}]):  [[5, 5, undefined]]
```

`cython=True` 只给局部量加 `cdef object`（所有类型一样，语义不变）：

```text
def run(_d):
 cdef object _o
 cdef object R
 cdef object code
 _o=[]
 ...
```

---

## 3. 表达式语法

### 3.1 符号表

| 写法 | 含义 | 例 / 实跑结果 |
| --- | --- | --- |
| `$` | 当前行（整个行对象） | `value: "$"` → `_o.append([R])` |
| `$.名` / `$.数字` | 路径取字段（`.`） | `$.sku` → `R['sku']`；`$.5.a.1` → `R[5]['a'][1]` |
| `名称` | 裸标识符 = 已声明字段 | `qty * price` → `(qty*price)` |
| `$.名[下标]` | 动态下标（下标是字段或表达式） | `$.a[$i]` → required 字段：`R['a'][i]`（越界/缺键/类型不符即真实异常）；`optional` / `default` 字段：`_ix_get(R['a'],i)`（落 undefined 再交给守卫或默认值） |
| `$.名(...)` | 函数调用（A / B / C 三类共 55 个，见 §3.5） | `$uppercase($.sku)` → `_up(_sku)`（实参类型不可证时走 helper；引用声明为 `str` 的字段且类型可证时发内建方法 `sku.upper()`）；`$sum($.xs)` → `_sm(_xs)` |
| `function($x){ ... }` | 函数字面量（只作为 B 类高阶函数的回调） | `$map($.xs, function($x){ $x * 2 })` → `_mp(_xs,lambda x,_i1:_ml(x,2))` |
| `` `...` `` | 反引号：内联 Python 表达式 | `` `str($.5.a.1) + str(hourB)` `` |
| `#` / `@` / `~>` / `..` / 正则 / `^` / `\|...\|` / `{k:v}` / `$eval` | 位置绑定、join 绑定、变换、范围、正则、排序、分组、运行期求值 | 一律 `Unsupported`（DCG 接口实现细节文档 §3.9） |

逐行函数白名单（DCG 接口实现细节文档 §3.4）摘要见 §3.5；三类函数共 55 个，全部在 `dcg/jx/policy.py` 里按名字归类。

### 3.2 编译期 IR（`dcg.compile_expr`）

```text
'$.hourA + hourB':     ('bin', '+', ('index', ('root', (1, 1)), ('lit', 'hourA', (1, 2)), (1, 2)), ('field', 'hourB', (1, 11)), (1, 9))
'$uppercase($.sku)':   ('call', '$uppercase', [('index', ('root', (1, 12)), ('lit', 'sku', (1, 13)), (1, 13))], (1, 11))
'`str($.5.a.1) + str(hourB)`': ('py', 'str($.5.a.1) + str(hourB)', ['5.a.1', 'hourB'], (1, 1))
```

自定义函数经 `Registry` 注册后可在表达式里使用，编译成 `rfunc` 节点（运行期直接调 Python 可调用对象）：

```text
reg = dcg.Registry(); reg.fn('$double', abs)
compile_expr('$double($.0)', registry=reg):
  ('rfunc', 'builtins.abs', [('index', ('root', (1, 9)), ('lit', 0, (1, 10)), (1, 10))], (1, 8))
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

`py{ ... }` 是**语句块**形式（可写多条语句、在 build 期展开成独立函数）；块内局部变量必须用
`let NAME = EXPR;` 声明，结尾 `return` 整个块的值：

```text
def _py0():
 __jx_0_t = 2
 return __jx_0_t * 3
...
产物: _o.append([_py0()])
run([{'x':1}]):        [[6]]
```

块内的裸 `$` 是**当前行本身**（`before`/`after` 钩子里则是那份全量数据）；`_pyN` 是模块级函数，
所以它走形参：

```text
expr: py{ let t = `$`; return t + 10 }
->
 def _py0(_p0):
  __jx_0_t = _p0
  return __jx_0_t + 10
 ...
  _o.append([_py0(R)])
```

`$x` 与 `$$` 仍然拒绝：行模式没有变量作用域，也没有整篇文档。
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
   _o.append([_sm(_ix_get(R,'xs')),_mp(_ix_get(R,'xs'),lambda x,_i1:_ml(x,2)),_fi(_ix_get(R,'xs'),lambda x,_i1:_gt(x,2)),_mx(_ix_get(R,'xs')),_jn(_mp(_ix_get(R,'xs'),lambda x,_i1:_S(x)),'+')])
 return [{k:r[i]for i,k in enumerate(['total','doubled','big','top','label'])}for r in _o]

run:                   [{'total': 6, 'doubled': [2, 4, 6], 'big': [3], 'top': 3, 'label': '1+2+3'}]
```

（`$.xs` 在 5 个字段里各被引用一次，生成码就**内联** `_ix_get(R,'xs')` 5 次、不落中转变量 ——
同名字段的复用只发生在「被兄弟字段**裸名**引用」的场合，见 §3.6。）

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

### 3.8 路径读值：行对象、动态下标与 undefined

| 场景 | 写法 | 产物 | 实跑 |
| --- | --- | --- | --- |
| 整行对象 | `value: "$"` | `_o.append([R])` | — |
| 动态下标（required） | `expr: "$.xs[i]"`（`i` 是另一字段） | `R['xs'][i]`（越界/缺键/类型不符 = 真实异常） | `run([{'xs':[10,20,30],'i':1}])` → `[[20, 1, 20]]` |
| 动态下标（optional / default） | 同上 + `optional: true` | `_t1=_ix_get(_ix_get(R,'xs'),i)` + `None if _t1 is _U` | 缺 `xs` → `None` |
| 缺失路径 | `value: "$.zz"`（`type: "any"`）或表达式里的 `$.zz` | `_ix_get(R,'zz')` | `[[{'a': 1}, False, undefined]]`（`$exists` → `False`） |

动态下标 + 行对象 + `$exists` 的完整产物：

```text
from dcg.runtime import *
def run(_d):
 _o=[]
 for R in _d:
  if R:
   i=R['i']
   _t1=_ix_get(_ix_get(R,'xs'),i)
   o=None if _t1 is _U else _t1
   _o.append([R['xs'][i],i,o])
 return _o

run([{'xs':[10,20,30],'i':1}]): [[20, 1, 20]]
```

---

## 4. 输出形态

`output.shape` 四选一，**编译期固定**（D8）。同一个 2 字段 schema + `[['A-1',2],['B-2',1]]`
（四个 `shape` 与 `before` / `after` / `trace` / `input.source` 的完整组合见 §4.4）：

| `shape` | 返回 | 实跑结果 |
| --- | --- | --- |
| `"list"`（默认） | 行列表（每行是位置列表） | `[['A-1', 2], ['B-2', 1]]` |
| `"dict"` | 列式：`{字段: [每行的值]}` | `{'sku': ['A-1', 'B-2'], 'qty': [2, 1]}` |
| `"index"` | `{行号: 行}` | `{0: ['A-1', 2], 1: ['B-2', 1]}` |
| `"rows"` | 行式：每行一个 `{字段: 值}` | `[{'sku': 'A-1', 'qty': 2}, {'sku': 'B-2', 'qty': 1}]` |

`shape="rows"` 的键序 = 字段声明序；它与 `"dict"` 的差别是**容器方向**：
`dict` 是 `{字段: 整列}`，`rows` 是 `[{字段: 值}, ...]`。
要注意 `list` 与 `rows` 的差别只有一条：行**有没有字段名**。

`shape="dict"` 的产物形状（注意尾部换了容器，行内仍是位置列表）：

```text
def run(_d):
 _o=[]
 _c=_o.append
 for R in _d:
  _c([R[0],R[1]])
 return {k:[r[i]for r in _o]for i,k in enumerate(['sku','qty'])}
```

`output` 只有四个键：`shape` / `before` / `after` / `trace`。**没有 `single`**——「只取第一行」
直接用 `run(...)[0]`，不值得为一个消费侧的方便在生成器里背一整套分支。

### 4.1 `before` / `after`：数据级前处理与后处理

两个键是**整体数据 → 新数据**的转换钩子，每个都**只求值一次**，不在循环里逐行跑：

```text
def run(_d):
  _d = <before>(_d)      # 1. 输入定位之后：拿到全量输入，返回的数据替换输入
  ...行循环与形状组装...
  return <after>(...)    # 2. 结果组装之后：拿到全量结果，返回的数据替换返回值
```

- `before` 收到的参数是**经 `input.source` 定位后的全量数据**；它返回的数据替换输入数据，
  之后的行循环遍历的就是它。
- `after` 收到的参数是**全量处理后的结果**（已经套用完 `shape`）；它返回的数据替换返回值。
- 两者都是**可选**的，不写就是原样透传。

值的写法：

| 写法 | 含义 | 例 |
| --- | --- | --- |
| `"= <JSONata 表达式>"` | 对全量数据求值一次（`=` 前缀可省） | `"= $sort($, function($a,$b){ $a[0] > $b[0] })"` |
| `"@pkg.mod.fn"` | 调用该函数一次，返回值当新数据 | `"@mycode.prep"` |

> 钩子是**数据级**的：整批求值一次，不逐行。需要按行筛选或改写时，用 `before` 整体
> 变换数据（例如 `"before": "= `[r for r in $ if r['qty'] > 0]`"`），见 §4.1 的组合示例。

#### `before` 之一：JSONata 表达式

schema `{"name":"b1","output":{"shape":"list","before":"= $.rows"},
"out":{"type":"object","fields":{"v":{"type":"int64","value":"$.0"}}}}`，
输入 `{"rows": [[9, 8], [7, 6]]}`：

```text
from dcg.runtime import *
def run(_d):
 _d=(lambda D:_ix_get(D,'rows'))(_d)
 _o=[]
 _c=_o.append
 for R in _d:
  _c([R[0]])
 return _o

run({"rows": [[9, 8], [7, 6]]}) -> [[9], [7]]
```

表达式里的**裸 `$` 就是全量数据本身**（不是某一行），所以 `$.rows` 取的是输入的 `rows` 键。
钩子被包成 `(lambda D: ...)(_d)`：形参叫 `D` 而不是 `R`，因为它拿到的不是行。

高阶函数同样可用——下面是按每行首元素排序（比较器返回 `true` 表示交换）：

```text
"before": "= $sort($.rows, function($a,$b){ $a[0] > $b[0] })"
->
 _d=(lambda D:_so(_ix_get(D,'rows'),lambda a,b:_gt(a[0],b[0])))(_d)
```

#### `before` 之二：`py{}` 代码块（多语句）

`py{ ... }` 在钩子里不再是「一个表达式」，而是一段真正的函数体：
`let NAME = EXPR;` 声明局部、`return EXPR` 返回新数据。块内用 `$.路径` 引用全量数据的字段，
每个 `$.链` 变成钩子函数的一个形参。

```text
{"name": "b2",
 "output": {"shape": "list",
            "before": "= py{ let rows = $.rows; let keep = [r for r in rows if r[0] > 1]; return keep }"},
 "out": {"type": "object", "fields": {"v": {"type": "int64", "value": "$.0"}}}}
```

```text
from dcg.runtime import *
def _py0(_p0):
 __jx_0_rows = _p0
 __jx_0_keep = [r for r in __jx_0_rows if r[0] > 1]
 return __jx_0_keep
def run(_d):
 _d=(lambda D:_py0(D['rows']))(_d)
 _o=[]
 _c=_o.append
 for R in _d:
  _c([R[0]])
 return _o

run({"rows": [[1, 0], [2, 0], [3, 0]]}) -> [[2], [3]]
```

**块内也可以直接写裸 `$`** —— 它就是那份全量数据本身，不必先 `let rows = $.rows`：

```text
{"name": "b2d",
 "output": {"shape": "list",
            "before": "= py{ let keep = [r for r in $ if r[0] > 1]; return keep }"},
 "out": {"type": "object", "fields": {"v": {"type": "int64", "value": "$.0"}}}}
```

```text
from dcg.runtime import *
def _py0(_p0):
 __jx_0_keep = [r for r in _p0 if r[0] > 1]
 return __jx_0_keep
def run(_d):
 _d=(lambda D:_py0(D))(_d)
 _o=[]
 _c=_o.append
 for R in _d:
  _c([R[0]])
 return _o

run([[1, 0], [2, 0], [3, 0]]) -> [[2], [3]]
```

`_pyN` 是模块级函数，看不到 `run()` 里的局部名，所以裸 `$` **走形参**：块里出现裸 `$` 时，
该块末尾多接一个形参，调用处传 `D`（行内块传 `R`）。没有裸 `$` 就不多接——产物里不会多出用不上的参数。
块里也支持反引号段，段内 `$` 与块内 `$` 同义：

```text
"before": "= py{ let keep = `[r for r in $ if r[0] > 1]`; return keep }"
```

py{} 的完整语法见 §3.7。也能直接用反引号段写单表达式：

```text
"before": "= `[r for r in $ if r]`"      # 丢掉 falsy 行（旧 filter:"$" 的等价写法）
->
 _d=(lambda D:[r for r in D if r])(_d)
```

#### `after`：结果组装之后再动一次

`after` 拿到的是**形状已套用**的全量结果，可以改写成任意返回结构：

```text
{"output": {"shape": "list",
            "after": "= {'n': $count($), 'rows': $}"}}
```

```text
from dcg.runtime import *
def run(_d):
 _o=[]
 _c=_o.append
 for R in _d:
  _c([R[0]])
 return (lambda D:_ob([('n',_ct(D)),('rows',D)]))(_o)

run([[1], [2], [3]]) -> {'n': 3, 'rows': [[1], [2], [3]]}
```

#### 两者共存：先 before，再行循环，最后 after

```text
from dcg.runtime import *
def run(_d):
 _d=(lambda D:[r for r in D if r[0] > 1])(_d)
 _o=[]
 _c=_o.append
 for R in _d:
  _c([R[0]])
 return (lambda D:{'n': len(D)})(_o)

run([[1], [2], [3]]) -> {'n': 2}
```

#### 外部函数形态

`before` / `after` 写 `@pkg.mod.fn` 时，产物头部按名 import 一次，**整批调用一次**：

```text
import importlib as _il
_hq0='json.dumps'
_hp0=_hq0.rfind('.')
_h0=getattr(_il.import_module(_hq0[:_hp0]),_hq0[_hp0+1:])
def run(_d):
 _d=_h0(_d)
 _o=[]
 ...
 return _o
```

传进来的是**整份数据**（`before` 是定位后的输入，`after` 是处理完的结果），返回值当新数据；
一个钩子只 import 一次，不进每行开销。

#### 常用写法对照

| 想要的效果 | 怎么写 |
| --- | --- |
| 丢掉 falsy 行 | `"before": "= `[r for r in $ if r]`"` |
| 按某个字段筛行 | `"before": "= `[r for r in $ if r['qty'] > 0]`"` |
| 不过滤 | 不写 `before` |
| 逐行改写行内容 | `"before": "= `[{'k': r[0] * 10} for r in $]`"` |
| 结果组装完再改写 | `"after": "= {'n': $count($), 'rows': $}"` |


### 4.2 `trace`

`trace=true` 额外生成一个**独立调试产物** `gen/<name>.trace.py`（全局 `TRACE` 追加
`(原始行, 输出行)`），正式产物与 `trace=false` **逐字节相同**：

```text
manifest:              {'name': 't', 'schema_sha1': '...', 'artifact': 'out\\gen\\t.py', 'trace_artifact': 'out\\gen\\t.trace.py', 'lines': 6, 'chars': 68, 'cython': False}
TRACE=[]
def run(_d):
 _o=[]
 _c=_o.append
 for R in _d:
  _c([1])
  TRACE.append((R,_o[-1]))
 return _o

run:                   [[1], [1]]
TRACE:                 [(['A-1'], [1]), (['B-2'], [1])]
```

上面打印的是 `out/gen/t.trace.py` 的源码；正式产物 `out/gen/t.py` 只有 68 字符、不含 `TRACE` 这两行，与 `trace=false` 时逐字节相同。

### 4.3 `input.source`

`input.source` 是**行集合在输入数据里的路径**，语法与字段 `value` 完全一样。写 `"$.index"`
就让产物在开头解包一层，写 `"$.a.b"` 就取 `data['a']['b']`：

```text
input.source="$.index" 产物: def run(_d): / _d=_d['index'] / ...
run({'index':[['a'],['b']]}): [['a'], ['b']]

input.source="$.a.b"   产物: def run(_d): / _d=_d['a']['b'] / ...
run({'a':{'b':[['a'],['b']]}}): [['a'], ['b']]
```

这条路径是 **schema 声明的固定结构**，所以产物用**裸下标**取、缺键直接 `KeyError`；
不写这个键（或写 `"$"`）则整份输入就是行集合，产物连这一行都不生成。

### 4.4 组合矩阵：同一份 schema，把四个开关组合跑全

§4.1–4.3 是逐个开关。这一节用一份更真实的订单 schema（`str` / `int64` / `float64` /
`list` / 嵌套 `object` 五类字段）把组合一次跑全。字段定义：

```json
{"name":"orders","output":{"shape":"rows"},"out":{"type":"object","fields":{
  "sku":   {"type":"str",    "value":"$.sku"},
  "qty":   {"type":"int64",  "value":"$.qty"},
  "amount":{"type":"float64","expr": "$.qty * $.price"},
  "tags":  {"type":"list",   "value":"$.tags"},
  "item":  {"type":"object","fields":{
      "sku":{"type":"str","value":"$.sku"},
      "qty":{"type":"int64","value":"$.qty"}}}}}}
```

两行输入（第二行 `qty=0`，用来验证 0 不被当成缺失；`tags` 一个非空一个空）：

```json
[{"sku":"A-1","qty":2,"price":3.5,"tags":["new"]},
 {"sku":"B-2","qty":0,"price":1.0,"tags":[]}]
```

| `output` 配置 | 返回表达式（产物尾部） | run 结果 |
| --- | --- | --- |
| `{"shape":"list"}` | `return _o` | `[['A-1', 2, 7.0, ['new'], {'sku': 'A-1', 'qty': 2}], ['B-2', 0, 0.0, [], {...}]]` |
| `{"shape":"rows"}` | `return [{k:r[i]for i,k in enumerate([...])}for r in _o]` | `[{'sku': 'A-1', 'qty': 2, 'amount': 7.0, 'tags': ['new'], 'item': {...}}, ...]` |
| `{"shape":"dict"}` | `return {'sku':[r[0]for r in _o],'qty':[r[1]for r in _o],...}` | `{'sku': ['A-1', 'B-2'], 'qty': [2, 0], 'amount': [7.0, 0.0], 'tags': [['new'], []], 'item': [...]}` |
| `{"shape":"index"}` | `return {i:r for i,r in enumerate(_o)}` | `{0: ['A-1', ...], 1: ['B-2', ...]}` |
| `{"shape":"rows","before":"= `[r for r in $ if r['qty'] > 0]`"}` | 首行多 `_d=(lambda D:...)(_d)` | `[{'sku': 'A-1', ...}]`（`qty=0` 那行在进循环前被剔掉） |
| `{"shape":"rows","trace":true}` | 正式产物不变；另生成 `orders.trace.py` | `TRACE` 里每行 `(原始行, 输出行)` |
| `{"shape":"rows"}` + `{"input":{"source":"$.index"}}` | 首行多 `_d=_d['index']` | 同 `rows`，但入参是 `{"index": [...]}` |

`shape="rows"` 的完整产物（10 行 / 303 字符；`_sku`/`_qty` 是顶层字段先算好、嵌套 `item`
直接复用，不重复读行）：

```text
from dcg.runtime import *
def run(_d):
 _o=[]
 _c=_o.append
 for R in _d:
  _qty=_ix_get(R,'qty')
  _sku=_ix_get(R,'sku')
  _c([_sku,_qty,_ml(_ix_get(R,'qty'),_ix_get(R,'price')),R['tags'],{'sku':_sku,'qty':_qty}])
 return [{k:r[i]for i,k in enumerate(['sku','qty','amount','tags','item'])}for r in _o]

run: [{'sku': 'A-1', 'qty': 2, 'amount': 7.0, 'tags': ['new'], 'item': {'sku': 'A-1', 'qty': 2}},
      {'sku': 'B-2', 'qty': 0, 'amount': 0.0, 'tags': [], 'item': {'sku': 'B-2', 'qty': 0}}]
```

`before` 只在行循环之前插一行（`_o=[]` 之前），循环体一个字符都不动：

```text
def run(_d):
 _d=(lambda D:[r for r in D if _ix_get(r,'qty') > 0])(_d)
 _o=[]
 _c=_o.append
 for R in _d:
  _c([...])
```

零行输入的容器差异：

| 形状 | `run([])` |
| --- | --- |
| `list` / `rows` | `[]` |
| `dict` | `{'sku': [], 'qty': [], 'amount': [], 'tags': [], 'item': []}`（每字段一个空列表） |
| `index` | `{}` |

校验期就会被拒绝的配置（全部实跑）：

| 配置 | 报错 |
| --- | --- |
| `{"shape":"table"}` | `SchemaError: output.shape: 必须是 ('list', 'dict', 'index', 'rows') 之一，得到 'table'` |
| `{"single":true}` | `SchemaError: output.single: "single" 不存在：输出形状由 shape 决定，没有「只取第一行」开关（要单行就直接读结果的第一项）` |
| `{"before":true}` | `SchemaError: output.before: before 不接受布尔值：它是「整体数据 → 新数据」的转换钩子，请写 JSONata 表达式（如 "= $map($, function($r){ $r })"）或外部函数（"@pkg.mod.fn"）` |
| `{"before":"`sku`"}` | `SchemaError: out.fields: 裸名 'sku' 不是本次 schema 声明的字段；已声明: ['v']（行内取值请写 $.sku 或 $.路径）`（反引号段里的裸名是字段引用） |
| `{"after":"=`nofunc`"}` | `SchemaError: out.fields: 裸名 'nofunc' 不是本次 schema 声明的字段；已声明: ['v']（行内取值请写 $.nofunc 或 $.路径）` |
| `{"after":"@f"}` | `SchemaError: output.after: 外部函数必须是 "@pkg.mod.fn" 形式，得到 '@f'` |
| `{"before":"= "}` | `SchemaError: output.before: "=" 之后缺少表达式，得到 '= '` |

三条正交规则：`shape` × `before` × `after` × `trace` 相互独立，全部编译期固定，
同一组合的产物逐字节相同（`schema_sha1` 相同）；求值顺序是
**`before` → 行循环 → `shape` 组装 → `after`**，前两者各一次、不逐行；
`trace` 只改调试产物，正式产物不变。

---

## 5. 能力边界（诚实清单）

| 能力 | 现状 | 证据 |
| --- | --- | --- |
| 深路径（**expr / 反引号 / `value` 全侧**） | ✅ **任意深度**（`$.a.b.c.d` → `R['a']['b']['c']['d']`），不要求用嵌套 `fields` | `compile_expr` 实跑 / `tests/` |
| 批量 / 高阶 / 非确定函数 | ✅ 55 个（A 46 / B 6 / C 4，`$sort` 同属 A/B） | §3.5、`examples/jx/12_funcs.jx` |
| 前向引用 / 依赖求值 | ✅ 声明顺序任意；计算按字段依赖拓扑序（同层按声明序 tie-break），输出仍按声明序；环在 build 期报 `SchemaError` | `tests/test_codegen_snapshot.py` |
| `before` / `after` / `trace` | ✅ 已接线（见 §4.1、§4.2） | 产物实跑 |
| `value` 路径的空段 | ⚠️ **空段被静默吞掉**：`"$..a"` / `"$.a."` / `"$.a..b"` 被静默当成 `"$.a"` / `"$.a"` / `"$.a.b"` | `dcg.validate` 实跑（见 §8） |
| 反引号内联 Python | ⚠️ **任意 Python 执行，不是沙箱**；build 期只 `ast.parse` 不执行 | `__import__("os").getcwd()` 通过编译 |
| `py{}` 多语句块 | ✅ 支持：语句序列定稿为 `run()` 之外的辅助函数；块内只读原始行、不参与字段依赖图 | DCG 接口实现细节文档 §3.8、`JSONata 语法.md` §11 |
| 运行期切换 shape | ❌ 不支持，要为不同形态**分别 build** 两个产物 | `output.shape` 编译期决定产物结构 |
| 数组元素重建 | ❌ schema 里不逐元素重建数组：列表由 `value`/`expr` 直接给出（§2.5） | 无额外声明键 |
| 类型化列输入（Cython） | 可选第二产物，不是默认 | `cython=True` 产物只多 `cdef object` 行 |
| 异构整篇文档（多段混排） | ❌ 一个 schema 只发一个行列表；要靠「多产物 + 一层组装」 | 见 §8 |
| 非确定函数结果 | ⚠️ 每次运行不同（产物字节可复现）；证据需注入固定时钟/随机源 | `rt.CLOCK` / `rt.RANDOM` |

**"不降级"是刻意的**：`Unsupported` 意味着"这份 schema 做不到"，而不是"悄悄换成慢的实现"。
任何不支持的结构或写法都会变成带位置的显式报错，绝不静默吞掉、静默丢行。

已知但**未修复**的 3 条表达式缺陷（语言语义对齐问题，不影响产物形态与性能口径；逐条复现与正确语义见 `JSONata 语法.md` §15.1）：

* `$.m[0]`（`m` 为 `null`）→ 抛 `TypeError`，按契约应返回 `null`（常量下标内联成 `None[0]`，不经 `_ix_get`）；
* `$round(2.5)` → 得 `2`，JSONata 语义应为 `3`（Python 内置是银行家舍入）；
* `$count('abc')` → 得 undefined，JSONata 语义应为 `1`（非数组按单个元素计）。

## 7. 公开 API 与仓库结构

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

### 7.1 目录结构

共 **411 个文件**（不含 `__pycache__` / `.pytest_cache` / `.git`）。`★` = 改动这里会影响验收结论。

```text
DCG/
├─ README.md                  本文档（§8 验证结果与剩余边界）
├─ DCG 接口实现细节文档.md         冻结接口：§1 IR、§2 schema、§3 表达式、§4 代码生成、§5–§7
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
│  └─ jx/                     JSONata 前端
│     ├─ lexer.py / parser.py   词法 / 语法
│     ├─ compiler.py            AST → IR
│     ├─ paths.py               路径段解析
│     ├─ policy.py              函数能力表与可编译性策略（A/B/C 与永久拒绝）
│     └─ registry.py            $自定义函数注册表
│
├─ tests/                     907 tests（898 passed / 9 skipped，其中 5 条是沙箱环境跳过）
│  ├─ test_schema.py          schema 校验契约（含四形状、取值三键互斥）
│  ├─ test_codegen_snapshot.py ★  产物逐字节快照 + 零冗余规则
│  ├─ test_e2e_flight.py      ★  flight 端到端：产物行为、四种 shape、optional/before/after、Cython 同源
│  ├─ test_business_cases.py   5 个真实业务用例：产物与实测基准（examples/baseline.json）逐值比对
│  ├─ test_sentinel_integrity.py  undefined 是独立对象（0 != undefined）
│  ├─ test_jx_conformance.py  冻结 conformance 例样（50 通过 / 3 拒绝，数据在 tests/fixtures/）
│  ├─ test_jx_examples.py     242 条 JSONata 例样逐条可执行
│  ├─ jx_corpus.py            语料解析 + 真实执行层（测试与文档生成共用）
│  ├─ workload.py             契约测试用的固定负载（行生成器 + 6 份 schema）
│  ├─ codegen_rules.py        六条零冗余规则的机器判定
│  ├─ fixtures/conformance.json  冻结一致性例样数据
│  ├─ gen_jsonata_manual.py   JSONata 语法.md 生成器（分类示例逐条校验）
│  ├─ test_jsonata_manual.py  JSONata 语法.md 与渲染结果逐字节一致
│  ├─ test_jx_policy.py       函数能力表（55 个）与拒绝策略（含"运行期绝不 import jsonata"）
│  ├─ test_jx_py.py           反引号内联 Python 表达式
│  ├─ test_jx_py_block.py     反引号内的多语句块
│  ├─ test_readme_hooks.py    本文 §4.1 的钩子产物逐行钉死
│  └─ test_readme_complex.py  本文 §1.1 复杂示例的产物与结果
│
├─ evidence/                  采集产物与验收
│  ├─ accept.py    ★          静态验收门（14 项）
│  ├─ accept_final.txt        历史终值（v2.3 前的 16 项门禁记录，保留作对照）
│  ├─ pytest_v21_final.txt    v2.1 测试终值（历史窗口的快照，非当前）
│  ├─ pytest_final_v2only.txt 历史 696 passed 冻结日志（当时版本）
│  ├─ v2_*.txt                历史探针原始输出（冻结，见 §8）
│  ├─ *_report.md             历史评审/验收/文档审计报告（归档）
│  └─ pytest_gate_trap/       证明 -q/-qq 会吞摘要行的对照实验
│
└─ examples/
   ├─ flight.json     ★        规范示例 schema（§1 / §3.4 的端到端用它）
   ├─ flight_rows.json         配套 10 条数据（{"index": [...]}）
   ├─ adcm_v2.json             多段嵌套 + 列容器 + 位置行的完整业务 schema（§1.1）
   ├─ legacy_cases.py ★       5 个真实业务用例的 v2 实现（判据：逐值等于实测结果）
   ├─ readme_complex.py        §1.1 复杂例的可执行副本
   ├─ readme_samples.py     ★  附录 A 全部样例的现场生成器（逐条可复现）
   ├─ flight_generated_{list,dict,index}.py / flight_generated_cython.pyx   实际产物
   ├─ baseline.json           上述 5 个用例的实测输出（冻结的期望值）
   ├─ readme_blocks.py         本文所有 Python 代码块的实跑副本
   └─ jx/01..15*.jx            242 条例样的可执行来源（15 个文件；01–15，含 12_funcs.jx）
```

> `readme_samples.py` 会把 `trace=true` 的产物写到 `examples/_tmp_trace_build/`（沙箱环境下
> `tempfile` 的子目录不可列举，所以落在仓库内），该目录是临时产物，可随时删除。

生成产物只依赖 `dcg.runtime`（"零第三方依赖"，不是单文件自包含）：

```python
from dcg.runtime import *      # 产物第一行
```

---

## 附录 A. 逐项样例（每个特性、每个取值各一份）

本节把 `input` / `output` / `out` 的**每个键、每个取值**各跑一遍，给出完整的 schema、
真实产物与真实运行结果。所有输出都由 `examples/readme_samples.py` 现场生成：
`python -X utf8 examples/readme_samples.py` 可以逐条复现。

### A.1 顶层三键

```json
{"name": "demo", "input": {...}, "output": {...}, "out": {...}}
```

| 键 | 必需 | 说明 |
|---|---|---|
| `name` | 是 | 模块名，必须是合法 Python 标识符（`^[A-Za-z_][A-Za-z0-9_]*$`） |
| `input` | 可选 | 目前只有 `source` 一个键，缺省即「整份输入就是行集合」 |
| `output` | 是 | `shape` / `before` / `after` / `trace` 四键，**其它键一律未知键报错** |
| `out` | 是 | `{"type": "object", "fields": {...}}`，`type` 只能是 `"object"` |

### A.2 `input.source`：五个取值

`source` 是一条从 `$` 出发的路径，决定**从入参的哪个位置取到行集合**。规范化结果是一个
段元组：标识符段 → 字符串，数字段 → **整数**（是下标，不是键名）。

**① 缺省（等于 `"$"`）——整份输入就是行集合**

```json
{"name": "in0", "input": {"source": "$"}, "output": {"shape": "list"},
 "out": {"type": "object", "fields": {"n": {"type": "int64", "value": "$.0"}}}}
```

```python
def run(_d):
 _o=[]
 _c=_o.append
 for R in _d:
  _c([R[0]])
 return _o
```

```python
run([[1], [2]])   # -> [[1], [2]]；没有 input.source 时连 _d= 那行都不会生成
```

**② `"$.index"`——从字典的一个键下取行集合**

```json
{"input": {"source": "$.index"}, "output": {"shape": "list"},
 "out": {"type": "object", "fields": {"n": {"type": "int64", "value": "$.0"}}}}
```

```python
def run(_d):
 _d=_d['index']
 _o=[]
 _c=_o.append
 for R in _d:
  _c([R[0]])
 return _o
```

```python
run({"index": [[1], [2]]})   # -> [[1], [2]]
```

**③ `"$.data.0.rows"`——数字段是整数下标**

```json
{"input": {"source": "$.data.0.rows"},
 "output": {"shape": "list"},
 "out": {"type": "object", "fields": {"n": {"type": "int64", "value": "$.0"}}}}
```

```python
def run(_d):
 _d=_d['data'][0]['rows']
 ...
```

```python
run({"data": [{"rows": [[1], [2]]}]})   # -> [[1], [2]]；产物里是 [0] 不是 ['0']
```

**④ `"$['x y'].k"`——方括号 + 引号段，可以写标识符写不了的键**

```python
def run(_d):
 _d=_d['x y']['k']
 ...
```

**⑤ 缺键的行为**：`input.source` 是**裸下标取**（`_d=_d['a']['b']`），缺键直接
`KeyError: 'a'` —— 它是 schema 声明的固定结构，不是逐行数据，不做 undefined 处理。

### A.3 `output.shape`：四个取值 + 缺省

同一份两字段 schema（`sku:str`、`n:int64`），同一份输入
`[{"sku": "A-1", "n": 2}, {"sku": "B-2", "n": 1}]`，只改 `shape`：

| `shape` | 产物 | 结果 |
|---|---|---|
| 缺省 / `"list"` | `return _o` | `[['A-1', 2], ['B-2', 1]]` |
| `"dict"` | `return {'sku':[r[0]for r in _o],'n':[r[1]for r in _o]}` | `{'sku': ['A-1', 'B-2'], 'n': [2, 1]}` |
| `"index"` | `return {i:r for i,r in enumerate(_o)}` | `{0: ['A-1', 2], 1: ['B-2', 1]}` |
| `"rows"` | `return [{k:r[i]for i,k in enumerate(['sku','n'])}for r in _o]` | `[{'sku': 'A-1', 'n': 2}, {'sku': 'B-2', 'n': 1}]` |

`"rows"` 完整产物（135 字符）：

```python
def run(_d):
 _o=[]
 _c=_o.append
 for R in _d:
  _c([R['sku'],R['n']])
 return [{k:r[i]for i,k in enumerate(['sku','n'])}for r in _o]
```

**零行输入的差别**（同一份 schema，喂 `[]`）：

| `shape` | 结果 |
|---|---|
| `list` / `rows` | `[]` |
| `dict` | `{'sku': [], 'n': []}` |
| `index` | `{}` |

> 单字段 + `dict`/`rows` 时产物会走「整行直接当值」的简化：`_c(R['n'])` + `return {'v':_o}`。
> 上面用两字段正是为了看到标准的列式产物。

### A.4 `output.before`：数据级前处理（五种写法）

`before` 在**字段取值之前**对「经 `input.source` 定位后的全量数据」求值一次，返回值替换
入参。产物里是一行 `_d=(lambda D:...)(_d)`。

**① JSONata 路径**

```json
{"input": {"source": "$"}, "output": {"shape": "list", "before": "= $.rows"},
 "out": {"type": "object", "fields": {"n": {"type": "int64", "value": "$.n"}}}}
```

```python
from dcg.runtime import *
def run(_d):
 _d=(lambda D:_ix_get(D,'rows'))(_d)
 _o=[]
 _c=_o.append
 for R in _d:
  _c([R['n']])
 return _o
```

```python
run({"rows": [{"n": 1}, {"n": 2}]})   # -> [[1], [2]]
```

**② JSONata 高阶函数（排序）**

```json
{"output": {"shape": "list", "before": "= $sort($, function($a,$b){ $a > $b })"},
 "out": {"type": "object", "fields": {"v": {"type": "int64", "value": "$"}}}}
```

```python
def run(_d):
 _d=(lambda D:_so(D,lambda a,b:_gt(a,b)))(_d)
 ...
```

```python
run([3, 1, 2])   # -> [[1], [2], [3]]
```

> 比较器返回真表示「`$a` 应排在 `$b` 之后」，所以 `$a > $b` 是**升序**。

**③ 反引号段（整份数据交给 Python）**

```json
{"output": {"shape": "list", "before": "= `[r for r in $ if r['n'] > 1]`"},
 "out": {"type": "object", "fields": {"n": {"type": "int64", "value": "$.n"}}}}
```

```python
def run(_d):
 _d=(lambda D:[r for r in D if r['n'] > 1])(_d)
 ...
```

```python
run([{"n": 1}, {"n": 2}, {"n": 3}])   # -> [[2], [3]]
```

段内的 `$` 就是**整份数据本身**（`before` 语境下不是当前行，此时还没有「行」）。

**④ `py{}` 多语句块，块内用 `$.rows`**

```json
{"output": {"shape": "list",
            "before": "= py{ let rows = [r for r in $.rows if r[0] > 1]; return rows }"},
 "out": {"type": "object", "fields": {"v": {"type": "int64", "value": "$.0"}}}}
```

```python
def _py0(_p0):
 __jx_0_rows = [r for r in _p0 if r[0] > 1]
 return __jx_0_rows
def run(_d):
 _d=(lambda D:_py0(D['rows']))(_d)
 _o=[]
 _c=_o.append
 for R in _d:
  _c([R[0]])
 return _o
```

数据必须是 `{"rows": [[1], [2], [3]]}` 这种形状 → `[[2], [3]]`。

**⑤ `py{}` 里写裸 `$`（= 全量数据本身）**

```json
{"output": {"shape": "list",
            "before": "= py{ let rows = [r for r in $ if r[0] > 1]; return rows }"},
 "out": {"type": "object", "fields": {"v": {"type": "int64", "value": "$.0"}}}}
```

```python
def _py0(_p0):
 __jx_0_rows = [r for r in _p0 if r[0] > 1]
 return __jx_0_rows
def run(_d):
 _d=(lambda D:_py0(D))(_d)
 ...
```

`run([[1], [2], [3]])` → `[[2], [3]]`；`$` 走了形参 `_p0`，所以调用处传的是 `D` 本身。

**⑥ 外部函数 `"@pkg.mod.fn"`**：产物头部会长出

```python
import importlib as _il
_hq0='json.dumps'
_hp0=_hq0.rfind('.')
_h0=getattr(_il.import_module(_hq0[:_hp0]),_hq0[_hp0+1:])
```

循环之前调用 `_d=_h0(_d)`，一个钩子只 `import` 一次。

### A.5 `output.after`：数据级后处理（四种写法）

`after` 在**形状组装之后**对整份结果求值一次，返回值就是 `run()` 的返回值。

**① JSONata 对象：再包一层**

```json
{"input": {"source": "$.rows"}, "output": {"shape": "list",
 "after": "= {'n': $count($), 'rows': $}"},
 "out": {"type": "object", "fields": {"n": {"type": "int64", "value": "$.n"}}}}
```

```python
def run(_d):
 _d=_d['rows']
 ...
 return (lambda D:_ob([('n',_ct(D)),('rows',D)]))(_o)
```

```python
run({"rows": [{"n": 1}, {"n": 2}]})   # -> {'n': 2, 'rows': [[1], [2]]}
```

**② 反引号段：整份结果交给标准库**

```json
{"output": {"shape": "list", "after": "= `json.dumps($)`"}, ...}
```

```python
import json
def run(_d):
 ...
 return (lambda D:json.dumps(D))(_o)
```

产物头部会自动补 `import json`（安全模块白名单里的模块按需前置）。

**③ `py{}`：加工整份结果**

```json
{"output": {"shape": "list", "after": "= py{ let col = [r[0] * 100 for r in $]; return col }"},
 "out": {"type": "object", "fields": {"n": {"type": "int64", "value": "$.n"}}}}
```

```python
def _py0(_p0):
 __jx_0_col = [r[0] * 100 for r in _p0]
 return __jx_0_col
def run(_d):
 ...
 return (lambda D:_py0(D))(_o)
```

```python
run({"rows": [{"n": 1}, {"n": 2}]})   # -> [100, 200]
```

**④ `dict` 形状下数行数要写 `$count($.v)`**

`shape` 为 `dict` 时 `$` 是整个列字典，`$count($)` 数的是**列数**，要数行数就取某一列：

```json
{"input": {"source": "$.batch.rows"},
 "output": {"shape": "dict", "after": "= {'rows': $count($.v), 'col': $}"},
 "out": {"type": "object", "fields": {"v": {"type": "int64", "value": "$.0"},
                                      "w": {"type": "int64", "value": "$.1"}}}}
```

```python
def run(_d):
 _d=_d['batch']['rows']
 _o=[]
 _c=_o.append
 for R in _d:
  _c([R[0],R[1]])
 return (lambda D:_ob([('rows',_ct(_ix_get(D,'v'))),('col',D)]))({'v':[r[0]for r in _o],'w':[r[1]for r in _o]})
```

```python
run({"batch": {"rows": [[3, 30], [1, 10], [2, 20]]}})
# -> {'rows': 3, 'col': {'v': [3, 1, 2], 'w': [30, 10, 20]}}
```

### A.6 `before` + `after` 共存

顺序固定：**`before` → 行循环 → `shape` 组装 → `after`**，各只求值一次。

```json
{"output": {"shape": "list", "before": "= `[r for r in $ if r[0] > 1]`",
            "after": "= {'n': $count($)}"},
 "out": {"type": "object", "fields": {"v": {"type": "int64", "value": "$.0"}}}}
```

```python
def run(_d):
 _d=(lambda D:[r for r in D if r[0] > 1])(_d)
 _o=[]
 _c=_o.append
 for R in _d:
  _c([R[0]])
 return (lambda D:_ob([('n',_ct(D))]))(_o)
```

```python
run([[1], [2], [3]])   # -> {'n': 2}
```

### A.7 `output.trace`：两个取值

| `trace` | 行为 |
|---|---|
| `false`（缺省） | 产物里没有任何 trace 代码 |
| `true` | `generate()` 的产物**逐字节不变**；只有 `build()` 额外写一个 `<name>.trace.py` |

`trace=true` 的 `build()` 产物（正式产物仍是 6 行 69 字符）：

```python
TRACE=[]
def run(_d):
 _o=[]
 _c=_o.append
 for R in _d:
  _c([1])
  TRACE.append((R,_o[-1]))
 return _o
```

manifest 里会多出 `trace_artifact`：

```python
{"name": "t", "schema_sha1": "310d2cc2...", "artifact": "build/gen/t.py",
 "trace_artifact": "build/gen/t.trace.py", "lines": 6, "chars": 69, "cython": false}
```

### A.8 `out.fields`：每个键、每个取值

#### A.8.1 `type`：八种声明

同一份 `shape="rows"` 的单字段 schema，只改 `type`：

| `type` | 产物里的取值 | 需要 runtime | 结果（输入 `{"v":7,"f":1.5,"s":"ab","b":true,"xs":[1,2],"meta":{"k":1},"x":3}`） |
|---|---|---|---|
| `int64` | `_c(R['v'])` | 否 | `[{'v': 7}]` |
| `int32` | `_c(R['v'])` | 否 | `[{'v': 7}]` |
| `float64` | `_c(R['f'])` | 否 | `[{'v': 1.5}]` |
| `str` | `_c(R['s'])` | 否 | `[{'v': 'ab'}]` |
| `bool` | `_c(R['b'])` | 否 | `[{'v': True}]` |
| `list` | `_c(R['xs'])` | 否 | `[{'v': [1, 2]}]` |
| `object` | `_c({'k':R['meta']['k']})` | 否 | `[{'v': {'k': 1}}]` |
| `any` | `_c(_ix_get(R,'x'))` | **是** | `[{'v': 3}]` |

**`type` 是声明不是转换**：上表没有任何 `int(...)`/`float(...)`/`str(...)`。声明成 `int64`
但数据里是字符串时，产物照样把字符串原样放进去 —— 需要转换就显式写 `expr`。

`any` 是唯一会引入 `from dcg.runtime import *` 的取值，因为它必须能表达 undefined。

#### A.8.2 `value` / `const` / `expr`：三选一

| 键 | 含义 | 例子 | 产物 |
|---|---|---|---|
| `value` | 从当前行取值（`$` 起头的路径） | `"value": "$.a"` | `R['a']` |
| `const` | 编译期常量，与数据无关 | `"const": "ok"` | `'ok'`，**零 runtime 依赖** |
| `expr` | JSONata 表达式 | `"expr": "qty * $.price"` | `_ml(qty,_ix_get(R,'price'))` |

恰好给一个；给两个或一个都不给都是 `SchemaError`。

`expr` 的例子（`qty` 被后面三个字段引用，于是落成局部名复用）：

```json
{"out": {"type": "object", "fields": {
  "qty":    {"type": "int64", "value": "$.qty"},
  "amount": {"type": "float64", "expr": "qty * $.price"},
  "big":    {"type": "bool", "expr": "qty > 1"},
  "label":  {"type": "str", "expr": "$string(qty) & '-item'"}}}}
```

```python
def run(_d):
 _o=[]
 _c=_o.append
 for R in _d:
  qty=R['qty']
  _c([qty,_ml(qty,_ix_get(R,'price')),(qty>1),(str(qty)+'-item')])
 return _o
```

```python
run([{"qty": 2, "price": 3.5}])   # -> [[2, 7.0, True, '2-item']]
```

#### A.8.3 `optional`：四个取值组合

| 声明 | 缺值时的行为 | 产物 |
|---|---|---|
| 都不写 | 具体 type → `KeyError`；`any` → undefined | `_c([R['a']])` / `_c([_ix_get(R,'a')])` |
| `"optional": true` | `None` | `_t1=_ix_get(R,'a')` + `a=None if _t1 is _U else _t1` |
| `"optional": true, "default": 0` | `0`（默认值优先） | `_c([_df(_ix_get(R,'a'),0)])` |
| 只写 `"default": 7` | `7` | `_c([_df(_ix_get(R,'zzz'),7)])` |

```python
# optional: true
def run(_d):
 _o=[]
 _c=_o.append
 for R in _d:
  _t1=_ix_get(R,'a')
  a=None if _t1 is _U else _t1
  _c([a])
 return _o

run([{"a": 1}, {}])   # -> [[1], [None]]
```

`declared optional` + `default` 时**不再生成 `None` 分支**：`run([{"a": 1}, {}])` → `[[1], [0]]`。

#### A.8.4 `fields`：两种嵌套

| 外层 type | 含义 | 产物 |
|---|---|---|
| `object` | 嵌套对象 = 具名 dict | `{'x':R['a'],'y':1}` |
| `list` | 位置行 = 列表，undefined 落 `None` 保位 | `_rl([R[0],_ix_get(R,1)])` |

```json
{"outer": {"type": "object", "fields": {
   "x": {"type": "int64", "value": "$.a"}, "y": {"type": "int64", "const": 1}}}}
# run([{"a": 5}]) -> [[{'x': 5, 'y': 1}]]

{"pair": {"type": "list", "fields": {
   "s": {"type": "str", "value": "$.0"},
   "a": {"type": "int64", "optional": true, "value": "$.1"}}}}
# run([["x", 1], ["y"]]) -> [[['x', 1]], [['y', None]]]
```

位置行里字段全可证时产物直接发列表字面量（`[R[0],R[1]]`），有可选/可能 undefined 的才发
`_rl([...])` 把 undefined 落成 `None`。

#### A.8.5 `aggregate`：两个取值（只在 `shape=dict` 下生效）

| 值 | 含义 | 例子 |
|---|---|---|
| 不写 | 该 object 字段是**一列具名行** | `{'sku': [...], 'item': [{'s':'A','n':1}, ...]}` |
| `true` | 逐子键拆成**子键列**（列容器） | `{'sku': [...], 'stat': {'n': [1, 2]}}` |

```json
{"output": {"shape": "dict"}, "out": {"type": "object", "fields": {
  "sku":  {"type": "str", "value": "$.sku"},
  "item": {"type": "object", "fields": {
             "s": {"type": "str", "value": "$.sku"},
             "n": {"type": "int64", "value": "$.n"}}}}}}
```

```python
def run(_d):
 _o=[]
 _c=_o.append
 for R in _d:
  _sku=_ix_get(R,'sku')
  _c([_sku,{'s':_sku,'n':R['n']}])
 return {'sku':[r[0]for r in _o],'item':[r[1]for r in _o]}
```

```python
run([{"sku": "A", "n": 1}, {"sku": "B", "n": 2}])
# -> {'sku': ['A', 'B'], 'item': [{'s': 'A', 'n': 1}, {'s': 'B', 'n': 2}]}
```

加上 `"aggregate": true` 后：

```python
def run(_d):
 _o=[]
 _c=_o.append
 for R in _d:
  _c([R['sku'],{'n':R['n']}])
 return {'sku':[r[0]for r in _o],'stat':{'n':[r['n']for r in [r[1]for r in _o]]}}
```

```python
# -> {'sku': ['A', 'B'], 'stat': {'n': [1, 2]}}
```

#### A.8.6 字段名：三个约束

| 约束 | 例子 | 报错 |
|---|---|---|
| 必须是合法标识符 | `{"a-b": ...}` | `out.fields: 必须是 [A-Za-z_][A-Za-z0-9_]*` |
| 不能是保留名 | `rows`/`row`/`r`/`out`/`ap`/`_` | `out.fields: 字段名 'rows' 与生成码内建名冲突，请改名` |
| 不能以下划线开头 | `{"_x": ...}` | 同上 |

### A.9 `generate()` 的两个关键字参数

| 参数 | 默认 | 效果 |
|---|---|---|
| `cython` | `False` | `True` 时产物多出 `cdef object` 声明，剥掉这些行后与纯 Python 产物**逐字节相同** |
| `inline_optional` | `True` | `False` 时 optional 守卫写成 `is_u(...)` 调用而不是 `is _U` |

```python
# cython=True
def run(_d):
 cdef object _o
 cdef object R
 cdef object _c
 _o=[]
 _c=_o.append
 for R in _d:
  _c([R['n']])
 return _o
```

```python
# inline_optional=False
  _t1=_ix_get(R,'a')
  a=None if is_u(_t1) else _t1
```

### A.10 不合格写法一览（全是 `SchemaError`，都带 `path`）

```
{"output": {"shape": "table"}}   -> output.shape: 必须是 ('list', 'dict', 'index', 'rows') 之一，得到 'table'
{"output": {"single": true}}     -> output.single: "single" 不存在：输出形状由 shape 决定，没有「只取第一行」开关
{"output": {"before": true}}     -> output.before: before 不接受布尔值：它是「整体数据 → 新数据」的转换钩子
{"output": {"before": "@f"}}     -> output.before: 外部函数必须是 "@pkg.mod.fn" 形式，得到 '@f'
{"output": {"after": "= "}}      -> output.after: "=" 之后缺少表达式，得到 '= '
{"output": {"foo": 1}}           -> output: 未知键: foo
{"input": {"datas": "$.index"}}  -> input: 未知键: datas
{"input": {"source": "index"}}   -> input.source: value 必须是 "$" 开头的路径（如 "$.a.b"），得到 'index'
字段名 "rows"                     -> out.fields: 字段名 'rows' 与生成码内建名冲突，请改名
字段名 "items"                    -> out.fields.t: 未知键: items
{"type":"list","fields":{} }     -> out.fields.<f>.fields: fields 不能为空
value 和 const 同时给             -> out.fields.a: value/const/expr 必须恰好提供一个（给了 2 个: value、const）
value/const/expr 一个都不给       -> out.fields.a: value/const/expr 必须恰好提供一个（一个都没给）
default 与 fields 共存            -> out.fields.o.default: 结构字段（fields）不能设 default
aggregate 用在 list 上            -> out.fields.o.aggregate: aggregate 只能用在 type 为 "object" 的字段上
before 里引用了不存在的裸名        -> out.fields: 裸名 'sku' 不是本次 schema 声明的字段；已声明: ['v']
name 不合法                       -> name: 必须是 [A-Za-z_][A-Za-z0-9_]*，得到 'bad name'
缺 out                           -> out: 缺少必填键
```

### A.11 组合样例：把开关全打开

**① 五开关全开**（`input.source` + `shape` + `before` + `after` + `trace`）

```json
{"name": "all5",
 "input": {"source": "$.batch.rows"},
 "output": {"shape": "dict",
            "before": "= $sort($, function($a,$b){ $a[0] > $b[0] })",
            "after":  "= {'rows': $count($.v), 'col': $}",
            "trace":  true},
 "out": {"type": "object", "fields": {"v": {"type": "int64", "value": "$.0"},
                                      "w": {"type": "int64", "value": "$.1"}}}}
```

```python
from dcg.runtime import *
def run(_d):
 _d=_d['batch']['rows']
 _d=(lambda D:_so(D,lambda a,b:_gt(a[0],b[0])))(_d)
 _o=[]
 _c=_o.append
 for R in _d:
  _c([R[0],R[1]])
 return (lambda D:_ob([('rows',_ct(_ix_get(D,'v'))),('col',D)]))({'v':[r[0]for r in _o],'w':[r[1]for r in _o]})
```

```python
run({"batch": {"rows": [[3, 30], [1, 10], [2, 20]]}})
# -> {'rows': 3, 'col': {'v': [1, 2, 3], 'w': [10, 20, 30]}}
```

四步在产物里依次可见：`_d=_d['batch']['rows']`（定位）→ `_d=(lambda D:...)`（before 排序）→
行循环 → `return (lambda D:...)`（shape 组装后再 after）。

**② `rows` 形状 + `before` 筛行 + `after` 加工**

```json
{"name": "combo2",
 "output": {"shape": "rows",
            "before": "= `[r for r in $ if r['qty'] > 0]`",
            "after":  "= {'count': $count($), 'items': $}"},
 "out": {"type": "object", "fields": {"sku": {"type": "str", "value": "$.sku"},
                                      "qty": {"type": "int64", "value": "$.qty"}}}}
```

```python
def run(_d):
 _d=(lambda D:[r for r in D if r['qty'] > 0])(_d)
 _o=[]
 _c=_o.append
 for R in _d:
  _c([R['sku'],R['qty']])
 return (lambda D:_ob([('count',_ct(D)),('items',D)]))([{k:r[i]for i,k in enumerate(['sku','qty'])}for r in _o])
```

```python
run([{"sku": "A", "qty": 2}, {"sku": "B", "qty": 0}])
# -> {'count': 1, 'items': [{'sku': 'A', 'qty': 2}]}
```

**③ 位置行 + 列的 `aggregate` + `input.source`**

见 §1.1 的完整例子（`flights` 与 `stats` 的对照）。

## 8. 验证结果与剩余边界

本次实现完成后，在干净工作树上复跑：

| 验证 | 结果 |
| --- | --- |
| 全量测试 `python -X utf8 -m pytest tests/ -p no:cacheprovider` | **898 passed / 9 skipped / 0 failed**（907 collected；9 skipped ＝ 4 条非确定 helper 对拍 + 5 条沙箱环境性跳过，正常机器上会照常执行） |
| JSONata 语料（`examples/jx/*.jx`） | **242 条**，逐条真实执行（解析/执行层 `tests/jx_corpus.py`） |
| 冻结 conformance（`tests/fixtures/conformance.json`） | **50/50 编译、3/3 拒绝** |
| 静态验收门禁 `python evidence/accept.py` | **14 PASS / 0 FAIL / 0 SKIP** |
| 产物字符预算 | **已取消**（2026-10-21 用户裁决；实测 `CAP=144 字符 / 9 行`、`M0=198 字符 / 10 行`，仅作参考，不参与判决） |
| 计时性能（比值 vs 手写参照） | **已按用户裁决删除**（m01094）：基准脚本、效率证书、效率门禁与本章性能小节均已移除；确定性约束（`compile()` 恰 1 次、同 schema 字节相同、Cython 同源、六条零冗余）由上述门禁与测试承担 |

仍存在的边界（是设计选择，不是未修缺陷）：

| # | 边界 | 现状 |
| --- | --- | --- |
| 1 | `value` 路径空段 | ⚠️ `"$..a"` / `"$.a."` / `"$.a..b"` 被静默当成 `"$.a"` / `"$.a"` / `"$.a.b"`（`dcg.validate` 实跑放行）；`expr` 侧同类写法被拒绝 |
| 2 | 数组元素重建 | ❌ schema 里不逐元素重建数组：列表由 `value`/`expr` 直接给出（§2.5） |
| 3 | 多段异构整篇文档 | ❌ 一个 schema 只发一个行列表；要「多产物 + 一层组装」（单组列表文档可用 `out.fields.<组>` 写成 `type:"list"` + `fields` 表达） |
| 4 | 运行期切换 shape | ❌ 形态编译期固定，要为不同形态分别 build |
| 5 | 反引号内联 Python | ⚠️ 任意 Python 执行，不是沙箱；build 期只 `ast.parse` |
| 6 | 非确定函数 | ⚠️ 结果不可复现；产物字节可复现；证据需注入固定时钟/随机源（§3.7） |
| 7 | `$eval` / 语法构造 | ❌ 永久拒绝，不降级（§3.5） |

另：`JSONata 语法.md` §15.1 列了 3 条**仍未修复**的表达式缺陷（`$.m[0]` 对 `null` 抛 `TypeError`、`$round(2.5)=2`、`$count('abc')=undefined`），修好时对应用例会从 §15.1 移到 §15.2 回归保护组。

发现新边界的流程：先按 [DCG 接口实现细节文档.md](<DCG 接口实现细节文档.md>) 判断是否属于设计边界，再登记，不静默调和。
