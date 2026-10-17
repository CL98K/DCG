# DCG v2 方案定稿

> 状态：待用户确认执行 · 依据 m00656/m00659/m00666 决策链
> 地点：`C:\Users\black\Desktop\DCG`

## 0. 已拍板的决策

| # | 决策 | 依据 |
|---|---|---|
| D1 | **不向前兼容**，可自由打破既有用法 | 用户明确 |
| D2 | **只优化运行期**，编译期开销不计 | "生成环境只会编译一次" |
| D3 | **产物即交付物**：编译一次 → 落盘可读 `.py`，可提交/diff | 同上 |
| D4 | **整工程不依赖 jsonata 包**；它只在 dev/CI 作测试探针 | 用户要求 |
| D5 | **`ext` 删除**，能力由反引号内联 Python 承接 | D6 |
| D6 | **符号层只保留 JSONata 一套语义**，反引号段是唯一扩展 | 消除 `$` 双语义 |
| D7 | **删除 `Template` 字符串模板 + `Engine`/`NodeTransformer` AST 改写** | D2/D3 推论 |
| D8 | 输出形态升格为**生成器选项**，编译期固定形状 | m00659 |
| D9 | 默认**零运行期依赖**（helper 内联），不设 `compact` 模式 | "编译一次"的正确形态 |
| D10 | 冻结 50 例一致性证据进仓库，jsonata 包消失后回归基线仍在 | D4 |

## 1. 架构

```
schema.json + 表达式
        │
        ├─ schema 校验器 ──────────► 非法形状 / build 期报错(带位置)
        │
        └─ jx 编译器(自研, 无依赖)
             lexer ─► parser(Pratt) ─► IR ─► codegen
                                            │
                                            ▼
                              gen/<name>.py   (纯 Python, 零依赖, 可读可 diff)
                                            │
                                     ┌──────┴──────┐
                                     ▼             ▼
                               CPython 直接跑   Cython 可选加速(同源+类型标注)
```

**旧链路整块删除**：`Template`(dcg.py:56)、`Engine`(dcg.py:856)、`tranfIFS`(dcg.py:339)、
`PY2Cython` 改名逻辑、`_field`/`FREE_MODE` 间接层。它们存在的目的只是"让通用引擎能对上号"，
直接生成最终代码后不再需要。

## 2. Schema 规格

```jsonc
{
  "name": "flight",
  "output": {
    "shape":  "list",     // "list" | "dict" | "index"
    "single": false,      // true = 单条展平
    "filter": true,       // 丢弃 falsy 行
    "post":   null,       // 整批后处理函数名
    "trace":  false       // 生成带打印的调试变体
  },
  "out": {
    "type": "object",
    "fields": {
      "id":    { "type": "int64",  "value": "$.ID" },
      "sum":   { "type": "int64",  "expr":  "$.a + `math.floor($.b * 1.2)`" },
      "grade": { "type": "string", "expr":  "$.score >= 90 ? 'A' : 'B'", "optional": true },
      "tag":   { "type": "string", "const": "flight" },
      "nested":{ "type": "object", "fields": { "x": { "type": "int64", "value": "$.x" } } }
    }
  }
}
```

**取值三键互斥（build 期强制）**

| 键 | 含义 | 生成的代码 |
|---|---|---|
| `value` | 直接取列（等价 JSONata 路径） | 局部名直接引用，零开销 |
| `const` | 字面量 | 内联常量 |
| `expr` | JSONata + 反引号扩展 | 编译表达式 |

**关键设计点**
- **取消"字符串前缀决定语义"**（现 `format.py:280-284` 的 `$`/`#` 闸门）。`"a"` 永远不是路径。
- `"fields"` **显式声明**嵌套，消掉 `format.py:509-510` 的"list 里出现 dict 就当嵌套"特例，
  连带修掉 `[{"字段": {描述}}]` 报裸 `KeyError: 'name'` 的问题。
- `"optional": true` 是唯一决定**是否生成 undefined 守卫**的开关。守卫实测成本 1.28×（纯守卫）
  / 2.09×（含 null 检查），只付在真正可选的字段上。
