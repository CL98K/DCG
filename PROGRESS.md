# PROGRESS — DCG v2.1.0 项目进度

> 本文件长期入库维护（用户 m02343 决定）：每轮开工先看这里，收尾时更新。
> 规则来源：`AGENTS.md` v3.0（状态锚点、检查点汇报、滚动 ETA、防静默熔断）。
> 裁决记录：`_tmp/fix_plan/DECISIONS_v*.md`（不入库；`_tmp/` 已于第二十六轮清理，历史裁决记录不再保留）。

## 一、当前状态区（每个检查点覆写）

- **task**: 第四十轮 — 全部基准重测（用户 m03736「所有基准测试全部重测一遍」）
- **step**: 已完成并提交
- **done**:
  - **三套基准全部重跑**：`evidence/bench_run.py`（13 项主协议，跑 2 轮做 A/A）、`evidence/bench_cython.py`（13 项三路对照，`ALL_EQ: True`）、`evidence/bench_cols.py`（80 行列数边界）。三份证据 JSON 各自加了 `patched` 字段记录重测时点与期间改动
  - **发现并修复一处被旧快照掩盖的回归（`dcg/runtime.py`）**：`$sort` 的 `_so` 自第二十八轮起把归并排序的 `merge` / `msort` 写成函数内闭包，**无比较器**的快路径每次调用都要新建两个函数对象，`sorted()` 一步从 0.15 涨到 0.24 µs/次；`hires_sort` 因此一度到 1.50~1.59×。把两者提成模块级 `_sort_merge(left, right, comp)` / `_sort_msort(xs, comp)` 后回到 0.194 µs（1.32×），`hires_sort` 落到 **1.25×**（旧快照 1.30×）；比较器路径逐例对拍一致（`$a-$b`→`[2,1,3]`、`$a>$b`→`[1,2,3]`、`$a<$b`→`[3,2,1]`、空数组、单元素、`['b','a']`、`_so(1)`→`[1]`、`_so(UNDEF)`→undefined）
  - **`hires_reduce` 的 +17% 漂移已定位为真实**：9 轮隔离实测 dcg 0.5847~0.6694 µs/行（中位 0.5963）、手写 0.1925~0.2145（不漂）、ratio 中位 3.025；拆解得 bound 0.6108 vs nobind 0.5466 vs hand 0.2075，`_rd` 单独 0.4338、手写循环 0.3614、裸 `+` 0.3017、`sum()` 0.1673 —— 成因是每元素两层 Python 调用（`lambda` + `_ad`），第三十五轮的 helper 局部绑定在该用例上净亏。**已有「保留 P1」裁定，本轮不改**，只在 §5.5 如实披露
  - **`验收报告.md` 全量同步**：§5 数据源行（新 `generated_at`）、§5.1 表 12 行的 DCG/手写/比值/判定、判定分布表（2 快 + 5 持平 + 5 慢）、§5.1 逐项归因表 5 行的数字、§5.3 `order_pipeline` 行、§5.5 新增「全量重测」条目、§5.6 Cython 表 13 行 + 结论 1/2/3 的数字 + 快照口径段、§7.1 R1/R2/R3 数字、§8 的 Cython 摘要行
  - 验证：全量 `1458 passed / 10 skipped (total 1468)`（计数无漂移）、accept `GATES: 17 PASS / 0 FAIL / 0 SKIP`、文档守护 110 passed / 1 skipped
- **doing**: 无
- **blocked**: 无
- **risk**: 低（改动 = 一个 runtime 函数提级 + 三份证据 JSON + 报告数字同步；语义由 1468 例与 17 门禁双背书）
- **裁定（2026-10-29T16:04+08:00，用户 m03964「局部绑定 不动，保留」）**：helper 局部绑定作为整体机制保留，**不按用例分化**、不为 `hires_reduce` 的净亏单独回退；该净亏已在 `验收报告.md` §5.5（9 轮隔离实测）与 §7.1 R1 中如实披露并注明不再作为待优化项
- **next**: 用户侧 `git push origin main`（本地领先 15 个提交）；`AGENTS.md`（用户自改 v3.1）继续排除
- **eta**: 无（本轮已收尾）
- **updated_at**: 2026-10-29T14:48:23+08:00

**第三十九轮归档（已完成并提交 `5598251`）**
- 3 files +43/−143：R1–R4 定性为「已知弱点（已接受的现状，不是待办清单）」；删除 `_infer_types` / `_infer_node` / `_infer_as` 三个未被调用的标量推导方法（净 −114 行）
- 全量 `1458 passed / 10 skipped (total 1468)`、accept `GATES: 17 PASS / 0 FAIL / 0 SKIP`

**第三十八轮归档（已完成并提交 `8c79456`）**
- 2 files +20/−18（仅 `验收报告.md` + `PROGRESS.md`），**零代码改动**：P4 聚合特化评估后结论「不做」，登记为 R4（6.66×，结构性弱点）
- 全量 `1458 passed / 10 skipped (total 1468)`、accept `GATES: 17 PASS / 0 FAIL / 0 SKIP`

**第三十七轮归档（已完成并提交 `1fd728b`）**
- 6 files +292/−40：A 项列表元素类型自动推断（`_infer_elem_types` + `_KIND_TO_TYPE`），实测 1.54×、产物零 helper；`test_e2e_flight.py` 新增 4 条守护、改写 1 条、同步 3 条断言
- 全量 `1458 passed / 10 skipped (total 1468)`、accept `GATES: 17 PASS / 0 FAIL / 0 SKIP`

**第三十六轮归档（已完成并提交 `8ed68c1`）**
- 5 files +210/−30：`$reduce` 累加形态展成行级 `for` 循环（`_reduce_inline`），实测 1.60×；`TestReduceInline` 15 条守护；协议产物逐字节不变
- 全量 `1454 passed / 10 skipped (total 1464)`、accept `GATES: 17 PASS / 0 FAIL / 0 SKIP`

