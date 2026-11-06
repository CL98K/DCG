# DCG Join 能力设计（RFC v0.3）

> 状态：**已实现（2.2.3）**。本文档描述已落地的行为，不是计划书。
> 目标版本：**2.2.3**——`$lookup` 3–7 参表连接形态、`dcg.table` 运行时对象、`output.where` / `output.expand` 均已随该版本交付。
> 语义权威：本文件。实现权威：`dcg/table.py`（473 行）、`dcg/codegen.py`（`_Gen.lookup`、`_lk_slot`、`_pred_rewrite`、`_range_pat`、`_where_cond`、`specs_of`）、`dcg/runtime.py`（`_xp`）、`dcg/schema.py`。两者冲突时以实现为准，本文件凡修订处标 **修订（v0.3）**。
> 决策来源：用户 2026-11-05 指定「join 的描述放在 `$lookup` 算子里」「⑥ semi/anti 与 ⑦ fanout 都要」「复合键要支持」「非等值用表达式」。
> v0.1（顶层 `joins` 数组方案）**已作废**；v0.2 是实施基线，v0.3 记录它与实现的差异（§0.3）。

---

## 0. 摘要、问题与版本演进

### 0.1 问题陈述

DCG 的 schema 只描述「一行输入 → 一行输出」的表达式，输入里没有第二张表。实际数据加工里最常见的动作恰恰是**查另一张表**：把客户名接到订单行上、把订单按客户聚合成金额、按区间（费率档、价格带）取一个系数、用「右表存在」来筛掉孤儿行。

这些动作在 2.2.2 之前只能靠：调用方先把表摊平进每一行（内存与拷贝放大）、或在 DCG 外面用 Python 拼一遍（逃出 schema、失去产物与校验）。join 能力要解决的就是把「查表」写回表达式，同时**不把表数据塞进产物**。

### 0.2 候选方案与取舍（v0.1 → v0.2，历史）

| 项 | v0.1（已作废） | v0.2 / v0.3（现状） |
|---|---|---|
| 声明位置 | schema 顶层 `joins` 数组 | **`$lookup` 算子的多参形态** |
| 右表来源 | `run(data, tables=...)` | 同（保留） |
| 连接类型 | `how` 六种 | 由 `$lookup` + `output.where` / `output.expand` 组合表达 |
| 复合键 | 不支持 | **支持**（键与右键都可传数组） |
| 非等值 | 不支持 | **支持**（第 7 参谓词表达式） |

取舍理由：

1. **不新增顶层声明块**。顶层 `joins` 要把「表别名 → 表」的绑定和「字段引用」拆到两处维护，字段改名时两边都得改，且需要新的顶层键与新的校验分支，波及所有既有 schema 的校验路径。
2. **一个算子覆盖六种连接类型**。`how` 六种（inner/left/right/full/semi/anti）在行式表达式里其实是两个正交维度：*取什么*（列 / 整行 / 集合）和 *过滤什么*（`$exists(...)`）。用 `$lookup` 的「列 / 整行 / 策略」+ `output.where` 表达，组合数是乘积而不是六个分支。
3. **用户明确要求放在 `$lookup` 算子里**，因此沿用该名字，不引入 `$join`。

v0.1 的 `joins` 顶层键**未引入**；2.2.2 及更早的 schema 结构不受影响。

### 0.3 v0.2 → v0.3 变更记录

v0.2 冻结时 join 还没有代码，文档里既有已验证的设计承诺，也有尚未落地的设想。2.2.3 交付后，凡与实现不一致的条目一律以实现为准——尤其是**性能数字**和**错误文案**：它们会被用户直接复制进 issue，错一个字就是一轮排查。

| # | 项 | v0.2 写的 | v0.3（实现态） | 为什么改 |
|---|---|---|---|---|
| 1 | 版本与状态 | 目标 2.3.x，冻结为实施基线 | 目标 **2.2.3**，状态「已实现（2.2.3）」 | 能力在 2.2.3 内交付，没有单独的 2.3 版本 |
| 2 | 两参 `$lookup(o, k)` | 保留，语义不变 | **删除**，编译期报错，迁移到 `$.path` | `$lookup` 只服务表连接，两参是 JSONata 历史包袱，与 DCG 路径语义重复（§3.3） |
| 3 | 运行入口 | `run(_d, _t=None)` + `_st(_t)` 上下文 | `def run(_d, tables=None)`；`_T=_dot(tables,_SP)`（`_dot` 即 `dcg.table._tm`），槽位 `_i0=_T['表'].s0` | 索引在建索引入口一次建好，行循环里不再有「是否已建」分支 |
| 4 | 索引生命周期 | 首次访问时构建，缓存在 `threading.local` 上下文 | `prepare` / `bind` 在进入行循环前一次性建齐并绑到 `s0`、`s1`…；**无** `threading.local` | 每行探测里不再有构建分支与锁；并发安全改由「每线程各自持有 `Table`」承担 |
| 5 | 索引结构 | 未提 | `strict` 按类型分桶 `b_int` / `b_str` / `b_float` / `b_bool` / `b_other`，`loose` 单桶 `b_loose` | 省掉逐次 `type()` 比较；`1` 与 `True` 天然不同桶 |
| 6 | 谓词类型检查 | 「返回非布尔 → 运行期 TypeError」 | 编译成 `(lambda _l,_r: …)`，**按真值判断，不报错** | `Index.q` / `Index.qa` 里就是 `if pred(left, r)`，没有类型守卫（§4.5） |
| 7 | `output.where` 生效位置 | 行循环之后、shape 组装之前 | 行循环**之前**，过滤输入行（`_d=[R for R in _d if (…)]`） | 被过滤掉的行不执行 `$lookup`，是实打实的性能差别；semi/anti 语义不变 |
| 8 | `output.expand` 生效位置 | 行循环之后、shape 组装之前 | shape 组装**之后**、`after` 之前，走 runtime helper `_xp` | 展开的对象是输出行的某一列，必须等形状定型 |
| 9 | 错误文案 | §7 一张设计稿（`join:` 前缀、含表名） | 以实现为准逐条重写（§7），**无** `join:` 前缀 | 文案要能被逐字搜索，不能按草稿写 |
| 10 | 性能 | 三个绝对时间目标（250 ms / 900 ms / 0.5 µs）+ 原型 708 ms | 改为「档位 × 类别」规格 + CI 唯一口径（§6），文档不写任何本地数字 | 绝对时间随 runner 漂移，原型数字不可复现，写进文档即成为伪证据 |
| 11 | manifest | 计划新增 `tables` / `lookups` | **未实现**，manifest 键集不变（§8） | 表名与列名已在 `schema_sha1` 覆盖的表达式文本里；单独记录没有消费者 |
| 12 | 测试组织 | 六个 `tests/test_lookup_*.py` | 一个 `tests/test_join.py`，28 个用例按四层分组（§10） | 按层分组足够，切碎后共享 fixture 的成本更高 |
| 13 | 生成码对私有名的依赖 | 未提 | 产物 `from dcg.table import _tm as _dot` | 重命名 `_tm` 是破坏性变更，列为兼容风险（§9） |
| 14 | 非目标 | 4 条 | 补齐 2.2.3 明确不做的 3 条（§2） | 让「没做」和「做不到」区分开 |