- 非法形状（三键冲突 / 缺 `type` / 期望 dict 给了 list）在**校验期**报错并给 JSON 路径。

## 3. 输出形态（承接 v1 的五个正交维度）

v1 的输出形状不是"决定"出来的，是**运行期算出来的**：`protocol=False` 实测走 `tuple(doc.items())`，
即每行都在运行期重新判断容器类型。v2 全部升格为编译期常量。

| v1 维度 | v1 实现 | v2 | 关键差别 |
|---|---|---|---|
| `protocol` | `STRUC.PROTOCOL` | `"shape":"list"\|"dict"` | 形状编译期固定 |
| `single` | `STRUC.SINGLE` | `"single":true` | 编译期展开，无运行期分支 |
| `filter` | `default_filter` 模板 | `"filter":true` | 生成期决定；实测 v1 多数情况形状已由 `isConts` 决定 |
| `func` | `default_func` 模板 | `"post":"pkg.fn"` | **只对整批调一次**，不进热路径 |
| `debug` | `Template.debug` | `"trace":true` → 独立产物 | 不污染热路径 |
| `index` | `Template.index`（**坏的**：用 `chain` 从未 import，实测 `NameError`） | `"shape":"index"` | 有测试覆盖 |

**边界情形**
1. **undefined 键的处置是编译期常量**：`optional:true` 的字段生成 `if _v is not _U: ...`；必然存在的字段不留判断。
2. `single` 的两种旧解释（展平单条 / 保留原始结构）彻底分开，后者就是默认 `shape` 行为。
3. **`filter` 与 `single` 顺序固定**：先 filter 后 single，写进文档（v1 靠模板拼接顺序隐式决定）。
4. 形状不可运行期切换；要 list 和 dict 两种输出就 build 两个产物。

## 4. 表达式层：JSONata + 反引号内联 Python

反引号对是词法边界，JSONata 从不使用 `` ` ``，因此零歧义。

| 形态 | 写法 | 用途 |
|---|---|---|
| 单表达式 | `` `math.floor($.b * 1.2)` `` | 一行代码，无仪式 |
| 多语句块 | `` py{ let t = $.a * 1.2; return int(t) } `` | 几行逻辑，编译成内联函数 |
| 复用函数 | `Registry.fn("$myfn", callable)` → `$myfn(...)` | 多处复用/需单测 |

**三条硬规则**
1. **`$` 是唯一的字段/变量入口**。反引号内容不暴露裸字段名 —— 否则 `int`/`str`/`math` 与字段名静默撞车。
   `$.x` → 生成的局部名；`$` 单独 = 当前行；`$v` = JSONata 变量。
2. **反引号内必须是 Python 表达式**（`ast.parse(seg, "eval")` 校验），不能声明新名字、不能 `import`；多语句走 `py{}`。
3. **build 期报错带字符偏移**，不留到运行期 `SyntaxError`。

**边界**：反引号内不能再含反引号；反引号段**不参与 undefined 语义**（拿到的是已求值参数，`None` 就是 `None`）。

**必须显式拒绝（build 期 `Unsupported` + 位置）**：`..` 区间、正则字面量、`function(){}` 高阶字面量，
以及需整批/非确定的 `$sum/$max/$min/$average/$sort/$reverse/$distinct/$reduce/$each/$sift/$keys/$values/$lookup/$merge/$spread/$now/$millis/$random/$eval/$map/$filter/$single`。绝不静默降级。

## 5. 生成码规则（运行时性能的全部着力点）

```python
# gen/flight.py —— 自动生成，请勿手改
def run(rows):
    out = []
    ap = out.append
    for r in rows:
        _id, _a, _b = r[0], r[3], r[4]      # 全局行首解包（消重复下标）
        out_ = [int(_id), _add(_a, math.floor(_mul(_b, 1.2)))]
        ap(out_)
    return out
