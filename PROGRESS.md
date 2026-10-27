# PROGRESS — DCG v2.1.0 项目进度

> 本文件长期入库维护（用户 m02343 决定）：每轮开工先看这里，收尾时更新。
> 规则来源：`AGENTS.md`。裁决记录：`_tmp/fix_plan/DECISIONS_v*.md`（不入库）。

## 当前状态（第十七轮，收尾待提交）

**主题**：用户 m04777 的 **33 项**文档一致性评审（5 设计层 + 20 对不齐 + 8 写作），全部文档级、引擎与测试未改。
裁决源：`_tmp/fix_plan/DECISIONS_v9.md`（C1–C27）；写域四路互斥（README / 接口+架构 / JSONata / 验收报告）。

### 已完成

1. **`.get` 快链收口（一.1）**：只对「`optional` + 具体 `type` + **纯名字段路径**」成立，含整数下标整条走 `_ix_get`。
   README §2.3（三处）、JSONata §1.3 第 3 档、接口 §4.4 三处统一（接口侧原本已 canonical）。
2. **动态下标两层判定（一.2）**：`_ix_get` 由**字段级 `optional`/`default`** 带来，不是路径读值本身——required 发裸下标
   `R['a'][R['i']]`，optional/default 才把整个下标访问包成 `_ix_get(base, idx)`。接口 §3.4 原「无论 `i` 怎么声明一律
   `_ix_get`」句已替换；README §3.1、JSONata §3 同步。
3. **算子级 vs 函数级语义拆分（一.3、三.3）**：接口 §4.4「函数语义补充」单段→三段（算子级 EAFP 默认规则 / 函数级按
   各函数语义、多数返回 undefined / 内联 vs helper 在「数据违反声明」时可能不同）；JSONata §13.6 第 3 条同口径。
4. **`import math` 判据裁决（一.4）**：评审抛出的两案（A 规范化 `_M`→`math` / B 产物补 `import math as _M`）**都不需要**——
   helper 的 `SOURCE` 从不内联进产物，内联模板（`dcg/codegen.py`）发的就是字面 `math.floor/ceil/sqrt`，产物 `import math`
   判据只扫产物文本。JSONata §13.4 改写为「runtime 内部可用 `_M.`、codegen 内联模板必须发 `math.`、不要给产物补别名」。
5. **验收 §4.2 表头改「helper 兜底名」（一.5）+ 表前内联说明**；`$boolean` 行等 helper 列只表示不可内联时的运行期实现。
6. **README 附录 A 读值表回填（二.1–二.4）**：`any` 未声明 optional 与具体 type 同形裸链 `_c(R['x'])`（缺键 KeyError）；
   A.8.3「都不写」行同改、`optional: true` 拆「具体 type（`.get` 快链）」与「any/未声明（`_ix_get`）」两行、节标题改
   「取值组合与产物」；§3.1 动态下标行改两层判定。
7. **`$count` ≠ `$length`（二.5–二.7）+ `$sum` 表述统一（三.5）**：JSONata §8 / §15.2 / 附录 A 三处说明 `$count` 对标量按 1 计、
   `$length(5)` 是 undefined；§12.2 `$sum([true,1])` 统一为「成功路径 C 级 `sum()`、不可累加抛 `TypeError: $sum: 元素必须是数值`、
   `true+1==2` 是 bool 为 int 子类的自然结果」。
8. **其余对齐（二.8–二.20）**：`$round` 1/2 参一律走 `_ro`（银行家舍入不能内联）；§1.4 引用 §1.2→§1.3；README §5 `$.m[0]`
   改述为「设计内行为」；`$map`/`$filter` lambda 只留无法展开场景（JSONata §13.5 + 架构 §6.5）；验收 §2.2 #5 删矛盾句
   （两路径终值形态不同，分别断言）；D-OPT-01 标注历史修复（现行判据只看 optional/default）；§0 真缺陷摘要补全（10 项 ID 全列
   + 按危害分类 + D-MATH-01 单列，删重复孤儿段）；`py{}` 多语句块称谓；架构 §7.5「46 个 A 类行内函数对应的算子级 helper」、
   §9.1 Registry「8 个成员」；§5.6 表 11 处 `±10%%`→`±10%`。
9. **写作（三.1–三.8）**：README §3.1 `$函数名(...)` 拆三段（路径读值→helper / 多处引用且有行首语句时行首绑定 / 裸名具体 type
   才内建方法，并区分 `shape:"list"` 行循环 vs `shape:"rows"` 推导式）；JSONata §3 两条 `hour` 示例补输入行并说明 required
   字段行首绑定后 `R['a'][hour]`。
