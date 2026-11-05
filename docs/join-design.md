# DCG Join 能力设计（RFC v0.1）

> 状态：**草案，待用户确认第 11 节 4 个未决项后冻结为 v1.0**
> 目标版本：**2.3.0**（新增 schema 顶层键与 `run` 可选参数，向后兼容）
> 决策来源：用户 2026-11-05 指定「全部类型都要」（left / inner / semi / anti / fanout / fanout_left / 链式）与「右表由运行时注入，API 用 `run(data, tables=...)`」。
> 原型：`.tmp_audit/join_proto/`（12 组用例全部通过；未入库，仅作语义参考）。

---

## 0. 摘要

DCG 当前的两个「连接类」函数都不是关系运算：

- `$join` 是**字符串拼接**（`dcg/runtime.py:434` `_jn`：逐元素 `$string` 后 `sep.join`）。
- `$lookup` 是**对象按键取值**（`dcg/runtime.py:1403` `_lk`：`o.get(k)`，数组逐元素）。

本 RFC 定义第三种能力：**schema 声明式的多表连接**，编译进产物、在行循环之前执行。右表不在构建时嵌入，而由调用方在运行时经 `run(data, tables=...)` 注入。`$lookup` / `$join` 的语义一律不动。

---

## 1. 目标

1. 一次声明即可完成 N:1 左连接、内连接、半连接、反连接、1:N 展开（fanout）与多跳链式连接。
2. 右表数据**运行时注入**：同一份产物可服务不同数据集；注入是显式参数，不依赖模块级全局状态，可并发、可测试。
3. 连接在**行循环之前**完成，行内表达式仍只见「当前行」，不新增行内函数、不改行模型。
4. 旧 schema 生成的产物**字节不变**；旧调用 `run(data)` 继续可用。

## 2. 非目标（v1 不做）

- 非等值连接（`a.x < b.y`、区间、模糊匹配）。
- 连接条件里的聚合 / 子查询 / 多列复合键（预留 `on` 为列表的语法位，v1 只接受单路径）。
- 右表分片、流式读取、超大表外部排序。
- 写回、更新、去重合并（`$merge` 之类）。
- 与 `output.before` / `output.after` 钩子互相调用（只定义执行顺序）。

---

## 3. 公开契约

### 3.1 运行入口

```python
run(data)                  # 无 joins 的产物：签名不变
run(data, tables=None)     # 含 joins 的产物：第二个可选位置参数
```

- 产物**仅在 schema 含 `joins` 时**把 `run` 改为两参数形式；不含 `joins` 的产物源码与哈希保持原样。
- `tables` 形状：`{表名: [行, ...]}`，行必须是 `dict`（与 `input` 行为 dict 的产物一致）。v1 不接受 `list[list]` 右表；传错类型报 `TypeError`。
- `tables=None` 且 schema 声明了 joins → `TypeError: join: schema 声明了 N 处 join，但未注入表（请调用 run(data, tables={...})）`。
- `tables` 里多出来的表**忽略**（允许一张表服务多处 join）。
- 注入的行集合不会被修改；连接结果行是**新 dict**（浅拷贝 + select 列），调用方的 `data` 不被写坏。

### 3.2 行模型

- 左侧行来自 `input.source` 定位后的行集合，必须是 `dict`（否则 `join` 阶段报 `TypeError`）。
- 右侧行必须是 `dict`；`on` / `right_key` 用同一套 `$.` 路径语法（`$.id`、`$.meta.region`、`$.0`）。
- 路径不存在与值为 `null` 在连接语义上等价（都视为「无键」）。

### 3.3 与 `build()` 的关系

`build()` 不接触任何表数据，只把 `joins` 声明编译进产物并写进 manifest（见 §8）。**不存在**「构建时嵌入右表」的选项——这是本 RFC 与早期 B1 方案的关键区别。

---

## 4. Schema 形状

```json
{
  "name": "orders_enriched",
  "input": {"source": "$.orders"},
  "joins": [
    {
      "table": "customers",
      "on": "$.cust",
      "right_key": "$.id",
      "how": "left",
      "select": {"name": "name", "level": "level"},
      "key_mode": "strict",
      "on_duplicate": "error"
    },
    {
      "table": "regions",
      "on": "$.region",
      "right_key": "$.id",
      "how": "left",
      "select": ["region_name"]
    }
  ],
  "output": {"shape": "list"},
  "out": {"type": "object", "fields": {"sku": {"type": "str", "value": "$.sku"}}}
}
```