```

1. **全局行首解包**：同一行被多个字段引用的下标只取一次 → 更快且代码更短
2. **类型约束编译期分支**：`type=int64` 直接生成 `int(...)`/`cdef long`，删掉所有 isinstance 分派链
3. **不生成恒等语句**（消灭 `datas = datas`）
4. **不生成只为 return 的中转变量**（消灭 `cdef list buffer_x`）
5. **行级 try/except 移出循环**；`SAFE_MODE` 的安全索引在 build 期定路径
6. **每个运算符一个专用 helper**（`cdef inline` 可选）：实测派发脚手架占守卫成本 89%，专用函数后守卫净成本仅 1.28×
7. **输出容器预分配**（Cython 路径）；字段固定时直接构造不留中间 list
8. **确定性序列化**：变量名由 schema sha1 派生、字段顺序固定 → **产物逐字节可复现**

## 6. 保证项

| 保证 | 说明 |
|---|---|
| 零运行期依赖 | 生成码 `import` 仅限标准库与用户注册函数 |
| 编译期全量报错 | 语法/形状/不支持特性全部 build 期暴露，带位置 |
| 语义正确性 | 与 jsonata-python 对照：核心 26 + 边界 24 冻结进仓库全绿 |
| 产物可复现 | 同 schema → 逐字节相同 |
| 无静默降级 | 不支持的构造一律报错 |
| 安全性（如实） | 反引号 = 任意 Python 执行，**不是沙箱**；build 期不执行代码；沙箱需进程级手段 |

## 7. 符号表与冲突矩阵

| 符号 | 含义 | 冲突处置 |
|---|---|---|
| `$` | JSONata 当前上下文；`$foo()` 内置函数 | 全语言统一，无第二语义 |
| `$.x` | 字段访问 | 唯一字段入口（含 Python 段） |
| `#` / `@` | JSONata 位置绑定 / join 绑定 | 保留 JSONata 原生语义，无 DCG 扩展 |
| `` ` ` `` | 内联 Python | 唯一扩展，词法级隔离 |

## 8. 文件结构

```
dcg/
  __init__.py      公开 API
  schema.py        校验 + 三键互斥 + 位置报错
  ir.py            IR 定义
  codegen.py       生成器（性能主战场）
  runtime.py       helper（生成码内联来源）
  build.py         build 一次 → gen/*.py + manifest.json
  migrate.py       旧配置 → 新 schema（best-effort）
  jx/
    lexer.py       正则 TOKEN（含反引号 token）
    parser.py      Pratt（BP 表按 JSONata 1.8）
    compiler.py    JSONata AST → Python 源码
    helpers.py     undefined 六条语义
    policy.py      Unsupported 白/黑名单
    registry.py    函数注册表
tests/             §10
bench/             bench_row.py / bench_codegen.py / bench_overhead.py
README.md
```

## 9. 实施顺序

| 线 | 内容 | 文件域 |
|---|---|---|
| **A｜引擎重写** | schema + IR + codegen + build/落盘 + 特化 | `dcg/*.py`（除 jx/） |
| **B｜jx 编译器** | lexer/parser/compiler/registry/policy/helpers | `dcg/jx/*` |
| **C｜Lead** | 契约、测试矩阵、基准方法学、README、端到端验收 | `tests/`、`bench/`、`README.md`、契约 |

M0 先固化基线（200k 行 × 8 字段，profiling 分解：对象构造 / 下标访问 / 类型分派 / try 包装各占多少），
**拿到分解数据再定优化目标和里程碑**。

## 10. 测试与基准矩阵

| 文件 | 覆盖 |
|---|---|
| `tests/test_schema.py` | 合法/非法形状、三键互斥、位置报错 |
| `tests/test_jx_conformance.py` | 50 例冻结证据（有 jsonata 时额外交叉校验） |
| `tests/test_jx_py.py` | 反引号：表达式/多语句/未定义名/裸名拒绝/偏移正确 |
| `tests/test_jx_policy.py` | `Unsupported` 清单逐条必抛 |
| `tests/test_codegen_snapshot.py` | 零恒等赋值、零多余中转、字节级可复现 |
| `tests/test_e2e_flight.py` | 民航 ACDM 端到端 |
| `bench/bench_row.py` | 200k×8：CPython / Cython / 手写参考 |
| `bench/bench_codegen.py` | 生成码行数、字符数、中间变量数 |
| `bench/bench_overhead.py` | 守卫/类型分派/异常包装逐项成本 |