10. **验收报告收口**：§6.2 新增「第十七轮增补」9 条；§7.2 本轮 7 文件清单（上一提交 5be1855）。

### 验证记录

- 文档守护：`test_doc_products` + `test_readme_hooks` = 42 passed；接口/架构四守护 = 78 passed；
  JSONata 五守护 = 81 passed；`test_doc_structure` + `docs_consistency` + `doc_error_messages` + `sync_contract` = 63 passed / 1 skipped
- 全量 `python -X utf8 -m pytest -q` = **1415 passed / 10 skipped / 0 failed（1425）** exit 0（日志 `_tmp/pytest_round17.log`）
- `python -X utf8 evidence/accept.py` = **17 PASS / 0 FAIL / 0 SKIP**
- `evidence/gen_doc_matrix.py --write` → `evidence/doc_vs_impl_matrix.md` 72 行

## 归档提交（最近在前）

| 提交 | 内容 |
|---|---|
| （本轮待提交） | 第十七轮：33 项文档一致性（设计口径 / 对齐 / 写作，引擎未改） |
| `5be1855` | 第十六轮增补：Cython 纳入验收发布门禁（GATES 15 → 17，无编译器 SKIP 不阻塞） |
| `cbba51a` | 第十六轮：Cython 产物实测并结案 R4 + 13 项快照重跑回填 |
| `d480f83` | 第十五轮：接口 3.4 交叉引用验收 2.2 动态下标四形态 |
| `2249970` | 第十五轮：路径下标口径与引用纠偏（7 项） |
| `e814346` | 第十四轮：`order_pipeline` 纳入协议 + 连跑 3 次取中位数 |
| `84c8964` | 第十三轮：重跑 12 项性能快照 |
| `ef517e9` | 第十二轮：24 项一致性 |

## 下一步

- 第十七轮待提交：`git add -A && git commit`（信息见验收 §7.2）
- 可选后续：`tests/gen_jsonata_manual.py` 的 `$count`/`hour`/`$sum` 表行尚未同步（本轮写域限制），若日后重跑生成器
  回填会覆盖这三处文案——需要时单独授权同步生成器
- 之后无未决事项；性能快照为 2026-10-27T00:17:27+0800 口径

## 踩过的坑（长期有效）

1. **`generate(validate(raw))` 会报错**：`validate()` 返回规范形（钩子二元组、路径 tuple），`generate()` 入口已 fail-fast
   拒绝（第九轮裁决 B）。原始 schema 直接传 `generate()`/`build()`。
2. **PowerShell 内联 `python -c` 会吞反引号/`$`**：一律写 `_tmp/fix_plan/*.py` 脚本再跑。
3. **行数统计**：`Get-Content | Measure-Object -Line` 不准；用 Python `splitlines()` 或 `gen_doc_matrix.py` 口径。
4. **换行口径**：README / 架构 / 接口 / JSONata / 验收 / `perf_protocol.json` 是 CRLF；`PROGRESS.md`、`.gitignore` 是 LF。
   编辑脚本必须 `open(..., newline="")` 读写，多行插入用对应换行符拼接，否则整文件换行被改写。
5. **重定向陷阱**：PowerShell `>` 写 UTF-16 BOM；取 HEAD 内容用 `subprocess` 读 bytes 再解码。
6. **沙箱**：不可写系统临时目录（Cython 编译目录必须放工作区）；文件沙箱剥离目录列举权限会导致 5 条 tmpdir 探测跳过。
7. **测试计数**：不要给 `test_sync_contract` 的 parametrize 加用例（会改变 collected 数 1425）；改 `.gitignore` 会改变
   沙箱 skipped 数（干净树检查），须同步本文与验收 §1.2/§3.1/§7.1 R7。
8. **性能数字**：单轮比值抖动 ±5–10%，只认「连跑 3 次比值中位数」；`n=92307`（`order_pipeline` 过滤后行数）不是 100000。
9. **teammate 后台轮次可能中断**：本轮 t2/t3/t4 各有 1 次「后台运行失败、无收尾消息」，恢复方式是重新 send_message 让它们
   按内容（而非行号）复核已落盘项、只补未完成项；写域互斥时不会互相覆盖。任务状态要由 Lead 核对 diff 后收口。
