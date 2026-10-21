# P2 独立代码评审报告：v2.1 未提交改动的可提交性判定

- 评审人：code-reviewer（第三方，未参与实现）
- 评审对象：工作区**未提交**改动（base = `HEAD d6b5b9a`，分支 main），声称是 v2.1：`items` 移除、`row:"list"` → `type:"list"` 这种 breaking change、codegen 热路径优化（字面量替 helper、`_flat_row`）
- 评审时间：2026-10-21 11:50–12:2x（Asia/Shanghai）
- 判定：**FIX FIRST**——代码主干可提交，但有 1 个"证据/文档不可提交"级 blocker 和 3 个"测试不可自证"级 blocker，必须在同一提交里修掉或从提交中剔除。
- 本文所有结论都附**可复跑脚本**（`_tmp/*.py`），不采信实现方注释与自证测试。

---

## 0. 一句话结论

`_flat_row()` 与 `_never_undef()` 两条热路径优化的**语义等价性成立**（我实跑 19+7 组用例全部与旧式转置/helper 逐值相等，无哨兵泄漏）；`items` 移除是**响亮的、带路径的报错**而非静默变更，`row` 别名**保留且与规范形逐字节同源**，因此 **2.1.0 合适，不需要 3.0.0**。真正不能直接提交的是：README 的性能数字锚在**第三方窗口**上、测试把旧行为**改写掉**而非扩充、以及一个会**吞异常**的 untracked `tests/conftest.py`。

---

## 1. 三问答复（Lead 指定的重点）

### (1) `_flat_row()` 在 `single:true` / 空输入 `_o=[]` / rows 形状下是否与旧式逐值等价？——**是，等价；不是语义回归。**

脚本：`_tmp/probe_flat_row.py`（19 个形状，逐值 + 类型比较），结论行 `FLAT_ROW_PROBE PASS: all cases equal to legacy transpose semantics`。

| 形状 | 旧式 | 新式（裸存） | 判定 |
|---|---|---|---|
| dict / 单键 | `{k:[r[0] for r in _o]}` | `{'a':_o}` | 空 `_o=[]` → `{'a':[]}`，双向一致 |
| rows / 单键 | `[{k:r[0]} for r in _o]` | `[{'a':r} for r in _o]` | 空 `_o=[]` → `[]`，一致 |
| rows / 单键 / `single=True` | `_o and {k:_o[0][0]}` | `_o and {'a':_o[0]}` | 0 行 → `[]`；1 行 → `{'a':v}`，一致 |
| dict / index + `single` | 被 `validate` 禁（schema.py:310） | 不会走到 `_flat_row` | 不适用 |

**最微妙的一点已钉住**：`_o and {...}` 的真值判断现在作用于 **非空 list**（`_o` 本身），而不是行值。因此在行值为 falsy（`0`/`None`/`False`/`[]`/`""`）时不会误判为"无行"——脚本对 5 种 falsy 逐个验证均返回 `{'a':<falsy>}` 而非 `[]`/`{}`。`_flat_row()` 的实现见 `dcg/codegen.py:1441-1451`，拼接见 `dcg/codegen.py:1604-1616`，`body()` 提前返回见 `dcg/codegen.py:1433-1437`。

风险面（非缺陷，但缺测试）：`tests/` 全库 grep `_flat_row` **0 命中**；`tests/test_codegen_snapshot.py` 的形状测试（`test_dict_shape_is_columnar` 在 :336）用的是**多字段** schema，`single` 测试（:353 `test_single_flattens_only_list_shape`）只覆盖 `shape=list`。**dict/rows 单键 + single 这条新路径完全没有回归测试兜底**——这正是本次唯一"新增分支"却零覆盖的地方。建议随提交补 3 个用例（dict 单键空输入、rows 单键空输入、rows 单键 single 假值行）。

### (2) `items` / `row:"list"` 删除是否构成 breaking change，2.1.0 够不够？——**构成 breaking，但处理得当；2.1.0 够，不必 3.0.0。**

脚本：`_tmp/probe_compat_boundary.py`。