**验收指标**（method 先钉死：同机同数据、预热后取中位数、测 3 次取最好）
主指标 `µs/行`、`行/秒`；三方对照（手写参考 / v1 / v2）。
目标倍数**等 M0 profiling 后再定**，不预先承诺。

## 11. 明确不做

自创第二套 DSL · 运行期解释 JSONata（慢 52–75×）· 兼容层 · 通用引擎+运行期分支 · 静默降级 · 语言层沙箱

## 12. M0 基线实测数据（v1，2026-10-17）

环境：Python 3.13.3 / Cython 3.1.1 / Windows amd64 · N=200000 行 × 8 字段 · 预热后取 3 次最小值
工作负载：`$.5.a` / `$.5.a.1` / `$.5.a.2` / `$.2` + 3 个 ext 表达式（对应 format.py:589-603 的仓内唯一完整示例）
行形状：`(code:str, a:int, b:int, c:int, d:int, payload:{'a':[4]})`

### 12.1 v1 实测

| 指标 | 值 |
|---|---|
| 运行期 | **0.480 µs/行 · 2,083,896 行/秒**（96.0 ms / 200k） |
| 构建期 | `Format.parse`(解析+编译) = **3.05 ms** |
| 输出形状 | `dict -> {'doc': [[...], ...]}` |
| Cython 加速比（v1 自报路径） | 1.25×（早期 20k 行 × 8 字段实测） |

### 12.2 成本的微分解（手写参照，bench/decompose.py）

| 变体 | µs/行 | 相对 |
|---|---|---|
| A 只解包一次 payload | 0.457 | **1.00×** |
| B 每字段重复 `r[5]['a']` | 0.608 | **1.33×** |
| C 无输出对象（只算） | 0.235 | 0.51× |
| D 只构造输出 | 0.288 | 0.63× |
| E 预分配代替 append | 0.487 | 1.06× |
| F 输出用 tuple 行 | 0.461 | 1.01× |

### 12.3 Cython 天花板（bench/cy_ceiling2.py）

| 变体 | µs/行 | 相对纯 Python |
|---|---|---|
| py 手写 row-object | 0.472 | 1.00× |
| cy row-object（list 行） | 0.419 | 1.13× |
| cy row-object（tuple 行） | 0.401 | 1.18× |
| **cy row-object 不构造输出** | **0.090** | — |
| py typed columns（numpy int32） | 0.602 | 0.78× |
| **cy typed columns** | **0.284** | **1.66×** |

### 12.4 v1 实际生成码（bench/capture_src.py 截获，445 字符）

```python
def _inline_call_CAP(datas, **kwargs):
    datas = datas['index']
    buffer_vhyivxjgdz = {'doc': []}
    doc_vhyivxjgdz = buffer_vhyivxjgdz['doc']
    doc_vhyivxjgdz_append = doc_vhyivxjgdz.append
    for data in datas:
        VAR_EWURWO_1 = data[5]['a']
        hourB = VAR_EWURWO_1[2]
        hourA = VAR_EWURWO_1[1]
        doc_vhyivxjgdz_append([hourA, hourB, str(VAR_EWURWO_1[1]) + str(hourB), hourA + hourB])
    return buffer_vhyivxjgdz
```

**并截获到一个多余产物**：`Format.parse` 会额外 `compile` 一份 118 字符的**空桩**（`datas = None; buffer_x = None; return buffer_x`），即每次 parse 至少编译两次。

### 12.5 M0 结论 —— 直接约束 §5 的效率承诺