---

## 1. 目标（已达成）

1. 在行内表达式里直接描述「取另一张表的列 / 整行 / 一对多集合」，不新增顶层声明块。
2. 右表**运行时注入**：`run(data, tables=...)`；不嵌入产物、不写模块级全局。
3. 覆盖 N:1 取列、取整行、多跳、复合键、1:N 数组、1:N 聚合、区间（非等值）、semi/anti、fanout——全部已实现，用例见 §10。
4. **不含 `$lookup` 的 schema：产物源码与 `run` 签名逐字节不变**（`run(_d)` 单参、不 import `_tm`）。
   **修订（v0.3）**：v0.2 的第 4 条是「两参 `$lookup` 语义不变」。该形态已删除（§3.3），这条换成「无 `$lookup` 的产物不变」——这才是真正需要守住的不变量，由 golden 快照守护。

## 2. 非目标（2.2.3 不做）

- 多对多去重、笛卡尔积去重（用户自行 `$distinct`）。
- 右表分片 / 流式 / 外存排序。
- 写回、更新右表。
- 谓词里引用第三张表、聚合、子查询。
- 连接结果的列冲突自动改名（不做静默覆盖）。
- `manifest` 的 `tables` / `lookups` 字段（§8）。
- 区间谓词 + 策略 `all` 的 `bisect` 优化：该组合强制退回逐行扫描（§4.5）。
- 非 `dict` 右表行的友好报错：现在直接抛 `AttributeError`（§7）。

---

## 3. 公开契约

### 3.1 运行入口

```python
run(data)                 # 产物不含表连接：单参，签名不变
run(data, tables=None)    # 产物含表连接：第二个参数
```

- **仅在 schema 里出现 3–7 参 `$lookup` 时**，产物把签名写成 `def run(_d, tables=None)`；否则仍是 `def run(_d)`，源码逐字节不变（golden 快照守护）。
- `tables` 形状：`{表名: dcg.Table 或 [行, ...]}`。裸行列表在 `_tm` 里被包成 `Table`，**每次调用都重建索引**；性能敏感场景直接传 `Table`，或先 `prepare` 一次再复用。
- 声明了连接却传 `tables=None` → `TypeError`（文案见 §7）。
- 多出来的表忽略；同一张表可服务多处 `$lookup`（同规格共用槽位）。
- 注入的行不被修改；返回的整行 `dict` 是浅拷贝。

### 3.2 `$lookup` 签名

```text
$lookup(键, '表', '右键', '列'[, '模式'[, '策略'[, '谓词']]])
```

| 位置 | 名称 | 必填 | 说明 |
|---|---|---|---|
| 1 | 键 | 是 | 值，或值数组（复合键） |
| 2 | 表 | 是 | 字符串字面量，`tables` 的键 |
| 3 | 右键 | 是 | 路径字符串（`'id'` / `'meta.region'`），或路径数组；`null` 表示不建索引（只允许配区间谓词） |
| 4 | 列 | 否 | 右表列名；**省略或写 `null`** → 返回整行 `dict` |
| 5 | 模式 | 否 | `strict`（默认）/ `loose` |
| 6 | 策略 | 否 | `one`（默认）/ `first` / `all` |
| 7 | 谓词 | 否 | 布尔表达式，`$l.` 指左行、`$r.` 指候选右行 |

- 第 2–7 参必须是**字面量**（表名、列名、右键、模式、策略、谓词文本）；不能用表达式拼出表名。
- `$lookup` 只能写在 `expr`（以及 `output.where` / `before` / `after` 这些钩子）里。字段的 `value` 只接受 `$` 开头的路径，写 `$lookup` 会报 `value 路径非法`。
- 第 3 参给数组即复合键，如 `['sku', 'region']`；元素个数不限，但左键的元组长度必须一致，否则永不匹配。

### 3.3 两参形态已移除（A3 修订）

v0.2 把「两参 `$lookup(o, k)` 保持不变」写成既有约束。2.2.3 已**删除**该形态：接口只保留 3–7 参表连接形态。

编译期报错（`SchemaError`，文案逐字一致；实际输出带定位前缀，如 `out.fields.a.expr: `）：