| 字段 | 必填 | 默认 | 说明 |
|---|---|---|---|
| `table` | 是 | — | 注入表名（`tables` 的键），非空字符串 |
| `on` | 是 | — | 左侧行上的键路径，必须以 `$.` 开头 |
| `right_key` | 否 | `"$.id"` | 右侧行上的键路径 |
| `how` | 否 | `"left"` | `left` / `inner` / `semi` / `anti` / `fanout` / `fanout_left` |
| `select` | 否 | `{}` | 要带出的右表列；`{}` 表示只筛行不加列（semi/anti 必须为空） |
| `key_mode` | 否 | `"strict"` | `strict`（类型+值）/ `loose`（`str()` 后比较） |
| `on_duplicate` | 否 | `"error"` | `error` / `first`；仅对 left/inner/semi/anti 生效 |

`select` 两种写法：

- 列表 `["name", "level"]`：右表列名 = 输出列名。
- 映射 `{"name": "name", "level": "level"}`：`输出名: 右表列名`，用于改名或避免冲突。

`select` 的源列在右表行里不存在 → 该列填 `null`（不报错；右表形态可能因数据而异）。

校验（`dcg.validate` / `build` 期，报 `SchemaError`，定位到 `joins[i].<字段>`）：

- `joins` 必须是数组，长度 0–64。
- 未知键报错（与现有 schema 的严格风格一致）。
- `how` 不在枚举内、`key_mode` / `on_duplicate` 非法、`on` 不以 `$.` 开头、`select` 与 `how` 冲突（semi/anti 带 select）、`select` 输出名重复 → 报错。
- `joins` 与 `input.source` 同时存在没问题；与 `output.before` 同时存在时按 §5.6 的顺序执行。

---

## 5. 语义

### 5.1 键归一

| `key_mode` | 规则 |
|---|---|
| `strict`（默认） | 类型与值都相等才匹配：`1` ≠ `"1"` ≠ `1.0` ≠ `True`；`None` 永不匹配 |
| `loose` | `str(v)` 后比较：`1` 与 `"1"` 匹配；`None` 永不匹配 |

- 两侧键先归一化再查索引；`strict` 用 `(type(v).__name__, v)`，`loose` 用 `str(v)`。
- `NaN` 永不匹配（`NaN != NaN`，索引里也查不到）。
- 右侧键重复且 `how ∈ {left, inner, semi, anti}` 且 `on_duplicate="error"` → 运行期 `TypeError`，文案见 §7。`fanout` / `fanout_left` 天然允许多行，忽略 `on_duplicate`。

### 5.2 六种 how

| how | 未命中 | 命中 1 行 | 命中 N 行 | 列 |
|---|---|---|---|---|
| `left` | 保留 1 行，select 列 `None` | 保留 1 行 + 列 | 错误（见上） | 加列 |
| `inner` | **丢弃** | 保留 1 行 + 列 | 错误 | 加列 |
| `semi` | 丢弃 | 保留 1 行（原样） | 错误 | 不加列 |
| `anti` | 保留 1 行（原样） | 丢弃 | 错误 | 不加列 |
| `fanout` | 丢弃 | 保留 1 行 + 列 | **展开 N 行** | 加列 |
| `fanout_left` | 保留 1 行，列 `None` | 保留 1 行 + 列 | 展开 N 行 | 加列 |

### 5.3 列冲突

`select` 的输出名与**当前行已有的键**冲突 → 运行期 `TypeError`：

```
join: 第 2 处 join 的 select 列 'level' 与现有列冲突（现有键: sku, cust, region, level）；
请用 {"新名": "源列"} 改名
```

不提供静默覆盖。原因：覆盖会让「前一行表达式读到哪一列」依赖运行顺序，是难查的坑；显式改名成本极低。

### 5.4 链式