1. **v1 已接近纯 Python 手写极限**：0.480 vs 0.457 = v1 只慢 5%，且 v1 **已经**做了"payload 只取一次"的正确优化（截获源码可见）。→ 纯 Python 代码生成的性能空间 ≤5%。
2. **真正的成本是输出对象构造，占 78%**（不构造输出则 0.090 µs/行）。这部分**不可由 codegen 消除** —— 只要还产出 Python list/dict 就必须付。
3. **Cython 的真实增益是 1.13–1.18×**（row-object 输入），不是宣传口径的更大幅度；拿满 **1.66×** 需**类型化列输入**（numpy int32 + memoryview），这是**能力变化**（输入从行对象变列数组），必须单独决策。
4. **最有价值的算法级修复是消除字段重复访问**（1.33×）—— 即 §5 规则 1「全局行首解包」。这是唯一既缩短代码又提性能的改动。
5. **构建期 3.05 ms/次**（含那份多余空桩编译），编译一次的场景下不重要；但多余空桩必须删。

**因此 §5 的目标修订为**：
- 产物字符数：445 → **≤180**（可验证）
- 运行期：纯 Python **≥0.457 µs/行**（达到手写极限）；Cython row-object **≥0.419 µs/行**
- 类型化列输入作为**可选第二产物**（`input_layout: "columns"`），拿满 1.66×，默认不开

---

## 13. 实施期裁决日志（Lead，2026-10-17）

按时间倒序。**契约正文（CONTRACT.md）为准**，本节只记「为什么这么裁」与「谁改了什么」。

### 13.1 §5b —— v1/v2 同名冲突（P0，独立验证发现）

- **现象**：v1 引擎是模块 `dcg.py`，v2 是包 `dcg/`，常规包遮蔽同名模块 →
  `format.py:13 from dcg import ERROR, ...` 报 `ImportError`。v1 基线与回退路径同时失效，
  §7 的「同进程三方对照」直接无法执行。
- **裁决 A′**：`bench/_v1bridge.py` 把 `sys.modules["dcg"]` 临时钉到 `dcg.py`，导入 `format` 后换回 v2 包。
  实测两者同进程共存。**不**复制 v1 源码（96KB 会漂移）、**不**在 `dcg/__init__.py` re-export v1 符号
  （污染 v2 公开面，且解决不了对照）、**不**强制子进程（白牺牲同进程要求）。
- **纪律**：需要 v1 的脚本必须**先** `from _v1bridge import load_v1_engine`，禁止各自再写一份。
- verifier-dev 已把自己的重复实现删除并合流；`tests/test_v1bridge_collision.py` 提供自动化保证。

### 13.2 §3.6 —— 下标：`$ix` call 节点废弃

- **现象**：动态下标编成 `("call","$ix",[base,idx])`，而 `$ix` 不在 §1 节点表、不在 §3.4 白名单。
- **我犯的错（必须记录）**：我据「`_ix` = `$indexOf`」推断这会让 `a[b]` 静默变成字符串查找。
  **推断不成立** —— 实测 `dcg/codegen.py:147/189-190` 路由到 `_ix_get`，从未路由到 `_ix`。
  parser-dev 当场更正我，我核对源码后确认它是对的。
- **教训**：`_ix` 与 `_ix_get` 差一字符、语义完全不同 → 这是**命名歧义**，不是 bug。
  我把它升级成「静默错值」，是**过度推断**。代价：给三个队友发了一条错误结论，
  已逐一更正并写进契约正文，防止它被传播成"已知 bug"。
- **裁决**：动态下标统一 `("index", base, idx, loc)`；`idx` 非 `("lit",...)` 时用 `_ix_get`。
  `_chain` 里常量段本来就走 `("index",...)`，改完**整条链只剩一种节点**。

### 13.3 §3.7 —— 反引号段必须支持数字段（打掉 M0/CAP 的命脉）

- **现象**：`_PY_FIELD = re.compile(r"\$\.([A-Za-z_][A-Za-z0-9_]*)")` 只认 `$.ident`。实测
  `analyze_py_segment('str($.5.a.1) + str(hourB)')` → `Unsupported`（段内偏移 5）。
  而 v1 的 `ext` 正是该写法（§7 与 `bench/capture_src.py`）→ **迁移后 M0/CAP 根本跑不起来**。