```text
$lookup 的两参形态（对象取键）已在 2.2.3 移除；取对象字段请写 $.path 或 $.a.b，表连接请写 $lookup(键,'表','右键','列')
```

迁移路径：

| 旧写法（≤ 2.2.2） | 2.2.3 写法 |
|---|---|
| `$lookup($.meta, 'region')` | `$.meta.region` |
| `$lookup($, 'id')` | `$.id` |
| `$lookup($.a, 'b')`（数组逐元素取键） | `$.a.b`（DCG 的路径对数组本就逐元素下钻） |

**为什么移除**：

1. `$lookup` 只服务表连接（第 2 参是表名）。两参形态是 JSONata 的历史包袱——借了一个「查表」算子的名字，实际只做对象取键。
2. 两参与 DCG 的路径语义**重复**。`$.path` / `$.a.b` 已经覆盖取字段，并且带数组下钻、缺键归一、`optional` 语义；两套写法并存只会让「推荐哪个」长期模糊。
3. 保留两参意味着参数个数分支里永远留着一个与表连接无关的形态，生成器、文档、错误文案都要为它分叉。

### 3.4 返回值

| 形态 | 命中 | 未命中 |
|---|---|---|
| 4 参（取列），`one` / `first` | 该列的值 | `null` |
| 3 参（取整行），`one` / `first` | 右行 `dict`（浅拷贝） | `undefined` |
| 策略 `all` + 列 | 命中的**非 null** 列值组成的 `list` | `[]` |
| 策略 `all` + 整行 | 行副本 `list` | `[]` |
| 区间（`pv` / `pr`） | 同取列 / 取整行 | 同取列 / 取整行 |

未命中的差异是刻意的：取整行用 `undefined` 才能配合 `$exists(...)` 写 semi/anti；取列用 `null` 让输出干净。
**修订（v0.3）**：`all` + 列会**跳过该列为 null 的行**（`Index.ga` 的实现），因此 `$count($lookup(…,'strict','all'))` 数的是「该列非 null 的命中行数」，不是命中行数。要数行请对整行形态取 `$count`。

### 3.5 Python 侧 API（`dcg.table`）

```python
from dcg import Table, prepare, bind, specs_of      # 包顶层导出
from dcg.table import Index, RangeIndex, _tm        # 按需直接取
```

| 对象 | 签名 | 作用 |
|---|---|---|
| `Table` | `Table(rows, keep_rows=True)` | 一份行数据 + 若干索引 |
| `Table.index` | `index(key, mode="strict", strategy="one") -> Index` | 按 `(key, mode, strategy)` 幂等建/取索引 |
| `Table.range` | `range(key, hi, mode="strict", strategy="one") -> RangeIndex` | 按 `lo` 排序 + `bisect` 的区间索引；`mode` / `strategy` 只用于规格匹配 |
| `Table.find` | `find(kind, key, mode, strategy, hi)` | 按规格找已有索引，没有返回 `None`（`bind` 用）；区间索引额外按 `(key, hi)` 兜底匹配 |
| `Table.release` | `release()` | 释放行数据；仅 `keep_rows=False` 的 `Table` 允许 |
| `prepare` | `prepare(specs, tables, release=True)` | 按规格建齐索引，绑到 `s0`、`s1`…，返回 `{名: Table}` |
| `bind` | `bind(specs, tables)` | 只做规格匹配与槽位绑定；缺索引直接报错 |
| `specs_of` | `specs_of(schema) -> list[dict]` | schema 需要的索引规格，顺序 = `$lookup` 首次出现顺序 |

- 规格项：`{"table", "key", "mode", "strategy", "kind", "hi"}`，`kind` 为 `"hash"` / `"range"`。`_spec_parts` 保留了元组分支，但元组没有表名，`prepare` 会报 `规格缺少表名`——**实际只接受 dict 形态**。
- 槽位命名：`prepare` / `bind` 把索引挂成 `Table` 的属性 `s0`、`s1`…（顺序即规格顺序）；生成码里对应局部名 `_i0`、`_i1`…。
- 索引只存**行引用**，不复制行；列不建索引，探测拿到行之后 `row.get(col)`。`Index.g` / `Index.a` / `Index.gv` 返回的是行对象或索引内部列表**本体**（热路径不复制，只读）；需要能安全改写的副本请用 `gr` / `gar`。
- `keep_rows=False` + `release()` 支持「建完索引就丢掉行」。`prepare(release=True)` 对这类 `Table` 自动释放；`_tm` 不释放。行数据释放后再建索引（`index()` 或 `range()`）会抛 `RuntimeError: Table 的行数据已释放（keep_rows=False），无法再建索引`——不能静默建成空索引。
- `Index.agg(col, kind)`：`kind ∈ sum / count / max / min / avg`，按 `(col, kind)` 缓存在 `Index._agg`，只对 `strategy='all'` 有意义。空桶 `sum` / `count` 记 `0`，其余记 `None`（§4.3）。未知 `kind` 抛 `ValueError`（不再静默当成 `avg`）。
- `prepare` / `bind` / `_tm` 遇到未注入的表名抛 `TypeError: 表 'x' 未注入；已注入：[...]`，不甩裸 `KeyError`。

---

## 4. 语义

### 4.1 键归一与分桶

| 模式 | 规则 |
|---|---|
| `strict`（默认） | 先按 `type(v)` 分桶，再全等比较：`1` ≠ `"1"` ≠ `1.0` ≠ `True` |
| `loose` | `str(v)` 后落 `b_loose`；`1` 与 `"1"` 匹配 |