**第三十五轮归档（已完成并提交 `4b7cbd8`）**
- 7 files +184/−20：helper 局部绑定（`_g=globals()` + `_gt=_g['_gt']`），收益约 1%（8.2 ns/次调用）；`TestOptPassLocalBinds` 4 条守护；README 13 个产物块重写；接口 §4.2 / 架构 §7.5 各 1 段
- 全量 `1439 passed / 10 skipped (total 1449)`、accept `GATES: 17 PASS / 0 FAIL / 0 SKIP`

**第三十四轮归档（已完成并提交 `09d2fde`）**
- 4 files +20/−24：五份文档正文 12 处「第 X 轮」改中性表述（README 与 `JSONata 语法.md` 本就 0 处）；`PROGRESS.md` 与 `perf_protocol.json` 的 `patched.round` 未改
- 全量 `1435 passed / 10 skipped (total 1445)`、accept `GATES: 17 PASS / 0 FAIL / 0 SKIP`

**第三十三轮归档（已完成并提交 `5a43a36`）**
- 9 files +504/−30：`itype` 元素类型声明（`dcg/schema.py` + `codegen._elem_type`）+ 常量折叠 + CSE；新增 19 条回归，collected 1426→**1445**（`test_e2e_flight.py` 130→149）；13 项协议产物与 `perf_protocol.json` 的 `src` 逐字节相同（协议用例未声明 `itype`），§5.1 判定不变
- 全量 `1435 passed / 10 skipped (total 1445)`、accept `GATES: 17 PASS / 0 FAIL / 0 SKIP`

**第三十二轮归档（已完成并提交 `be4b927`）**
- 2 files +15/−13：删掉验收报告 §7.1 的 **R5**（`evidence/` 历史报告含过期锚点）与 **R7**（跳过数随环境浮动，口径已在 §1.2/§3.4 完整记载，属重复），并把剩余条目重排为连号 **R1 / R2 / R3**（原 R3→R2、原 R6→R3）；全量 `1416 passed / 10 skipped (total 1426)`、accept 17 PASS / 0 FAIL / 0 SKIP

**第三十一轮归档（已完成并提交 `8b63f07`）**
- 2 files +18/−12：删掉验收报告 §7.1 已结案的 **R2**（`aggregate` 行容器形态，已修：`agg_container` 1.03× 持平）与 **R4**（Cython 加速未验证，第十六轮已实测结案：13 项中位数 1.02×）；同时改写 §5.6「背景」里的悬空引用「§7.1 R4 此前记…」

**第三十轮归档（已完成并提交 `a08d60e` + `2ef451f`）**
- `a08d60e`（3 files +21/−13）：文档对齐终检 —— `JSONata 语法.md:43` 的 `$round` 不内联理由改为「舍入规则其实一致（都 half to even），真因是十进制移位」；架构 §6 策略三微基准表「优化后」列补 `_as_list(...)` 形态并加注；PROGRESS 同步
- `2ef451f`（1 file +101/−5）：`AGENTS.md` 升 v3.1（防格式崩塌强约束、新增规则 1A），用户要求入库

**第二十九轮归档（已完成并提交 `ee944d8`）**
- 9 files +146/−109：`$map`/`$filter` 首参不可证数组时改走 `_as_list()` 运行期单值提升（删掉第二十八轮的退回门禁）；`HELPERS` 86→87、`__all__` 96→97、实际引用 84→85、测试计数 1425→1426
- perf 补测（只重测产物变化的 2 项，其余 10 项逐字节相同）：`hires_filter` 0.4370→0.4562 µs/行、1.21×→1.37×；`hires_map` 0.4153→0.4629、1.81×→1.85×；`perf_protocol.json` 加 `patched` 留痕
- 全量 `1416 passed / 10 skipped (total 1426)`、accept `GATES: 17 PASS / 0 FAIL / 0 SKIP`

**第二十八轮归档（已完成并提交 `04e7d5f`）**
- 12 files +471/−163：与 JSONata 1.8.6 全量对齐（P0 `_ro` 银行家舍入 / P2 字符串与数值边界 / P1 单值提升 / P3 `_in` 与 and/or / 样例与文档同步），P4/P5 转 §1.4.1 声明
- 全量 `python -X utf8 -m pytest -q`：`1415 passed / 10 skipped (total 1425)`、exit 0，计数契约无漂移
- `python -X utf8 evidence/accept.py`：`GATES: 17 PASS / 0 FAIL / 0 SKIP`
- 差分复跑（jsonata@1.8.6，170 例）：`same 126 / type_only 0 / value_diff 9 / dcg_err_only 3 / std_err_only 28 / both_err 4`（改前 `same 98 / value_diff 34 / type_only 2 / dcg_err_only 4`）；剩余 9 条 value_diff 中 2 条是 `$random`/`$shuffle` 非确定，7 条属 §1.4.1 声明的输出单值塌缩；`dcg_err_only` 3 条属已声明第 5 条（缺键 KeyError）×2 + §1.4.1 第 3 条（无函数值）×1；`std_err_only` 28 条全部属 §1.4.1 第 2 条（函数级宽松）

**第二十七轮归档（已完成并提交 `eb06f72`）**
- `$lookup` 数组入参递归收集命中值（6 files +53/−17）；全量 1415 passed / 10 skipped (1425)、accept `GATES: 17 PASS / 0 FAIL / 0 SKIP`。

**第二十六轮归档（已完成并提交 `d305afb`）**
- 清理 `_tmp/`（2684 文件 / 16.41 MB）、`build/`（51 文件 / 5.28 MB）、`build_tmp_t10/`、`_tmp_head_verify/`（140 文件 / 2.39 MB）、`__pycache__/` ×18、`.pytest_cache/`（部分），约 24 MB；仅 `PROGRESS.md` 变更；`.pytest_cache/` 因沙箱 ACL 拒绝仍残留（坑 12）。