- **裁决**：链 = `$.` + （标识符 | 十进制下标）段序列；`free_field_names` 元素 = 完整链（`"5.a.1"`）；
  新增 **`dcg/jx/paths.py::parse_path`** 作**唯一权威**解析，JSONata 路径与反引号段共用，禁止两套。
  `parse_path` 只负责解析；「符号段 ≥2 → 拒绝」是**路径编译层**的判据（§6.1），
  否则 `` `$.5.a.1.b` `` 的报错会退化成"不是合法链"而非"深路径不支持"。

### 13.4 §1/§6.1 —— 裸名 = 字段；`$.a.b` 必须拒绝

- **实测**：`a + b` → `("bin","+",("field","a"),("field","b"))`、`foo` → `("field","foo")`。
  §1 旧措辞「裸标识符一律不是字段」与实测冲突。
- **裁决：以实测为准，保留裸名=字段**。理由：M0/CAP 的 ext 分支就是 `hourA + hourB`、
  `hour[2] + 123`，引用**本 schema 先声明的字段**；改掉则 M0/CAP 跑不起来。
  §1 旧措辞**废弃**。附加要求：schema 中不存在的裸名必须 **build 期报错**并列出可用字段名。
- **实测违约**：`$.a.b` → `index(index(root,'a'),'b')`、`$.a.b.c` 三层，**均未被拒绝**，与 §6.1 冲突。
- **裁决**：从 `root` 起，链条中**符号段 ≥2** 即拒绝。`$.5.a.1`（符号段仅 `a`）允许；
  `$.5.a.1.b` 与 `$.a.b` 拒绝。**注意区分数值段与符号段**（`$.5.a` 的 `5` 是数值下标，不计入）。

### 13.5 归属与纪律（一次真实的写入域越界）

- parser-dev **如实承认** `dcg/jx/compiler.py` 的 `_flatten`/`_apply_segs`/`_chain` 是它越界写的
  （动机：写 codegen 时发现 `$.5.a.1` 编不出来，就直接改了 jx）。
- **裁决**：不追究本次，**不回退**该代码（`$.5.a.1` 常量下标链是 M0/CAP 命脉，回退造成真实损失），
  但改动清单须交 jx-dev 纳入其实现；**自此不再写 `dcg/jx/**`**。
- **拒绝"授权 5 分钟改一下"**：便利不值得破坏写入域一致性。本次已因并发写导致
  「我读到的源码与实测结果不一致」（compiler.py 在我 grep 与 read 之间被改），排查成本远超 5 分钟。
- parser-dev 否认产出 `bench/_workload.py`(11226B) 与 `bench/codegen_rules.py`(14609B)，
  已向 verifier-dev 求证归属（两者不在其 task-3 writeScopes 内）。
- **教训**：`dcg/codegen.py` 半成品期间 `__all__` 声明了尚未定义的 `build`
  → `from dcg.codegen import build` 报 `ImportError`。**半成品不得放进 import 得通的公开面。**

### 13.6 队友独立发现（采纳）

- verifier-dev：v1 规则判定 **rule3 FAIL 4 处**（datas 中转 / buffer 中转 / append 别名 /
  只被再解包的容器）、**rule8 FAIL**（`cctpnrvgwn` 随机名）→ 实证 v1 违反 §4.2 规则④⑥。
- verifier-dev：`cy_ceiling2` 实测 **cy list-rows 0.416 慢于 py 0.390**，与 v1 冻结基线
  「cy 相对 py 约 -18%」同向 → 独立复现「Cython 不自动更快」，v2 不得盲信 Cython。
- verifier-dev：`pytest` 用模块级 `pytest.skip(allow_module_level=True)` 会产生 **exit 5**
  （"no tests collected"），与"测试文件损坏"不可区分 → 已改为 `@pytest.mark.skipif`。
  这是个值得留痕的 pytest 陷阱。
- **v1 运行期波动 0.480 → 0.588 µs/行（+22%，同机同代码）** → 印证 §7「取 3 次最小值」的必要性。