- 按 `joins` 数组顺序**依次**执行；后一跳的 `on` 可以引用前一跳 `select` 带出的列（原型实测：`customers` 带出 `region`，第二跳 `on: "$.region"`）。
- 每跳都看到「上一跳完成后的行」；`semi` / `anti` 只筛行不改列。
- 链式中间某跳把行丢光不是错误，正常输出空结果。

### 5.5 行数契约（1:N 的影响）

- `out_count` 与 `shape` 都按**连接后的行数**计算。
- `shape="list"` / `"rows"`：输出行数 = 连接后行数（fanout 会变多）。
- `shape="index"`：数值键按**连接后的顺序**从 0 重新编号（不是原始行号）。
- `shape="dict"`：每列长度 = 连接后行数。
- `output.trace=true` 时，trace 记录每处 join 的 `table` / `how` / 输入行数 / 输出行数 / 索引规模，**不**逐行记录。

### 5.6 与现有阶段的顺序

```
input.source 定位 → output.before → joins（按声明顺序）→ 行循环 → shape 组装 → output.after
```

- `before` 先跑：用户可以整形/过滤输入，join 看到的是 before 的输出。
- `after` 最后跑：看到的是已组装成目标 shape 的结构（与现状一致）。
- joins 与 hooks 同用时，`before` 返回值仍必须是行数组（现有规则不变）。

### 5.7 与 `$join` / `$lookup` 的关系

不动。`$join` 仍是字符串拼接，`$lookup` 仍是对象取键；文档（`JSONata 语法.md`、README）在 join 节加一句「关系连接请用 schema 的 `joins`」即可。

---

## 6. 生成代码形态

目标：每处 join 一个模块级函数，`run` 里串起来；索引每次调用重建（无状态、并发安全）。

```python
def _jidx_0(rows, key, dup):
    idx = {}
    for r in rows:
        k = _jkey(r.get("id"))            # 归一化在生成代码里内联
        if k in idx:
            if dup == "error":
                raise TypeError("join: 表 'customers' 的键 $.id 重复 ...")
            continue
        idx[k] = r
    return idx

def _j0(_d, _t):
    idx = _jidx_0(_t["customers"], "id", "error")
    out = []
    for r in _d:
        k = _jkey(r.get("cust"))
        m = idx.get(k)
        ...
    return out

def run(_d, _t=None):
    ...
    if _t is None:
        raise TypeError("join: schema 声明了 2 处 join，但未注入表 ...")
    _d = _j0(_d, _t)
    _d = _j1(_d, _t)
    ...
```

- 缺失表：在进入第一个 join 前检查所有 `table` 是否都在 `_t` 里，一次报全（`表 'customers'、'regions' 未注入`）。
- 键归一化：`strict` 用 `(type(v).__name__, v)`；`loose` 用 `str(v)`；`None` 直接判「无键」不查索引。
- 性能目标（验收门槛）：10 万行、单跳、右表 1 万行 ≤ 250 ms；20 万行 × 2 跳 ≤ 900 ms（原型 708 ms，产物内联后应更快）。基准脚本进 `evidence/`。

---

## 7. 错误文案（草案，实现时以此为准）

| 阶段 | 场景 | 类型 | 文案 |
|---|---|---|---|
| validate | how 非法 | SchemaError | `joins[0].how: 必须是 'left' / 'inner' / 'semi' / 'anti' / 'fanout' / 'fanout_left' 之一，得到 'outer'` |
| validate | on 非法 | SchemaError | `joins[0].on: 必须以 $. 开头，得到 'cust'` |
| validate | semi 带 select | SchemaError | `joins[1].select: how='semi' 不能带出右表列` |
| validate | select 重名 | SchemaError | `joins[0].select: 输出列名 'name' 重复` |
| run | 未注入 | TypeError | `join: schema 声明了 2 处 join，但未注入表（请调用 run(data, tables={...})）` |
| run | 缺表 | TypeError | `join: 表 'customers' 未注入；已注入：['regions']` |
| run | 右表非 dict | TypeError | `join: 表 'customers' 的第 3 行不是 dict（得到 list）` |
| run | 左行非 dict | TypeError | `join: 左侧第 2 行不是 dict（得到 list）；joins 只支持 dict 行` |
| run | 键重复 | TypeError | `join: 表 'customers' 的键 $.id 重复：值 1 命中多行（on_duplicate='error'）；如确需多行请用 how='fanout'，或改 on_duplicate='first'` |
| run | 列冲突 | TypeError | 见 §5.3 |