**第二十四轮归档（上一轮，已完成并提交 `c681df2`）**
- **task**: 第二十四轮 — 14 项评审（A 档 8 + B 档 6）
- **step**: 全部 14 项落盘 → 生成器同步 → 守护/回归/accept 全绿 → 已提交
- **done**:
  - **A1+A14** `验收报告.md` §4.2 `$boolean` 行：`bool()（可证时）/ _B（不可证时）` → `_B`（可证真值域时内联 `bool()`，不走 helper），与表头「helper 兜底名」一致
  - **A2** §4.2 表前加「输入列只用于展示 helper 兜底名的调用结果，**不表示该输入一定走 helper**」
  - **A3** 接口 §4.4 计数说明补「`_pg`、`_b` 不产出，实际会被生成器引用的是 **84** 项」（实测 `len(HELPERS)==86`、两者从不出现于产物），验收脚注「口径详见接口 §4.4」因此成立
  - **A4** 验收 §2.2 #2 基座表述收紧：required 基座是裸链（`xs[$.i]` → 行首 `xs=R['xs']` 后 `_ix_get(xs,_ix_get(R,'i'))`；`$.xs[$.i]` → `_ix_get(R['xs'],_ix_get(R,'i'))`），只有 optional 基座才 `_ix_get`
  - **A5** 架构 §7.5：`46 个 A 类行内函数对应的算子级 helper` → `A 类 46 个行内函数对应的运行期 helper`（46 是函数数，不是 helper 数）
  - **A6** README §3.1：`$count 才内联：…` → `$count 等**可证类型**的函数才内联：…；$length 裸名 str → len(...)；$uppercase 裸名 str → .upper()`
  - **A7** JSONata §3 `$.a[hour].v` 说明改为「`hour` 是 `value: "$.hour"`、`type: "str"` 的 required 字段（产物行首 `hour=R['hour']`）」，与输入行 `hour: 'x'` 一致；`tests/gen_jsonata_manual.py:325` 同步（note + `fields_extra` 改 value 形态）
  - **A8** 验收 §8 ③：`另对 5 份文档` → `另对 5 份文档（含本报告）`
  - **B9** 10 键顺序统一为接口 §3.9 顺序（`eval range regex bind_pos bind_join sort transform reduce group path`）：验收 §4.1 表、§4.5 正文、附录 A 三处 + JSONata §13.7（并注明「顺序同接口文档 §3.9」）
  - **B10** JSONata §1.4：删掉与接口 §3.6/架构 §6.5 的循环引用，改为「接口 §3.6 与架构 §6.5 均以本表作为显式偏离的唯一来源」
  - **B11** 接口 §4.4 分类名 `哨兵与谓词` → `谓词（哨兵不计入）`
  - **B12** JSONata §13.2 补「`$not` 的 `_nt` 也属函数级 helper，因此不在下面的一元行里」
  - **B13** 接口 §4.1 `lines`/`chars`：删掉「见架构 §7.3 与 README §7」，改「历史决策见 git 记录」
- **doing**: 全量回归（job pwsh-257 首轮已 1415/10/1425 exit 0；gen 同步后再跑一轮确认）→ commit → 📊 汇总
- **blocked**: 无
- **risk**: 低。B9 只改列举顺序不改集合；A3 的新句与既有「86 项/谓词也计入」说明不冲突
- **next**: 回归全绿 → `git commit` → 汇总
- **eta**: 约 2 分钟
- **updated_at**: 2026-10-27T11:17+08:00

## 二、变更日志（追加，不覆盖）

- **2026-10-29T10:06+08:00｜第三十六轮**：性能 P2 —— `$reduce` 累加形态展成行级循环。`$reduce(arr, function($a,$b){$a + $b}, 0)` 这种「单一 `$a OP $b`、`OP ∈ {+,-,*}`、init 是字面量、第一实参可证为数组」的形态不再发 `_rd(arr, lambda a,b:_ad(a,b), 0)`，改为在行级 defs 里发 `_t1=<init>` / `for _t2 in <arr>:` / `_t1=_ad(_t1,_t2)`，字段表达式只返回临时量 —— 省掉 `_rd` 的循环壳与**每元素一次 lambda 调用**。`OP` 仍发 `_ad`/`_sb`/`_ml` helper 而不是裸运算符：helper 保有 JSONata 的 `'a'+'b'` / `[1]+[2]` / `True+1` 语义与 undefined 传播，4000 例模糊测试证明裸 `+` + 自定义 TypeError 分支结果与抛错类别都等价、只差文案，不值 0.05 µs/行。新增 `_decl_of(node)`（`FIELD` 或 `INDEX` 首段 → `self.fields`），`_is_listy` 改为查它且**要求 required**（optional 字段可能是 undefined，`for` 遍历会 `TypeError`；这条被 `test_optional_list_ignores_itype` 抓出并已修）。**踩坑**：初版用 `_mentions_position(body, params)` 判位置引用，而它判的是「回调第 2 形参被引用」——`$reduce` 的第 2 形参就是累加项，`$a+$b` 必然引用，导致内联永不触发；改用 `_mentions_position_call(body)`。**实测 1.60×**：20 万行 × 2 元素、7 遍取最小值 0.594 → 0.372 µs/行（手写 `sum()` 0.241）；3600 例随机混合类型（3 算子 × 3 init × int/float/str/bool/None/undefined/list/tuple/dict）内联与 `_rd` 逐值一致、抛错类别一致。**协议读数不变**：`hires_reduce` 用 `$reduce($.6, …)` 根元组下标，`_is_listy` 证不出数组 → 按设计退回 `_rd`，13 项协议产物逐字节相同，§5.1 判定不变。**同步**：`TestReduceInline` 15 条守护；接口 §4.3 内联表改 `$reduce` 行 + 新增一行 + `_as_list` 行补注；验收 §5.5 加补测、§7.1 R1 补「去掉调用层」；计数 1449→**1464**（`test_e2e_flight.py` 168）。**验证**：全量 `1454 passed / 10 skipped (total 1464)`、accept `GATES: 17 PASS / 0 FAIL / 0 SKIP`。