- `None` 与缺键 → 永不匹配（建索引时跳过，探测时直接短路，不查索引）。
- 复合键：逐分量取值后组成元组；**任一分量为 `None` → 整体不匹配**。左键传数组（`[1, 'x']`）时由 `Index._probe` 转成元组。
- 不可哈希的键值在构建时被跳过（`hash()` 抛 `TypeError`），因此永远不会命中。
- **修订（v0.3）**：没有对 `NaN` 做特判。`strict` 下 `NaN` 会进 `b_float` 桶，探测按 `dict` 的相等语义走（`nan != nan`），不同 `NaN` 对象之间不命中，同一对象因身份相同可能命中。v0.2 写的「`NaN` 永不匹配」是设计承诺，实现没加守卫。

### 4.2 策略

| 策略 | 右表键重复时 |
|---|---|
| `one`（默认） | **建索引时**抛 `ValueError`（文案见 §7） |
| `first` | 保留首条 |
| `all` | 桶里存列表，返回全部匹配 |

索引按 `(_key_id(key), mode, strategy, hi)` 缓存在 `Table._indexes`，`index()` 幂等。`all` 与 `one` 的桶形态不同（列表 vs 单行），互不共用。

### 4.3 一对多与聚合

```text
$sum($lookup($.cust, 'orders', 'cust', 'amount', 'strict', 'all'))
$count($lookup($.cust, 'orders', 'cust', 'id', 'strict', 'all'))
$max(...) / $min(...) / $average(...)
```

- 实测：`$sum([]) = 0`、`$count([]) = 0`；`$average([])` / `$max([])` / `$min([])` 为 `undefined`（落到 `optional` 字段上归一成 `null`）。
- 运行时层有预聚合入口 `Index.agg(col, kind)`（§3.5），把「每行遍历 N 条」变成「每行一次 dict 查找」；空桶 `sum` / `count` 记 `0`，其余记 `None`。
- 生成码侧**不**自动把 `$sum($lookup(…))` 改写为 `agg`。`evidence/join_bench.py` 的 `agg1n` 与 `agg1n_scan` 就是这组对照：前者走预聚合、后者走扫描，用来量化差距。这是已知的优化余地，不是缺陷。

### 4.4 多跳

嵌套 `$lookup` 即顺序：内层先求值，外层拿内层结果当键。中间未命中为 `null`，继续外层查询（`null` 键永不匹配）。多跳不额外建索引：每一跳各自一个槽位（`s0`、`s1`）。

### 4.5 谓词（非等值）与区间索引

```text
$lookup($.region, 'rates', 'region', 'rate',
        'strict', 'one',
        '$l.amount >= $r.lo and $l.amount < $r.hi')
```

- `$l.` = 左行（当前行）；`$r.` = 候选右行；`$.` 等价于 `$l.`。编译期由 `_pred_rewrite` 把 `$l.x` / `$r.x` 换成 `_l0` / `_r0`，再映射成 `R['x']` / `_r['x']`。
- 执行：先按等值前缀（第 1 / 3 参）取候选集（`Index._cand`），再用谓词过滤。第 3 参传 `null` 时对全表扫描，但 `null` 右键**只允许区间谓词**——等值连接在编译期就报错。
- 谓词在构建期编译成产物里的 `(lambda _l,_r: …)`，不出现 `eval`。
- **区间快路径**：谓词形状恰好是 `$r.lo <= x and x < $r.hi`（含 `$r.hi = null or x < $r.hi` 变体）时，`_range_pat` 识别它并改用 `RangeIndex`（按 `lo` 排序 + `bisect_right`，`hi` 为 `null` 当 `+inf`）。识别不到该图样就退回逐行扫描候选。
  - 策略为 `all` 时：右键给了等值/前缀键的，用哈希索引先过滤再逐行判定（语义不变，是「等值前缀 + 区间条件」的常用写法）；右键为 `null` 的，直接走区间索引的 `all` 形态 `pva` / `pra`（`RangeIndex.pick_all` 取全部重叠区间）。
- **修订（v0.3）**：谓词**按真值判断**，返回非布尔不报错。v0.2 承诺的「返回非布尔 → 运行期 `TypeError`」没有实现（`Index.q` / `Index.qa` 里就是 `if pred(left, r)`）。写谓词时请自己保证返回布尔。
- 复杂度：有等值前缀 ≈ 索引查找；右键 `null` + 区间 = O(左行 × log 右表)；纯扫描形态 O(左行 × 右表行)，只适合小表。

### 4.6 `output.where` 与 `output.expand`

```json
"output": {"shape": "list",
           "where": "$exists($lookup($.cust, 'customers', 'id'))",
           "expand": "emails"}
```

- `where`：逐行布尔表达式，作用在**输入行**上（`$.` / `$l.` 指当前输入行）；`$r.` 在 `where` 里被编译期拒绝（那里没有右行）。假值与 `undefined` 的行被丢弃。产物里是 `_d=[R for R in _d if (…)]`。
- 旧版本用 `filter` 表示逐行钩子并已废弃，故新键名用 `where`，不复用 `filter`。
- `expand`：指定一个 `out.fields` 里已声明的字段名。该字段是数组时一行展开成 N 行（其余字段复制），空数组丢弃该行；非数组（含 `null` / 缺失）按单元素保留一行。只允许 `output.shape` 为 `"list"` / `"rows"`。实现是 runtime helper `_xp(rows, col, pos)`：`shape="list"` 的行是**位置行**，`pos` 指出目标列在行里的下标（编译期由 `_Gen._expand_pos` 算出；单字段扁平形态为 `None`，此时行本身就是该列的值）。
- 实际顺序：`input.source` → 槽位绑定（`_T` / `_i0…`）→ `output.before` → `output.where` → 行循环（含 `$lookup`）→ shape 组装 → `output.expand` → `output.after`。
- **修订（v0.3）**：v0.2 说 `where` / `expand` 都在「行循环之后、shape 组装之前」。实际 `where` 在行循环**之前**——被过滤掉的行**不会**执行 `$lookup`（semi/anti 因此更便宜）；`expand` 在 shape 组装**之后**（展开的对象是输出行）。