- **`items` 不是静默变更**：`dcg/schema.py:153-158` 在未知键检查**之前**拦截，报错带精确 `path`（实测 `path='out.fields.tags.items'`）并给出两条替代写法。消息文案："`"items" 已移除：数组请用 value/expr 直接引用（如 {"type":"list","value":"$.tags"}），对象/位置行请用 "fields"`"。这是**可自愈**的 breaking：报错即迁移指引。
- **`row` 别名保留且同源**：`dcg/schema.py:173-191` 把 `row:"list"`→`type:"list"`、`row:"object"`→`type:"object"`，规范形**不留 `row` 键**；实测 `generate()` 输出与 `type:"list"` **逐字节相同（`alias byte-identical to new syntax: True`）**。错误路径也齐全（无 `fields` / 非法取值 / `row:"object"` 与 `type:"list"` 自相矛盾）。
- **为什么 2.1.0 够**：留下 2.0 的 `row` 语法即可运行且产物字节不变；`items` 是"删除但带迁移指引"的字段形态，用户一次性改 schema 即可，不涉及产物 ABI、不涉及 Python API 签名、不涉及序列化格式。按 semver，这更像 minor 中的 deprecation 收口；真正的 3.0 级事件应是"已发布产物/导入路径失效"或"行为静默改变"。
- **但文档侧不达标（见 B1）**：README `§2.4`（`README.md:295-315`）把 `row:"list"` 章节整段改写为 `type:"list"`，全仓 `grep` 不到任何"别名/兼容/已废弃"说明（`_tmp/probe_compat_boundary.py` 扫描全部 `*.md` 仅命中一处无关项）。**实现保留了兼容，而 README 假装它不存在**——这是比 breaking 更糟的文档不对称：读者以为旧写法会报错（实际能跑），或反过来在生产里继续用兼容路径（实际没有承诺）。这也是为什么不能只靠"2.1.0 够了"就放行。

### (3) 测试快照是否被顺手更新成新行为、掩盖回归？——**是，有两处必须改。**

- **B2（严重）**：`tests/test_codegen_snapshot.py` 里旧的 `items` 语义用例被**直接改写成**新语法，旧的"`items` → 一元素数组/元素位置行"行为**没有保留任何断言**。最典型的是 `tests/test_codegen_snapshot.py:564`（原 `test_nested_list_items_is_a_one_element_list`，现 `test_nested_list_row_is_a_one_element_list`）与 `:587` 起的 `test_nested_list_of_objects_composes`——新用例把原来"`items` 直接是元素 spec"改成"`type:"list"` + `fields:{o:{...}}`"，**多包了一层字段 `o`**，这不是等价迁移，而是换了一道题。改写成新题后，旧行为（元素 spec 直接产出数组）不再有任何测试守护，重构者可以任意改变它而不被发现。
- **B3（中）**：`tests/test_codegen_snapshot.py:550/:572` 用子串断言判"是否回退了 helper"：`assert "_ob(" not in src and "{'x':R['a']}" in src`。这类断言的**正样本**（`{'x':R['a']}`）同时出现在 `_ob([('x',R['a'])])` 里，只有**负样本** `"_ob(" not in src` 在真正区分两者——两个断言一强一弱拼在一起，读起来像"双保险"，实际只靠一条。`tests/test_schema.py:485` 的 `"[R[0],R[1]]" in src` 同样只是子串：只有在 `row` 是**多元素**时才勉强有区分度，单元素行 `_rl([R[0]])` 也含 `[R[0]]`（`_tmp/probe_helper_substring.py` 实测输出已记录各形状下两个子串的真实出现情况）。建议改为断言"生成的 row 表达式以 `[` 开头且不含 `_rl([`"或直接对表达式整体做正则锚定。

---

## 2. Blocker 清单（按严重度）