- **2026-10-28T23:52+08:00｜第三十五轮**：性能 P1 —— helper 局部绑定。`from dcg.runtime import *` 只把 helper 放进模块全局，函数体里每次调用都是 `LOAD_GLOBAL`；产物改为在 `run()` 前发一行 `_g=globals()`、在函数开头把**实际用到**的 helper 各绑一次（`_gt=_g['_gt']`），此后同名在函数内是局部变量。名字按字典序发出（同一 schema 产物逐字节稳定）；一个 helper 都不用的裸运算产物连 `_g` 行都不发。**实测收益约 1%**：CPython 3.13 的 `LOAD_GLOBAL` 已有内联缓存，隔离实验测得每次调用只差 8.2 ns（74.0→65.8 ns），200k 行 9 轮取最小值 `hires_filter` 1.008×、`hires_map` 1.125×、`hires_reduce` 0.960×、`optwide_hit` 0.993×、10 个 helper 的合成行 1.014×，落在噪声内；helper 越密集收益越大。**踩坑**：第一版写成 `_gt=_gt` 会 `UnboundLocalError`（函数内赋值即整个函数的局部名，右值解析到自己）。**同步**：`tests/test_e2e_flight.py::TestOptPassLocalBinds` 4 条守护；README 13 个产物块按新产物重写；接口 §4.2 加「helper 的局部绑定」、架构 §7.5 加「策略三补充」；计数 1445→**1449**（`test_e2e_flight.py` 149→153）。**验证**：全量 `1439 passed / 10 skipped (total 1449)`、accept `GATES: 17 PASS / 0 FAIL / 0 SKIP`。
- **2026-10-28T16:34+08:00｜第三十四轮**：按用户要求删除文档正文里的「第 X 轮」字样。范围＝ README / 接口 / 架构 / `JSONata 语法.md` / 验收报告 五份文档，共 **12 处**：「第二十九轮补测」→「补测」、「第三十三轮补测」→「补测」、「（第三十三轮；只改产物文本…）」→「（只改产物文本…）」、「策略四…（第三十三轮）」→ 去括号、「（第二十九轮引入，替代…）」→「（替代…）」、「语义与第二十九轮以前完全一致」→「语义与引入 `itype` 之前完全一致」、「（第九轮裁决 B）」→「（历史裁决 B）」、「上一快照（第十三轮）」→「上一快照」、「第十六轮据此补齐实测」→「后续已据此补齐实测」、「第二十九轮之后」→「引入 `_as_list()` 提升之后」、「第三十三轮补上第三条可行路」→「现行版本补上第三条可行路」。复查五份文档残留 0 处。**未改**：`PROGRESS.md`（变更日志本身按轮次组织，61 处）与 `evidence/perf_protocol.json` 的 `patched.round`，待用户裁定。验证：文档守护 110 passed / 1 skipped、全量 `1435 passed / 10 skipped (total 1445)`、accept `GATES: 17 PASS / 0 FAIL / 0 SKIP`。
- **2026-10-29T13:51:57+08:00｜第三十九轮**：收尾清零（用户 m03615 五项裁定）。**第 2 项**：`验收报告.md` §7.1 标题改「已知弱点（已接受的现状，不是待办清单）」+ 定性 blockquote（四条都不是「还没做」而是「评估完结论是维持现状」；除非提出改契约/放语法的新路径否则不修；验收判定不包含这四条），表头「处置」→「结论与依据（不再修复）」，R2 由「遗留弱点未根治」改「非量级性弱点」并补「正是 R1 的下界」。**第 4 项**：删除 `dcg/codegen.py` 死代码 `_infer_types` / `_infer_node` / `_infer_as` + `_INFER_NUM` + `self._inferred` + `_decl_type` 的不可达分支，净 −114 行，改写 4 处注释指涉。**自造回滚**：首次删除用「下一个 def」圈边界，误删仍在使用的 `_node_is_num` / `_const_field_name` / `_elem_type`，全量 166 failed，从 `git show HEAD:` 精确切片恢复后转绿。**验证**：`1458 passed / 10 skipped (total 1468)`（计数无漂移）、accept `GATES: 17 PASS / 0 FAIL / 0 SKIP`。
- **2026-10-29T13:45+08:00｜第三十八轮**：P4 收窄为「聚合特化」，结论是**不做**，登记为 R4。**侦察最大发现**：`aggregate=True`（`dcg/schema.py:36-39`）是**列容器**语义（每个子键聚成一列、长度=行数），不是「N 行压成一个标量」；`bench_run.py` 的 3 个 `agg_*` 用例测的都是列构造 —— **DCG 没有跨行聚合的语义载体**，我上一轮被用例名里的 `agg` 误导。**A 方案侦察**：`_hook_text` 把 `after` 编成 `(lambda D:<expr>)(_o)`，`D` 是组装完成的结果而非原始输入；`$.*`/`$[1]` 取列被 `dcg/jx/policy.py` 结构性拒绝（`$.*` 报 `不支持的起始记号 '*'`）。可行写法只有 `$sum($map($, function($r){$r[1]}))`。**A3 实测（100k 行）**：0.1907 vs 手写 `sum()` 0.0286 µs/行 = **6.66×**；但拆解表明聚合段本身只占 0.0507、已接近纯 Python `sum(map(lambda))` 的 0.0434，差距几乎全在「按 shape 建全量结果列表再丢掉」≈0.14 µs/行。**推翻我的优化直觉**：去掉中间列表反而更慢（`sum([推导式])` 0.0275 vs `sum(生成器)` 0.0288），所以「聚合特化」无空间，真正的方向是避免这种 shape。三条出路（after 加原始数据形参 / 放开取列语法 / map+sum 组合）均不采纳。**零代码改动**，文档仅 `验收报告.md` §7.1 +1 行 R4。**验证**：全量 `1458 passed / 10 skipped (total 1468)`、accept `GATES: 17 PASS / 0 FAIL / 0 SKIP`。
- **2026-10-29T11:02+08:00｜第三十七轮**：A 项——列表元素类型**自动推断**（用户 m03276 开工、m03410 裁定「保持现状」）。**机制**：`codegen._infer_elem_types()` 从**使用方式**反推 `type:"list"` 字段的元素类型，证据只有三种——列表被 `$sum`/`$average`/`$max`/`$min`/`$number` 用过；`$map`/`$filter` 回调形参参与算术或顺序比较；参与只收字符串的函数。命中即写进 `_inferred_elem`（值为 schema 类型名，经新增的 `_KIND_TO_TYPE` 从类别反查——**初版直接把类别 `"num"` 存进去，`_TYPE_KIND.get()` 查不到，导致「推断成功但产物仍发 helper」，这是本轮最大的坑**）。`_elem_type()` 改为显式 `itype` 优先、推断兜底。**为什么这条能默认开启而标量推导 `_infer_types` 不能**：标量推导把 `type:"any"` 字段当已声明类型，裸运算会退化异常语义；元素推导只在字段已声明 `type:"list"` 时成立，违约后果与显式 `itype` 完全一致（同抛 `TypeError`），属「把已有写法的必然含义读出来」，不是运行时探测。**闸门**：冲突（同一字段既当数值又当字符串）、`optional`、`fields`、显式 `itype` 一律不推。**实测 1.54×**：100k 行 × 2 元素 1.3277 → 0.8624 µs/行，产物零 helper、无 `from dcg.runtime import *` 行。**语义验证**：正常数据三形态（显式/推断/无证据）逐值一致；脏数据（元素含 `None`）显式与推断都抛 `TypeError`，无证据形态按 undefined 过滤。**协议读数不变**：13 项协议用例用 `$.6` 直取根元组下标，不是声明字段，推断帮不到它（如实披露）。**计数**：新增 4 条 + 改写 1 条 + 同步 3 条断言，collected 1464→**1468**（`test_e2e_flight.py` 168→172）。**验证**：全量 `1458 passed / 10 skipped (total 1468)`、accept `GATES: 17 PASS / 0 FAIL / 0 SKIP`。
- **2026-10-28T15:03+08:00｜第三十三轮**：性能 P0 剩余（`itype` 元素类型）+ P1（常量折叠 / CSE）。**`itype`**（用户裁定键名，`dcg/schema.py`）：`type:"list"` 字段的可选键，声明元素类型；`codegen._elem_type()` 把它注入 `$map`/`$filter` 回调形参，`_var_proven`/`_kind` 因此可证形参类型，回调体里的算术/比较发裸运算（`[v for v in xs if (v>1)]`、`[(v*2) for v in xs]`），每元素省一次 helper 调用；它是**声明式承诺**（数据里出现 `null`/异类元素即 `TypeError`）。**常量折叠**：纯字面量 `+ - *`/`&`/同类比较/`and`/`or` 折成字面量，`/`、`%`（会提前 `ZeroDivisionError`）与 `true = 1`、`null & 'x'`、`true + 1`（1.8 与 Python 语义不同）不折。**CSE**：同字段内出现 ≥2 次的同构纯调用只算一次并提到行级 `_tN`；不纯函数与字段路径/字面量/`py{}` 不参与。**实测**：`itype` 收益取决于每行元素数 —— 协议用例数组只有 2 个元素，交叉测量 3 轮取中位数得 `hires_map` 1.12×、`hires_filter` 1.00×、`hires_reduce` 1.01×（落噪声带），L=128 微基准约 1.9×；13 项协议产物与 `perf_protocol.json` 的 `src` 逐字节相同，故 §5.1 判定不变、不重跑协议。**计数**：新增 19 条测试，collected 1426→**1445**（`test_e2e_flight.py` 130→149），五份文档与 `test_sync_contract` 全同步。**验证**：全量 `1435 passed / 10 skipped (total 1445)`、accept `GATES: 17 PASS / 0 FAIL / 0 SKIP`。
- **2026-10-28T13:58+08:00｜第三十二轮**：继续精简 `验收报告.md` §7.1 —— 删掉 **R5**（`evidence/` 历史报告含过期锚点）与 **R7**（跳过数随环境浮动，该口径 §1.2/§3.4 已有完整记载），并把剩余三条**重排为连号**：原 R3「遗留弱点未根治」→ **R2**、原 R6「`order_pipeline` 慢 2.78×」→ **R3**、R1 不变。现在该表只有 R1/R2/R3 三行、无空档。引用复查：`验收报告.md` 全文除这三行外无任何 `R#` 字样（§8 按名称引用），其余四份文档零引用。全量 1416 passed / 10 skipped (total 1426)、accept 17 PASS / 0 FAIL / 0 SKIP。
- **2026-10-28T13:34+08:00｜第三十一轮**：删掉 `验收报告.md` §7.1「未修的已知问题」里已结案的 **R2**（`aggregate` 行容器形态，已修：`agg_container` 1.03× 持平）与 **R4**（Cython 加速未验证，第十六轮已实测结案：13 项中位数 1.02×、`order_pipeline` 0.71×）。表格现为 R1/R3/R5/R6/R7，保留原编号不重排（§8 等处仍引用 R1·R3）；同时把 §5.6「背景」里的悬空引用「§7.1 R4 此前记…」改写为「§7.1 曾把…记为未修问题（已结案，故已从表中删除）」。全库无其它 R2/R4 引用（`PROGRESS.md` 归档行的「结案 R4」属历史陈述）。全量 1416 passed / 10 skipped (total 1426)、accept 17 PASS / 0 FAIL / 0 SKIP。
- **2026-10-28T11:55+08:00｜第三十轮**：文档对齐终检，修两处与代码不符的旧口径。**`JSONata 语法.md:43`**（§1.3 产物形态）把「Python `round()` 是银行家舍入，与 JSONata 的 half-away-from-zero 不同」改为「舍入规则其实一致（都 round half to even），真因是 1.8 带 `precision` 时走 `toString()` 十进制移位（`$round(2.675,2)` = 2.68 vs `round(2.675,2)` = 2.67）」——第二十八轮漏改此句（只同步了 §1.4 / §7 / §15 / 附录 A）。**架构文档 §6 策略三微基准表**「优化后」列补 `_as_list(...)` 形态并加注「首参证不出数组时套 `_as_list` 做运行期提升（第二十九轮），多约 0.06 µs/行，协议口径见验收报告 §5.1（`hires_map` 1.85×、`hires_filter` 1.37×）」。**全库复查**：`half-away-from-zero`/`ROUND_HALF_UP`/`1.21×`/`86 个 helper`/`96 个名字`/`84 个`/`1425` 零残留（仅 `:661` 与 `test_e2e_flight.py:1161` 属「旧文档曾写反」的历史陈述）；守护 53 passed / 1 skipped、accept 17 PASS / 0 FAIL / 0 SKIP。
- **2026-10-28T11:48+08:00｜第二十九轮**：`$map`/`$filter` 首参不可证数组时改走 `_as_list()` 运行期提升。**根因**：第二十八轮给 `_higher_inline` 加的「首参必须可证为数组」门禁让 `$filter($.6, fn)` 退回 `_fi` + lambda，perf 实测掉到约 2.1×（协议记录里的 1.21× 是门禁之前读数）。**改法**：`runtime._fi` 加单值提升（`$filter(1, $v>0)` → `[1]`，用户 m01245 接受）；`_as_list` 进 `HELPERS` 并加 `list` 快路径（不复制）；codegen 在推导式源头套 `_as_list(...)`，不再退回 lambda。**计数**：helper 86→87、`__all__` 96→97、实际引用 84→85、collected 1425→**1426**（`test_codegen_snapshot` 随 helper 名单参数化 +1），五份文档 + 守护测试全同步。**perf 补测**：仅 `hires_filter`/`hires_map` 产物变化（其余 10 项 src 逐字节相同），重测 1.21×→**1.37×**、1.81×→**1.85×**（多出的约 0.06 µs/行是每行一次提升调用的成本），判定分布不变；`perf_protocol.json` 就地更新并加 `patched` 留痕。**验证**：全量 `1416 passed / 10 skipped (total 1426)`、accept `17 PASS / 0 FAIL / 0 SKIP`。
- **2026-10-28T01:20+08:00｜第二十八轮**：按用户「方案 C」与 JSONata 1.8.6 全量对齐（P0–P3），P4/P5 转为声明。**P0 舍入**：`_ro` 由 `ROUND_HALF_UP` 改 `ROUND_HALF_EVEN`（1.8 实测 `$round(2.5)=2`，文档原写 half away from zero 属反向错误）。**P1 输入侧单值提升**：`_mn/_mx/_av/_sg/_mg/_rv/_ds/_ky/_so` 对非容器按单元素数组处理；`_so` 按 1.8 归并排序逐行移植（比较器返回按真值判定，故 `$a-$b` 得 `[2,1,3]`）；`_ky` 支持对象数组并去重、空对象得 undefined。**P2 字符串/数值边界**：`_S`（null→`'null'`、整值 float→`2`、`:.15g` 归一、指数写法、容器走紧凑 JSON、NaN/Inf→undefined）、`_ss`（负 start/负 length 按 1.8 移植）、`_sbb`/`_sba`（未命中回整串）、`_sp`（limit 为结果个数上限、负值 undefined）、`_N`（正则校验，拒绝 `'Infinity'`/`'0x1F'`/空白填充/`'+1'`，None→undefined）、`_b64d`（不严格校验）、`_fnm`（整数位补零）。**P3 逻辑**：`and`/`or` 内联改由 `_bool_provable` 把门（仅两侧可证布尔才内联），保证恒返回布尔；`_in` 右侧非数组按单元素比（`'a' in 'abc'` 为 false）。**codegen**：新增 `_bool_provable`；`_higher_inline` 增列表性门禁；`$substringAfter` 退出内联表。**文档**：`JSONata 语法.md` §1.4 由 9 条改 8 条并新增 §1.4.1「5 类模型边界」；四份文档 + `gen_jsonata_manual.py` + codegen 注释全库同步银行家舍入/`null` 拼接/`$split` limit 口径。**验证**：差分 170 例 `same 98→126`（剩余 9 条 value_diff 全属已声明模型边界或非确定函数）、全量 `1415 passed / 10 skipped (1425)`、`accept.py` 17 PASS / 0 FAIL；`_jxdiff/` 脚手架已删。
- **2026-10-27T23:52+08:00｜第二十七轮**：`$lookup` 数组入参对齐官方语义——`_lk` 对 `list`/`tuple` 递归收集命中值（改前数组一律 undefined）；同步 `JSONata 语法.md` §12.2（+2 行）、`tests/gen_jsonata_manual.py` `ARRAY_ROWS`（+2 条）、`验收报告.md` §4.2 + §1.2 计数（123→126，既存偏差）、`tests/test_docs_consistency.py` 守护 +6 断言；全量 1415/10/1425、accept 17 PASS、计数无漂移。
- **2026-10-27T18:34+08:00｜第二十六轮**：清理临时产物——删除 `_tmp/`、`build/`、`build_tmp_t10/`、`_tmp_head_verify/`、`__pycache__/` ×18、`.pytest_cache/`（部分）；共释放约 24 MB 磁盘；`git status` 仍 clean、无跟踪文件受影响；`.pytest_cache/` 因沙箱 ACL 拒绝仍残留（详见坑 12）。