### 4.7 与 `output.before` / `after` 的关系

`before` 在槽位绑定之后、`where` 之前（因此也先于所有 `$lookup`）；`after` 仍最后（`expand` 之后）。`where` / `expand` 不改这两个钩子的语义。

---

## 5. 生成代码形态

含表连接的产物（`simple_n1`，左表 `x` 取右表 `t` 的 `c`）：

```python
from dcg.table import _tm as _dot

_SP = [{'table': 't', 'key': 'id', 'mode': 'strict', 'strategy': 'one',
        'kind': 'hash', 'hi': None}]

def run(_d, tables=None):
    _T = _dot(tables, _SP)
    _i0 = _T['t'].s0
    _o = []
    _c = _o.append
    for R in _d:
        _c([_i0.gv(R['x'], 'c')])
    return _o
```

- `_SP` 是 `specs_of(schema)` 的产物，编译期固化进产物；`_tm(tables, specs)` 按它建索引并返回 `{名: Table}`，槽位 `s0`、`s1`… 与 `_i0`、`_i1`… 一一对应。
- 发射映射：

| 形态 | 产物片段 |
|---|---|
| `one` / `first` + 列 | `_i0.gv(k, 'col')` |
| `one` / `first` + 整行 | `_i0.gr(k)` |
| `all` + 列 | `_i0.ga(k, 'col')` |
| `all` + 整行 | `_i0.gar(k)` |
| 谓词 + 列 | `_i0.q(k, R, (lambda _l,_r: …), 'col')` |
| 谓词 + 整行 | `_i0.q(k, R, (lambda _l,_r: …), None)`（第 4 参传 `None` 即整行） |
| `all` + 谓词 | `_i0.qa(k, R, (lambda _l,_r: …), 'col' \| None)` |
| 区间 + 列 | `_i0.pv(x, 'col')` |
| 区间 + 整行 | `_i0.pr(x)` |
| 区间 + 列 + `all` | `_i0.pva(x, 'col')` |
| 区间 + 整行 + `all` | `_i0.pra(x)` |
| `output.expand` | `_xp(<rows>, '<col>', <pos>)` |

- 不含表连接的产物：不出现 `_dot` / `_tm`、`run` 仍是一参数、源码逐字节不变。
- 同规格的多处 `$lookup` 共用槽位（`_lk_slot` 以 `(table, rkeys, mode, strategy, kind, hi)` 为签名去重）。

---

## 6. 性能规格（硬约束）

### 6.1 档位与查询分类

右表体量三档：**1 000 / 10 000 / 1 000 000** 行。左表默认 200 000 行（CI 档 50 000 行）。

| 类别 | 含义 | 对应 case |
|---|---|---|
| 简单 | 单表连接、单键、等值 | `simple_n1` |
| 复杂 | 多级链式 / 复合键 / 范围谓词 / 1:N 聚合 | `chain2`、`composite_key`、`range_pred`、`agg1n`、`agg1n_scan` |

`agg1n` 走 `Index.agg` 预聚合，`agg1n_scan` 走列表扫描——两者是同一问题的两个实现，差值即预聚合的收益，必须一起报。

### 6.2 流程硬约束

1. **先优化，后出数。** 未优化的实现跑出来的数字不得作为结论写进任何文档、issue 或发布说明。要报数，先确认该路径已是本文档描述的形态（索引一次建好、行循环内无构建分支、区间走 `bisect`）。
2. **所有对外发布的性能指标必须由 GitHub Actions CI 产出。** 唯一合法来源是 `join-bench` job 上传的 artifact `join-bench`（其中的 `join-bench.json`）。
3. **本地数字只作开发期参考**，不进文档结论、不进验收报告的性能栏。本地跑出「快了 3 倍」不是证据，只是继续开发的理由。
4. 引用指标时必须带 artifact 的 run 链接与 `meta`（`dcg_version` / `python` / `platform` / `rows` / `right` / `repeat` / `seed`）。缺 `meta` 的数字不构成指标。

### 6.3 指标口径

| 项 | 口径 |
|---|---|
| 采样 | 每项 warmup 1 次（右表 > 200 000 行时 2 次）后采样，取**中位数**；CI 档与 `--full` 档都是 `repeat=3` |
| GC | 采样段 `gc.collect()` + `gc.disable()`，`finally` 恢复；`meta.gc_disabled = true`。不关的话大表上分代 GC 会随机落进计时窗口，实测极差 44% → 7% |
| 吞吐 | `us_per_row = median_ms × 1000 / 左表行数` |
| 建索引 | `build_ms`（建表 + 建索引的墙钟，一次性，**不在** `median_ms` 里） |
| 内存 | 拆两列：`peak_build_mb`（建表 + 建索引）与 `peak_run_mb`（一次 run）。**两列不可跨层比较**——e2e 的 `peak_run_mb` 由左表输出组装支配，与右表规模几乎无关。右表 > 200 000 行时都记 `n/a`（`MEM_LIMIT_ROWS`） |
| 扇出 | `fanout` = 该 case 在右表上的平均每键行数。`agg1n`/`agg1n_scan` 的桶数由 `_n_oid()` 钉在 `FANOUT = 50`，三档一致，否则桶宽变化会被误读成「连接随右表规模劣化」 |
| 内存预检 | `(右表行数 + 左表行数) × 420 B` 超过可用内存 70% 时该组合标 `skipped`，不硬跑；可用内存取 `min(物理可用, cgroup 限额)` |
| 随机性 | 数据全为确定性算术式（不依赖 `random`），`meta.random = false` |
| 1e6 档 | 只由 `--full` 触发（`workflow_dispatch` 的 `full=true`） |