业务错误在产物里抛 `TypeError`（与 `output.before` 钩子返回类型错误一致），不新增异常类，避免扩公共 API。

---

## 8. manifest 与可复现

`build()` 的 manifest 新增：

```json
"joins": [
  {"table": "customers", "how": "left", "on": "$.cust", "right_key": "$.id",
   "select": ["name", "level"], "key_mode": "strict", "on_duplicate": "error"},
  {"table": "regions", "how": "left", "on": "$.region", "right_key": "$.id", "select": ["region_name"]}
]
```

- 只记录**声明**，不记录数据、不记录表的 sha256（构建期根本没有表）。
- `schema_sha1` 覆盖整个 schema（含 joins），因此 join 声明变化会自然改变产物哈希，与现有漂移检测一致。
- 运行时数据漂移不在 v1 的校验范围内；调用方可用 manifest 的 `joins` 声明自行核对。

---

## 9. 兼容与版本

- 新 schema 顶层键 `joins`：旧版本 DCG（≤2.2.2）读到会按「未知键」报错。因此这是 **2.3.0 minor**，发布说明里写清「schema 若含 `joins`，需 DCG ≥ 2.3.0」。
- 不含 `joins` 的 schema：产物源码、`run` 签名、manifest 均**不变**（golden 快照必须保持字节一致）。
- 含 `joins` 的产物：`run(data)` 调用会得到明确 `TypeError`，而不是静默输出未连接数据。
- `dcg.generate(..., cython=True)` 对 joins 的产物形态：v1 不承诺 Cython 版本可用 join（若开启 cython，join 阶段仍用 Python 生成），文档注明。

## 10. 测试清单

| 文件 | 内容 |
|---|---|
| `tests/test_join_schema.py` | 声明校验：未知键、how/on/select 非法、默认值、semi+select 冲突、长度上限 |
| `tests/test_join_semantics.py` | 六种 how 的行数与列；NULL 键；strict/loose；重复键 error/first；链式两跳 + 列可见性 |
| `tests/test_join_inject.py` | `run(data, tables=...)`；缺表/多余表/错类型；左行与右行非 dict；`data` 不被修改 |
| `tests/test_join_shapes.py` | list/rows/dict/index 四种 shape 下的行数与键；`out_count`；trace 摘要 |
| `tests/test_join_compat.py` | 无 joins 的产物与 2.2.2 golden 字节一致；`run(data)` 仍可用 |
| `evidence/join_bench.py` | 10 万行单跳、20 万行两跳的耗时基线（进 accept.py 门禁或单独脚本） |

至少 45 条新用例；目标把测试总数推到 1892 → ≥1935 区间（具体数以最终实现为准，`验收报告.md` 同步）。

## 11. 待拍板（v1 冻结前）

1. **select 默认值**：本稿默认 `{}`（只筛行不加列）。是否改成「默认带出右表全部列」？后者方便但列爆炸、易冲突，我倾向保持 `{}`。
2. **列冲突策略**：本稿运行期报错并提示改名。是否接受「静默覆盖」或「冲突时加 `right_` 前缀」？
3. **索引缓存**：本稿每次 `run` 重建索引（无状态）。是否要提供 `run(data, tables=..., cache=...)` 或进程级缓存？我倾向 v1 不做。
4. **fanout 下的 `index` / `trace` 细节**：本稿按连接后顺序重新编号、trace 只记摘要。是否认可？

## 12. 实施计划（估）

| 阶段 | 内容 | 估时 |
|---|---|---|
| P1 | `schema.py` 校验 + `codegen.py` 生成 join 阶段 + 运行期错误 | 2–3 天 |
| P2 | 六个测试文件 + 基准脚本 | 1.5 天 |
| P3 | 文档（接口文档新节、README、验收报告计数）+ CI + 发布 2.3.0 | 1 天 |

总计约 4–5 个工作日；若 P1 的 codegen 改动比预期大（生成器是单文件 3300+ 行），最多 6 天。