- **2026-10-27T11:17+08:00｜第二十四轮**：14 项评审（A1/A2/A3/A4/A5/A6/A7/A8 + B9/B10/B11/B12/B13，A14 与 A1 同处）——`$boolean` helper 列、§4.2 输入列说明、84 项 helper 口径、基座读值、A 类 helper 数、README「才内联」、`$.a[hour].v` 说明、4/5 份文档、10 键顺序、偏离表循环引用、谓词分类名、`_nt` 不在 §13.2、lines/chars 引用；`tests/gen_jsonata_manual.py` 的 hour 行同步。
- **2026-10-27T11:01+08:00｜第二十三轮**：验收报告 4 处口径补做——§4.2 脚注拆出 `$sqrt`/`$count` 的内联前提；§3.4 门禁代码块补无 C 编译器口径；§1.2 第 2 行两种口径并列；§6 表前加「只验收当前一致 + 追溯 git」口径句。
- **2026-10-27T10:55+08:00｜第二十二轮收尾**：JSONata §1.1 错误引用（§13 → §12.1）并合并重复句；§1.4 措辞；验收 §8 去掉 25+/39 项悬空计数；验收 §4.5 补「契约 §3.5」历史引用说明。matrix 72 行无 diff、守护 342 passed / 1 skipped、全量 1415 passed / 10 skipped / 1425、accept 17 PASS。用户清单缺第 2 项待补。
- **2026-10-27T10:46+08:00｜第二十一轮收尾**：`tests/gen_jsonata_manual.py` 与手写正文对齐（`$count`×3 / `$sum` / `hour` 输入行 / `$x + 1` 说明 / `$$` 行尾 / 附录 A 两条易错点）；`verify_round21_align.py` 结果 §3/§7/§8/§12.2 DIFF=0、stale=0；全量 1415 passed / 10 skipped / 1425、accept 17 PASS。
- **2026-10-27T11:10+08:00｜第二十轮收尾**：12 项评审全部处理（README 3 处 / 接口 1 处 / JSONata 4 处 / 验收 6 处）；文档守护 105 passed / 1 skipped、全量 1415 passed / 10 skipped / 1425、accept 17 PASS / 0 FAIL / 0 SKIP、matrix 72 行无 diff；1 处评审事实偏差（第 8 项）。
- **2026-10-27T10:47+08:00｜第十九轮收尾**：删除验收 §7.2 本轮改动清单；`tests/test_sync_contract.py` 两处指称改中性；验收报告 583→573 行；提交 **81f8d00**。
- **2026-10-27T10:35+08:00｜第十八轮收尾**：验收 §6 整体改「文档 / 是否对齐」两列表（原 6.1–6.5 共 16418 字符删除）；两步交叉引用修复；验收报告 747→583 行；提交 **ceb9378**。
- **2026-10-27T09:50+08:00｜第十七轮收尾**：33 项文档一致性完成并提交 **e2a3108**（6 files、+120/−89）；全量 1415 passed / 10 skipped / 1425；accept 17 PASS；matrix 72 行。3 处偏离：一.4 未采用评审方案 A/B（helper 源码从不内联）、二.15 属评审事实偏差、C27 落点改到 JSONata §12.2。
- **2026-10-27T00:40+08:00｜第十六轮增补**：Cython 纳入发布门禁 GATES 15→17（无 C 编译器 SKIP 不阻塞），提交 **5be1855**。
- **2026-10-27T00:17+08:00｜第十六轮**：Cython 13 项实测（cy/py 中位数 1.02×，仅 order_pipeline 0.71×）+ 基准快照重跑回填，提交 **cbba51a**。
- **2026-10-26｜第十五轮**：路径/下标口径与引用纠偏，提交 **2249970** + **d480f83**。