`status` 为 `skipped` / `error` 的行不得当作 0 或当作通过；`note` 里的异常首行要一起报。

### 6.4 结果表（CI 实测）

> 下表每一格都来自 `join-bench` artifact，对应 run `42183230556`（见 §6.4.1）。本地跑出的数字不填在这里。
>
> 表内的「中位数 / us_per_row / peak_run_mb」取 **e2e 层**（`dcg.generate` → `exec` → `run(data, tables=...)`），因为它才是用户真实付出成本的路径；`join-bench.json` 里同时保留了 `runtime` 层（直连 `dcg.table`）的同一批 case，本表的 `build_ms` 与 `peak_build_mb` 取 runtime 层的同一批测量（建表 + 建索引与层无关）。
>
> **两层的差不能一律读成「生成码的固定开销」**：它至少由三部分构成——生成码的固定开销、e2e 多出来的左表输出组装（`peak_run_mb` 那 3.8–5.2 MB 就是它）、以及 case 特有的算法差。`agg1n` 的差主要来自「预聚合被排除在计时外」（`runtime` 的 `Index.agg` 在 builder 里做完，`median_ms` 只量 dict 查找），`range_pred` 的差来自两层算法不同（见下条）。这两行不适用「固定开销」的说法。
>
> `agg1n_scan` 只有 `runtime` 层。生成码当前只发扫描形态（`$sum($lookup(...'all'))` → `$sum(_i0.ga(k,col))`），预聚合 `Index.agg` 尚未接到生成器（§4.3 记为已知优化余地）。因此 `e2e` 的 `agg1n` 行的数值应与 `runtime` 的 `agg1n_scan` 同量级；二者与 `runtime` 的 `agg1n` 之间的差，就是「把预聚合接进生成器」这件事的潜在收益。这一行的 e2e 格记「不适用（仅 runtime 层）」。
>
> `range_pred` 这一行**两层算法不同**：`runtime` 走 `RangeIndex.pick` → `bisect_right`；`e2e` 只能用「等值前缀（`region`）+ 谓词」，生成码发的是 `Index.qa` 谓词扫描。所以该行 e2e 的绝对值不代表区间索引的快路径，只能作为「生成器侧还不支持区间索引」的现状记录。
>
> 另注：`e2e` 层的计时前会先 `dcg.prepare(dcg.specs_of(schema), tables)` 把索引建好再计时——传裸行列表时生成码里的 `_tm` 每次调用都会重包 `Table`，索引幂等缓存随之失效，测出来的会是「建索引 + 连接」，右表 1e6 时建索引会盖掉连接本身。这次建索引的耗时单独记在 `build_ms` 列。

| 档位 | case | 类别 | 中位数（e2e） | us_per_row | `build_ms` | `peak_build_mb` | `peak_run_mb`（runtime / e2e） |
|---|---|---|---|---|---|---|---|
| 1 000 | `simple_n1` | 简单 | 20.595 ms | 0.4119 | 3.424 | 0.302 MB | 0.0001 / 3.853 MB |
| 1 000 | `chain2` | 复杂 | 30.562 ms | 0.6112 | 3.708 | 0.307 MB | 0.0001 / 3.853 MB |
| 1 000 | `composite_key` | 复杂 | 38.230 ms | 0.7646 | 4.797 | 0.320 MB | 0.0001 / 3.853 MB |
| 1 000 | `range_pred` | 复杂 | 68.532 ms | 1.3706 | 0.717 | 0.073 MB | 0.0001 / 3.874 MB |
| 1 000 | `agg1n` | 复杂 | 183.792 ms | 3.6758 | 1.708 | 0.197 MB | 0.0001 / 5.189 MB |
| 1 000 | `agg1n_scan` | 复杂 | 不适用（runtime 122.912 ms） | — | 1.516 | 0.194 MB | 0.0002 / — |
| 10 000 | `simple_n1` | 简单 | 22.965 ms | 0.4593 | 34.900 | 3.019 MB | 0.0001 / 3.853 MB |
| 10 000 | `chain2` | 复杂 | 34.327 ms | 0.6865 | 34.028 | 3.019 MB | 0.0001 / 3.853 MB |
| 10 000 | `composite_key` | 复杂 | 47.210 ms | 0.9442 | 49.365 | 3.172 MB | 0.0001 / 3.853 MB |
| 10 000 | `range_pred` | 复杂 | 68.176 ms | 1.3635 | 0.705 | 0.073 MB | 0.0001 / 3.874 MB |
| 10 000 | `agg1n` | 复杂 | 235.493 ms | 4.7099 | 15.570 | 1.950 MB | 0.0001 / 5.109 MB |
| 10 000 | `agg1n_scan` | 复杂 | 不适用（runtime 157.440 ms） | — | 13.832 | 1.931 MB | 0.0002 / — |
| 1 000 000 | `simple_n1` | 简单 | 46.747 ms | 0.9349 | 766.475 | 不采集（超 tracemalloc 档） | 不采集 |
| 1 000 000 | `chain2` | 复杂 | 59.991 ms | 1.1998 | 752.147 | 不采集（超 tracemalloc 档） | 不采集 |
| 1 000 000 | `composite_key` | 复杂 | 84.291 ms | 1.6858 | 1 048.052 | 不采集（超 tracemalloc 档） | 不采集 |
| 1 000 000 | `range_pred` | 复杂 | 68.804 ms | 1.3761 | 0.161 | 不采集（超 tracemalloc 档） | 不采集 |
| 1 000 000 | `agg1n` | 复杂 | 705.257 ms | 14.1051 | 771.160 | 不采集（超 tracemalloc 档） | 不采集 |
| 1 000 000 | `agg1n_scan` | 复杂 | 不适用（runtime 536.383 ms） | — | 482.961 | 不采集（超 tracemalloc 档） | 不采集 |