| # | 级别 | 位置 | 问题 | 建议 |
|---|---|---|---|---|
| **B1** | **不可提交** | `README.md:947-951`、`:955-958`、`:976`、`:1002`、`:1339` | §6.1 引用的 `v2_over_hand=1.019 / median_ratio=0.978 / noise_floor=0.0278 / window CPU-2026-10-19T18:01:11` 在**现存证据里找不到对应条目**：`evidence/row_v2.json` 当前是 12:03:38 新窗口（`v2_over_hand=1.0215`、`median_ratio=1.0`、`noise_floor=0.0968`），`evidence/row_v2_window_stdout.txt:73` 是 `1.065 FAIL`，`row_v2_w3_stdout.txt` 是 0.985 PASS，`row_v2_v2only_stdout.txt` 是 1.000。即这组数字属于**已不存在的第三方窗口**；`:976` 的锚定 sha1 `05F907B43EF5` 也不对应任何现存文件。 | 二选一：(a) 整段重写为新窗口数字并附 `evidence/row_v2.json` 的窗口号+sha1；(b) 保留历史数字但**明确标为历史窗口 `CPU-2026-10-19T18:01:11`**，并在正文单独给出 v2.1 的复核窗口。当前"保留原样+标注修订号归属"**不足够**：读者会把 1.019 当成 v2.1 的实测值。 |
| **B2** | **需修** | `tests/test_codegen_snapshot.py:564`、`:587` | 旧 `items` 用例被改写为新语法的**另一道题**（多包一层 `fields`），旧行为零守护 | 保留旧题（用迁移后的等价 schema 断言"与旧产物逐值相等"），新题追加而非替换 |
| **B3** | **需修** | `tests/test_codegen_snapshot.py:550`、`:572`；`tests/test_schema.py:485` | 子串断言强弱混搭，单元素 `_rl([x])` 也含正样本子串，区分度依赖另一条负断言 | 改为对 row 表达式做锚定/正则断言，去掉"正样本"大括号歧义 |
| **B4** | **需决策** | `tests/conftest.py`（**untracked**，79 行） | 新增的 pytest 沙箱兼容层：默认关闭（`_ACTIVE=False`，我已验证），开启后 monkeypatch `_pytest.pathlib.cleanup_dead_symlinks`、`_pytest.tmpdir.cleanup_dead_symlinks`、`tempfile.TemporaryDirectory.cleanup`，并**吞掉 `PermissionError/OSError`**；同时无条件在每次 pytest 输出 `COUNT_SUMMARY` 行。这是**测试基础设施的全局副作用**，且无自测。 | 不要与 v2.1 一起提交。要么单独提交并在 README/§9 说明用途，要么放 `bench/` 当外部工具用 `-p` 加载。若保留：把吞异常的范围收窄到"仅 basetemp 清理"，且**必须在 stdout 标出 `[sandbox-compat: ON]`**（现在只有 OFF 才标，反向标注更危险）。 |
| **B5** | **需补** | `tests/`（全库无 `_flat_row` 引用） | 本次唯一新增分支 `dict/rows 单键` 零回归测试 | 随提交补 3 个用例（见 §1(1) 末） |
| **B6** | **文档** | `dcg/schema.py:173` vs `README.md:295-315` | 代码保留 `row` 兼容别名 + 注释"已废弃"，README 全篇无提及；无 CHANGELOG/升级指南 | README §2.4 末尾加 3 行："v2.0 的 `row:"list"` 仍可用并等价于 `type:"list"`，计划在 2.x 后续移除" |
| **B7** | **证据链** | `evidence/accept.py`（校验 window 不校验 sha1） | `row_v2.json` 的 `window` 与 `sha1` 是两个独立字段，accept 只校验 window ⇒ 换源码后旧窗口可被"重新贴牌" | accept 增加：`sha1` 与当前工作区不一致时拒收（至少 WARN 并落盘 `sha1_differs`） |
| **B8** | **小** | `evidence/accept_perf_t11.txt` ≡ `evidence/accept_v21_handoff.txt`（SHA1 同为 `8C64DBF1423CA326E4AFFE5E35C71E440872CC27`）；`README.md:1302` 只登记 `accept_final.txt` | 新证据文件是旧内容的复制品，且未被 README 目录树登记 | 要么删除重复文件，要么注明"同一窗口的复核副本"；README §8.1 目录树补齐 |

---

## 3. 我实跑验证过的事实（可复现清单）

运行方式统一为：`$env:PYTHONPATH='C:\Users\black\Desktop\DCG'; python -X utf8 _tmp\<脚本>.py`。

| 脚本 | 验证内容 | 结果 |
|---|---|---|
| `_tmp/probe_flat_row.py` | `_flat_row` 19 组形状 vs 旧式转置，含空输入/`single`/falsy 行值 | 全 PASS，逐值+类型相等 |
| `_tmp/probe_row_alias.py` | `row:"list"` 归一化、生成字节等价、4 条错误路径 | 归一形无 `row` 键；`generate` 逐字节相同 |
| `_tmp/probe_compat_boundary.py` | v2.0 写法（items / row）在当前实现的真实行为 + 全仓 md 的兼容说明扫描 | `items` 报错带 path；`row` 别名静默可用；**md 中零兼容说明** |
| `_tmp/probe_undef_literal.py` | `_never_undef` 判定是否会让 `UNDEF` 哨兵泄漏进产物（7 组：object/row、const、default、any 缺键、py 块、嵌套作用域） | 无泄漏：`_ob`→`{}`、`_rl`→`[None]`、字面量路径正确 |
| `_tmp/probe_helper_substring.py` | 各形状下 `_ob(` / `_rl([` 子串的真实出现情况，验证 B3 | 单元素 `_rl([x])` 确实含 `[x]` |
| `_tmp/probe_readme_24.py` | README:247-254 的产物/运行结果块是否仍可复现 | **逐字节一致，MATCH True** |
| `_tmp/probe_readme_artifacts.py` | README:282-290、:301-311 的产物块 vs 当前 codegen | 一致（产物含 `_ob` 丢键与 `_flat_row` 裸存 `{'doc':_o}` 两处都已核对） |
| `_tmp/probe_lead_delta.py` | 把 codegen 三处 `§3.5「B 类」/§3.6` 注释放回 HEAD 措辞 → 是否回到 `e6b008afce4d` | **是**：81034B / `e6b008afce4d7b944593c8ee2b8502756faab592`，与 pytest 证据记录的 sha1 完全一致 ⇒ 该 delta **可在字节级证明为注释/文档串改动** |
| `_tmp/probe_lead_delta_ast.py` | 上一步两版本的 AST 差异 | 仅 2 处字符串常量（1 个 docstring + 1 个 `Unsupported` 消息文案），无可执行逻辑差异 |
| `_tmp/probe_conftest_review.py` | 新 `tests/conftest.py` 的开关默认值与副作用面 | 默认 `_ACTIVE=False`；开启后 patch 3 处全局状态并吞 `PermissionError/OSError` |