## 三、归档提交（最近在前）

| 提交 | 内容 |
|---|---|
| 本轮 | 第三十九轮：收尾清零 —— R1–R4 定性为「已知弱点非待办」+ 删除标量推导死代码（−114 行） |
| `8c79456` | 第三十八轮：P4 聚合特化 —— 结论「不做」，登记为 R4（零代码改动） |
| `1fd728b` | 第三十七轮：A 项 —— 列表元素类型自动推断（实测 1.54×，零 helper 产物） |
| `8ed68c1` | 第三十六轮：性能 P2 —— `$reduce` 累加形态展成行级循环（实测 1.60×） |
| `4b7cbd8` | 第三十五轮：性能 P1 —— helper 局部绑定（`_g=globals()` + 字典序绑定，实测约 1%） |
| `09d2fde` | 第三十四轮：删除五份文档正文里的「第 X 轮」字样（12 处改中性表述） |
| `c6f97f2` | 第三十三轮：`itype` 元素类型声明 + 常量折叠 + CSE（collected 1426→1445） |
| `8b63f07` | 第三十二轮：再删验收报告 §7.1 的 R5、R7，并把剩余条目重排为连号 R1/R2/R3 |
| `8b63f07` | 第三十一轮：删掉 §7.1 已结案的 R2（aggregate 行容器）与 R4（Cython 未验证）+ 修 §5.6 悬空引用 |
| `2ef451f` | 第三十轮（其二）：`AGENTS.md` 升 v3.1（防格式崩塌强约束 + 新增规则 1A） |
| `a08d60e` | 第三十轮（其一）：文档对齐终检（§1.3 `$round` 不内联理由纠偏 + 架构 §6 微基准表补 `_as_list` 形态） |
| `ee944d8` | 第二十九轮：`$map`/`$filter` 首参不可证数组时用 `_as_list()` 运行期提升（取消退回门禁）+ helper/测试计数同步 + perf 两项补测 |
| `04e7d5f` | 第二十八轮：与 JSONata 1.8.6 全量对齐（P0 银行家舍入 / P1 输入侧单值提升 / P2 字符串数值边界 / P3 恒返回布尔与 `in`），P4/P5 转 §1.4.1 声明 |
| `eb06f72` | 第二十七轮：`$lookup` 数组入参对齐官方语义（`_lk` 递归收集命中值）+ 文档/生成器/守护同步 |
| `d305afb` | 第二十六轮：清理工作区临时目录与临时文件（`_tmp` / `build` / `_tmp_head_verify` / `__pycache__` / `.pytest_cache`，约 24 MB）；仅 `PROGRESS.md` 变更 |
| `c681df2` | 第二十四轮：14 项文档口径收口（`$boolean` 兜底名 / 84 helper / 基座读值 / 10 键顺序 / 偏离表引用方向等） |
| `dd48e65` | 第二十三轮：验收报告口径补做（§4.2 `$sqrt`/`$count` 内联前提、§3.4+§1.2 无编译器门禁口径、§6 对齐口径句） |
| `0f5b7c5` | 第二十二轮：文档引用纠偏（JSONata §1.1 引用修正与去重 / §1.4 措辞 / 验收 §8 计数 / §4.5 历史引用） |
| `dbab6e8` | 第二十一轮：生成器与手写正文对齐（`$count` 口径 / `$sum` / `hour` 输入行 / `$x`·`$$` 行 / 附录 A 易错点） |
| `5daee71` | 第二十轮：12 项口径统一（跳过数 / 门禁 SKIP 口径 / `build()` 签名 / `$map`·`$filter` 产物形态 / 措辞） |
| `81f8d00` | 第十九轮：删除验收报告 §7.2 本轮改动清单（§7 仅留遗留问题表） |
| `ceb9378` | 第十八轮：验收 §6 改「文档 / 是否对齐」两列表 |
| `e2a3108` | 第十七轮：33 项文档一致性（`.get` 快链 / 动态下标两层 / 算子级 vs 函数级 / `$count`≠`$length` / helper 兜底名） |
| `5be1855` | 第十六轮增补：Cython 纳入验收发布门禁（GATES 15 → 17） |
| `cbba51a` | 第十六轮：Cython 产物实测结案 R4 + 13 项快照重跑回填 |
| `d480f83` | 第十五轮：接口 3.4 交叉引用验收 2.2 动态下标四形态 |
| `2249970` | 第十五轮：路径下标口径与引用纠偏（7 项） |
| `e814346` | 第十四轮：`order_pipeline` 纳入协议 + 连跑 3 次取中位数 |