**§6.4.1 数据来源**

| 项 | 值 |
|---|---|
| run | `42183230556`（`workflow_dispatch` + `full=true`，head `79dd873`） |
| job / artifact | `join-bench` / `join-bench`（`join-bench.json`） |
| runner | `ubuntu-latest`（`Linux-7.0.0-1017-azure-x86_64-with-glibc2.39`） |
| Python / DCG | 3.12.16 / 2.2.3 |
| 规模 | 左表 50 000 行；右表 1 000 / 10 000 / 1 000 000；`repeat=3`（`--full` 档与 CI 档同一采样数） |
| 采样口径 | `meta.gc_disabled = true`；右表 > 20 万行时 warmup 2 次；`meta.fanout = 50` |
| 内存预检 | `meta.avail_mem_mb = 14356.2`（取 `min(物理可用, cgroup 限额)`） |
| 自检 | `meta.contract` = `prepare/bind=ok range=ok agg=ok`；`meta.e2e.status` = `ok` |
| 脚本耗时 | 28.154 s |

同一提交的 push run `42183202224` 因 `concurrency: cancel-in-progress` 被上面那次 `workflow_dispatch` 取消，故本表只用 dispatch 那次的 artifact。

两点读表须知：

- `range_pred` 的右表是固定的 200 档（每档 5 000 宽），**不随 `--right` 变化**；该行在三个档位下数值应基本一致（实测 runtime 11.942 / 11.909 / 11.783 ms），出现差异说明档位参数泄漏进了 case。
- 1e6 档在本次运行里**没有出现 `skipped`**：内存预检按 `(左 + 右) × 420 B` 估约 441 MB，未撞 70% 可用内存线，六个 case 全部跑完；`peak_*` 列按口径记「不采集（超 tracemalloc 档）」，不是跑失败。
- `build_ms` 在 1e6 档是 0.75–1.05 s，比 `median_ms` 还大——**建索引是一次性成本，跨查询复用才划算**；§5.2 的 `dcg.prepare` / `bind` 就是为这条服务的。

### 6.5 CI 落地方式

`.github/workflows/ci.yml` 的 `join-bench` job：

- 触发：`if: github.event_name != 'pull_request'`——push 到主分支与 `workflow_dispatch` 都跑，PR 不跑（省 runner）。
- 步骤：`pip install -e .` → `python -X utf8 evidence/join_bench.py --ci --json join-bench.json`；`workflow_dispatch` 输入 `full=true` 时追加 `--full`（含 1e6 档；采样数仍是 3，1e6 档首个样本系统性偏低，`repeat=1` 会取到偏小的那个数）。
- 上传：`actions/upload-artifact`，`name: join-bench`、`path: join-bench.json`、`if: always()`、`if-no-files-found: warn`、保留 14 天。
- 该 job **不挂 `needs`、不进任何门禁**。基准数字有噪声，用绝对阈值卡门禁只会制造假失败；它的职责是产出可追溯的指标，不是判定通过与否。

---

## 7. 错误文案（实现态）

以下为**逐字**文案，改动即破坏性变更。编译期错误由 `SchemaError` 抛出，实际输出带定位前缀（如 `out.fields.a.expr: `）。

### 7.1 编译期

| 场景 | 文案 |
|---|---|
| 参数过少 | `$lookup 至少 3 个参数：$lookup(键,'表','右键'[,'列'[,'模式'[,'策略'[,'谓词']]]])` |
| 参数过多 | `$lookup 最多 7 个参数，得到 8 个` |
| 两参形态 | `$lookup 的两参形态（对象取键）已在 2.2.3 移除；取对象字段请写 $.path 或 $.a.b，表连接请写 $lookup(键,'表','右键','列')` |
| 表名非字面量 | `第 2 个参数（表名）必须是字符串字面量` |
| 右键非字面量 | `第 3 个参数（右键）必须是字符串字面量` |
| 右键数组元素非字面量 | `第 3 个参数（右键数组的元素）必须是字符串字面量` |
| 右键空数组 | `第 3 个参数（右键）不能是空数组` |
| 列名非字面量 | `第 4 个参数（列名）必须是字符串字面量` |
| 模式非法 | `第 5 个参数（模式）只能是 'strict' 或 'loose'，得到 'x'` |
| 策略非法 | `第 6 个参数（策略）只能是 'one' / 'first' / 'all'，得到 'x'` |
| 右键 `null` + 等值 | `第 3 个参数（右键）为 null 时只支持区间谓词（$r.lo <= x and x < $r.hi）；等值连接请给右键` |
| `where` 非表达式 | `output.where: 必须是表达式字符串` |
| `where` 里用 `$r.` | `output.where: output.where 里不能用 $r.（只有 $lookup 的谓词才有右行）；要用右表字段请把条件写进 $lookup 的第 7 个参数` |
| `expand` 非字段名 | `output.expand: 必须是 out.fields 里声明的字段名，得到 'zzz'` |
| `expand` 配非行式形状 | `output.expand: 只在 output.shape 为 "list" 或 "rows" 时生效，当前是 "dict"；要展开请改用行式形状` |

### 7.2 运行期