**关于评审期间 `dcg/codegen.py` 的时间线（已与 Lead 核对）**：评审开始时其 sha1 为 `e6b008afce4d` / 81034B（与 `evidence/pytest_perf_t11.txt` 记录一致），评审中变为 `d9767fe00638` / 81095B（mtime 12:07:28）。修改者为 **Lead**，经上面的还原实验证明**仅 3 处注释/文档串改动，零逻辑变更**。因此：
- 我此前基于 `e6b008afce4d` 做的语义验证结论对当前字节**同样成立**；
- 但**测试证据仍需重跑**：`evidence/pytest_perf_t11.txt` 的 843 passed 对应的是改动前字节，虽然逻辑等价，提交时证据应与提交字节一一对应（Lead 已同意重跑并写 `evidence/pytest_v21_final.txt`）。

---

## 4. 其它已核查事项（结论：不改）

- **版本号**：`pyproject.toml:7` `2.0.0`→`2.1.0`，全仓仅此一处版本声明；`dcg/__init__.py` 无 `__version__`（`getattr(dcg,'__version__','<none>')` → `<none>`），包未 `pip install`，`importlib.metadata.version('dcg')` → `PackageNotFoundError`。**建议**：给 `dcg/__init__.py` 加 `__version__ = "2.1.0"` 以便运行时自证（非 blocker）。
- **`_never_undef` 里 PY/PYBLOCK 的裸名递归分支**（`dcg/codegen.py:718-735`）：实测 `py{}` 块内**不允许**裸字段名（契约 §3.7；`dcg/jx/compiler.py` 抛 `Unsupported: 代码块里引用了未声明的名字 'a'`），因此该分支只在反引号内联 PY 段可命中，测试覆盖偏薄；但实现保守（拿不准就回退 helper），**不会造成语义错误**，只列为"覆盖缺口"。
- **`_emit_object` 的 `if "default" in s and "fields" not in s`**（`dcg/codegen.py:1291`）：`fields` 与 `default` 在 schema 层已互斥（`dcg/schema.py:196-198` 报"结构字段（fields）不能设 default"），故该守卫冗余但正确。
- **`_ar` 保留**（`dcg/runtime.py:440-446`）：注释说 v2.1 起只服务 JSONata 数组字面量；实测 schema 嵌套数组路径确实不再生成 `_ar(...)`��**既有产物导入兼容**这一理由成立，保留是对的。
- **`examples/` 两个生成物**仅 `return{`→`return {`（空格）导致 sha1 变化，属产物重新生成的正常漂移；`examples/readme_blocks.py:93` 的打印标题仍写"嵌套 fields / items --"，以及 `:125` 的 `items_demo` 变量名残留——**小瑕疵，建议顺手改**。
- **migrate 生成新语法**（`dcg/migrate.py:1189-1191` 用 `{"type":"list","fields":...}`）：正确，不生成已移除的 `items`/`row`。
- **README:77 附近的 `items` 均属 migration_report 的报告字段名**，已在 `README.md:1055` 显式注明与 schema 键无关——不是残留，无需改。

---

## 5. 提交建议（给 Lead 的执行顺序）

1. 修 **B1**（§6.1 数字口径，必须二选一处理干净）；
2. 修 **B2/B3**（测试：旧题保留 + 子串断言锚定）；
3. 补 **B5**（`_flat_row` 三形状回归用例）；
4. 决定 **B4**（`tests/conftest.py` 单独提交或剔除）；
5. 补 **B6**（README 一行兼容说明）；
6. Lead 重跑 pytest + accept，写入带 sha1 的 `evidence/pytest_v21_final.txt`，README §8.1 目录树登记（**B8**）；
7. 之后再 `git add`/commit，提交信息注明 breaking（`items` 移除）与兼容别名（`row`）现状。

若 1–3 都不想现在做，则退一步，**只提交 `dcg/` + `examples/` + `pyproject.toml`**（代码逻辑已验证无误），把 `README`/`tests`/`evidence`/`conftest.py` 拆到下一个提交——但那样测试与实现不同步，会留下一个"实现已改、测试未改"的中间态，**不建议**。

> 结论重申：**FIX FIRST**。代码本身可以提交（`_flat_row`/`_never_undef` 等价性我已实跑证伪"存在回归"的假设）；不能直接提交的是"数字不对口径、测试换了题、基础设施夹带私货"这三件套。