## 四、下一步

- 当前无未决事项；生成器与手写正文在已比对小节（§3/§7/§8/§12.2/§12.5/附录 A）无漂移。
- 说明：生成器 `render()` 输出仍比手写正文少若干**纯排版/额外章节**（正文是手写权威，生成器只作回填工具），
  逐字节比对不成立；如需回填，先写探针按内容比对目标小节确认 DIFF=0 再落盘（第二十六轮已删除 `_tmp/` 下的历史比对脚本，需重建）。
- 唯一遗留的磁盘占用：`C:\Users\black\Desktop\DCG\.pytest_cache`（沙箱内删不掉，详见坑 12）。

## 五、踩过的坑（长期有效）

1. **`generate(validate(raw))` 会报错**：`validate()` 返回规范形（钩子二元组、路径 tuple），`generate()` 入口已 fail-fast 拒绝（第九轮裁决 B）。原始 schema 直接传 `generate()`/`build()`。
2. **PowerShell 内联 `python -c` 会吞反引号/`$`**：一律写 `_tmp/fix_plan/*.py` 脚本再跑。
3. **行数统计**：`Get-Content | Measure-Object -Line` 不准；用 Python `splitlines()` 或 `gen_doc_matrix.py` 口径。
4. **换行口径**：`.gitattributes` 是 `* text=auto eol=lf`（**入库与检出统一 LF**），但工作区里 `README.md`、`dcg/*.py`、`evidence/perf_protocol.json` 仍是历史 CRLF 副本（`git ls-files --eol` 可查 `w/crlf`）。编辑脚本一律 `open(..., newline="")` 读写，保持该文件当前的工作区口径，禁止整文件翻转（旧表述「六份文档都是 CRLF」已过时）。
5. **重定向陷阱**：PowerShell `>` 写 UTF-16 BOM；取 HEAD 内容用 `subprocess` 读 bytes 再解码。
6. **沙箱**：不可写系统临时目录（Cython 编译目录必须放工作区）；文件沙箱剥离目录列举权限会导致 5 条 tmpdir 探测跳过。
7. **测试计数**：不要给 `test_sync_contract` 的 parametrize 加用例（会改变 collected 数 1464）；改 `.gitignore` 会改变沙箱 skipped 数，须同步验收 §1.2/§3.1/§7.1 R7。
8. **性能数字**：单轮比值抖动 ±5–10%，只认「连跑 3 次比值中位数」；`n=92307`（`order_pipeline` 过滤后行数）不是 100000。
9. **teammate 后台轮次可能中断**：恢复方式是重新 send_message 让其按内容（而非行号）复核已落盘项、只补未完成项；写域互斥时不会互相覆盖。
10. **跨文档 `§x.y` 引用是脆弱点**：删/改章节号前先 grep `§<n>.` 全仓（含 `tests/*.py`、`evidence/*.py`），否则会留下悬空引用或让守护测试误读。
11. **评审建议可能含事实错误**：文档类评审先跑探针核对产物再落盘（本轮第 8 项「基座走 `.get` 快链」实测不成立）。
12. **沙箱里 `_tmp` 一类目录删不掉是 ACL 问题，不是权限策略拒绝**：`_tmp` 曾被写入 `Everyone:(I)(CI)(DENY)(S,DC)`（`S`=删除、`DC`=删子项）的显式继承 ACE，进程处于 Low Mandatory Level 且令牌无任何 Se* 特权，导致 `Remove-Item -Recurse`、`cmd /c rd /s /q`、`icacls /remove:d`、`Directory::Move`（子项）、`Directory::Delete` 全部 `Access is denied`；**唯一可行的是 `Directory::Move` 改父目录项本身**（只需父目录 DELETE_CHILD）。`.pytest_cache` 更彻底，连 `icacls` 读 DACL 都拒绝，只能由非沙箱终端删除。
13. **`evidence/perf_runner.py` 已过时，刷新性能数字用 `evidence/bench_run.py`**：`perf_runner` 仍引用 `bench_run.BATCH`（现名 `REPEATS`）与 2 参的 `measure_case(name, data)`（现签名 3 参），直接跑会 `AttributeError: module 'bench_run' has no attribute 'BATCH'`；权威生成器是 `bench_run.py`（`--only` / `--rows` / `-o`），产出 `perf_protocol.json`。