| 场景 | 类型 | 文案 |
|---|---|---|
| 未注入任何表 | `TypeError` | `未注入表：schema 需要 ['t']；请写 run(data, tables={...})` |
| 缺某张表 | `TypeError` | `表 't' 未注入；已注入：['o']` |
| `bind` 收到裸列表 | `TypeError` | `bind 的 tables['x'] 必须是 dcg.Table，得到 list；裸列表请用 dcg.prepare` |
| `bind` 缺索引 | `ValueError` | `表 'customers' 缺少索引 id/strict/one；现有：…` |
| 策略 `one` 键重复 | `ValueError` | `右表键重复：'id' 的 1 命中多行（strategy='one'）；改用 strategy='first' 或 'all'` |
| 行数据已释放 | `RuntimeError` | `Table 的行数据已释放（keep_rows=False），无法再建索引` |
| 规格缺表名 | `TypeError` | `规格缺少表名` |
| 模式非法（运行期） | `ValueError` | `mode 必须是 'strict' 或 'loose'，得到 'x'` |
| 策略非法（运行期） | `ValueError` | `strategy 必须是 'one' / 'first' / 'all'，得到 'x'` |
| 右表行非 `dict` | `AttributeError` | `'list' object has no attribute 'get'`（**未做友好包装**，见 §2） |

**修订（v0.3）**：v0.2 的这张表带 `join:` 前缀、带表名与行号（如 `join: 表 'customers' 的第 3 行不是 dict（得到 list）`）。实现没有这些前缀：索引层的错误只知道自己手里的键，拿不到「第几行」。上表以实现为准。

---

## 8. manifest 与可复现

- manifest 键集（2.2.3）：`name`、`schema_sha1`、`artifact`、`trace_artifact`、`artifact_sha256`、`generator_version`、`runtime_helper_hash`、`lines`、`chars`、`cython`；另有侧车 `<name>.manifest.json`。
- **未新增** `tables` / `lookups` 键（v0.2 的计划未落地）。理由：表名与列名已经出现在 `schema_sha1` 覆盖的表达式文本里，单独再记一份没有消费者——产物运行时才注入表，构建期根本没有表对象。
- 不记录表数据、不记录表哈希（构建期没有表）。
- 运行时数据漂移不在校验范围：换一份右表数据，产物哈希不变。
- 产物与 `dcg.runtime` 的绑定由 `runtime_helper_hash` 覆盖；join 相关 helper 的增删会改变该哈希，进而使旧产物在新版本上校验失败——这是预期的。

---

## 9. 兼容与版本

- **两参 `$lookup` 已移除**，这是 2.2.3 唯一的破坏性变更。升级检查清单：全局搜 `$lookup(`，凡参数个数 < 3 的改成 `$` 路径（§3.3 迁移表）。
- 无 `$lookup` 的 schema：产物字节不变（golden 快照守护）。
- 3–7 参形态需要 DCG ≥ 2.2.3；更早版本读到会报参数个数错误。
- **风险：产物依赖私有名 `dcg.table._tm`。** 生成码是 `from dcg.table import _tm as _dot`，`_tm` 一旦改名，所有已生成的含表连接产物立即 `ImportError`。要么把 `_tm` 转为公开别名并长期保留，要么接受「产物必须重新生成」的约定——2.2.3 采用后者，因此该名不得随意改。
- `generate(..., cython=True)` 对含表连接的 schema：仍生成 Cython 形态，但 `$lookup` 走 runtime helper（Python 层调用），不承诺额外加速。
- `runtime.HELPERS` 当前 98 项、`runtime.__all__` 当前 108 项；`_xp` 是 `output.expand` 的 helper。两者都是 `runtime_helper_hash` 的输入，增减会波及所有产物的校验。

## 10. 测试清单

| 文件 | 内容 |
|---|---|
| `tests/test_join.py` | 46 个用例，五层：运行期对象（`Table` / `Index` / `RangeIndex` / `prepare` / `bind` / `agg`）、编译期校验（参数、字面量、模式/策略枚举、`where`/`expand`）、端到端（取列/整行/未命中/`strict`/`loose`/复合键/多跳/谓词/区间/策略）、`output.where` + `expand`、复核回归（loose 复合键、`specs_of`+`bind` 区间规格、释放后 `range` 报错、`null` 右键 + `all` 走区间索引、`shape="list"` 展开对齐、`agg` 未知种类、`prepare` 缺表文案、6 条编译期字面量文案、区间谓词反向写法、区间 + `all` + 整行、区间整行未命中、键分桶按类型区分） |
| `evidence/join_bench.py` | 两层（runtime / e2e）× 6 个 case × 三档右表；`--ci` / `--full` 两档规模（§6） |

`evidence/join_bench.py` 不在 `pytest` 收集范围内（文件名不匹配 `test_*.py`），只由 CI 的 `join-bench` job 调用。

## 11. 实施状态

| 阶段 | 内容 | 状态 |
|---|---|---|
| P1 | `schema.py`（`where` / `expand`）+ `codegen.py`（`$lookup` 识别、`run(_d, tables=None)`、谓词 lambda、`where` / `expand` 发射、`_lk_slot` 去重、`_range_pat` 区间快路径）+ `dcg/table.py`（`Table` / `Index` / `RangeIndex` / `prepare` / `bind` / `specs_of` / `_tm`） | 已交付（2.2.3） |
| P2 | `tests/test_join.py`（46 例）+ `evidence/join_bench.py` | 已交付（2.2.3） |
| P3 | 本文档 + `.github/workflows/ci.yml` 的 `join-bench` job | 已交付（2.2.3） |
| P4 | §6.4 结果表回填 | 已交付（run `42183230556`） |

2.2.3 明确不做：§2 的 8 条。其中「谓词非布尔报错」「`manifest.tables` / `lookups`」「非 `dict` 右表的友好报错」三条是**已知缺口**——它们是 v0.2 承诺过、2.2.3 未实现的部分，修复属于后续版本，不需要 RFC 变更。（原列的第四条「区间 + `all` 走 `bisect`」已实现：右键为 `null` 时走 `RangeIndex.pick_all`，见 §4.5。）
