# PROGRESS — DCG v2.1.0 项目进度

> 本文件长期入库维护（用户 m02343 决定）：每轮开工先看这里，收尾时更新。
> 规则来源：`AGENTS.md` v3.0（状态锚点、检查点汇报、滚动 ETA、防静默熔断）。
> 裁决记录：`_tmp/fix_plan/DECISIONS_v*.md`（不入库；`_tmp/` 已于第二十六轮清理，历史裁决记录不再保留）。

## 一、当前状态区（每个检查点覆写）

- **task**: 第二十八轮 — 与标准 JSONata 1.8.6 全量对齐（方案 C：P0 + P1 + P2 + P3）
- **step**: T1 `_ro` 舍入口径 → T2 字符串/数值边界 → T3 单值提升 → T4 逻辑与 in → T5 文档同步 → T6 差分复跑/全量回归/accept/提交
- **done**:
  - **P0** `_ro` 改 `ROUND_HALF_EVEN`（银行家舍入，与 1.8.6 的 `Math.round` + 「.5 且结果为奇数则减 1」逐例一致）；`tests/test_e2e_flight.py::test_round_never_inlines_because_decimal_shift_differs`（改名 + 新期望，不能内联的真因改为十进制移位）
  - **P2 字符串**：`_S` 对齐 `JSON.stringify`（null→`'null'`、整值浮点→整数、`toPrecision(15)` 归一、紧凑 JSON、JS 指数写法、NaN/±Inf→undefined）；`_ss` 移植 1.8 算法（负 start 夹 0、负 length 空串、小数下标截断）；`_sbb`/`_sba` 未命中返回整串；`_sp` 的 limit 改为「结果个数上限」（纯截断，负数→undefined）；`_b64d` 宽松解码；`_fnm` 整数位补零
  - **P2 数值**：`_N` 只认 JSON 数字字面量（拒绝空白填充/`0x`/`Infinity`/`+1`，null→undefined），非有限数→undefined（旧实现会把 `inf` 写进产物、`json.dumps` 产出非法 JSON 字面量 `Infinity`）
  - **P1 单值提升**：`_mn`/`_mx`/`_av`/`_rv`/`_so`/`_ds`/`_mg`/`_ky`/`_sg` 对标量入参按单元素数组处理；`_ky` 支持数组入参（逐元素取键、保序去重）；`$sort` 比较器改为 1.8 归并排序逐行移植（「返回真就交换」，`$sort([3,1,2],$a-$b)` → `[2,1,3]`）；`_ky({})` → undefined
  - **P3 逻辑**：`_in` 重写（任一侧 undefined→false；右侧非数组包成单元素数组；严格相等），故 `'a' in 'abc'` 为 false、`1 in [1,2]` 为 true；codegen `and`/`or` 内联条件由 `_truthy_provable` 收紧为新增的 `_bool_provable`（声明 bool 的字段也计入），`1 and 2` → true
  - **codegen 保护**：`_higher_inline` 要求首参 `_is_listy`（否则退回 `_mp`/`_fi`，避免 `for v in 1` 崩）；`$substringAfter` 取消内联（`partition()[2]` 未命中给 `''`，标准给整串）
  - **样例/测试同步**：`examples/jx/04_concat.jx`（null → `'null'` 口径，未新增用例）、`examples/jx/14_fixed_regressions.jx`（`$round(2.5)` → 2，真因改十进制移位）、`tests/test_docs_consistency.py::test_runtime_core_semantics_are_correct`（扩到 40+ 断言，覆盖全部新语义）、`tests/test_e2e_flight.py` bi8 期望改 `_sba(`
  - **验证（进行中）**：`test_docs_consistency.py` 16 passed；`test_e2e_flight.py` 126 passed / 4 skipped；`test_jx_examples.py` 244 passed
- **doing**: 无（改动已全部落盘并验证）
- **blocked**: 无
- **risk**: 低（收尾时已全部验证）。改动集中在 `dcg/runtime.py` 与 codegen 的内联判定，波及面广但由 1425 例回归 + 17 道 accept 门禁覆盖；`_jxdiff/` 临时脚手架已删除，工作区只剩 ` M AGENTS.md`（用户自改，不提交）
- **next**: `git commit`（13 个文件，排除 `AGENTS.md`）→ 输出 📊 汇总
- **eta**: 约 1 分钟
- **updated_at**: 2026-10-28T01:20+08:00

**第二十八轮验证结果（最终）**
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
| 本轮 | 第二十八轮：与 JSONata 1.8.6 全量对齐（P0 银行家舍入 / P1 输入侧单值提升 / P2 字符串数值边界 / P3 恒返回布尔与 `in`），P4/P5 转 §1.4.1 声明 |
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
4. **换行口径**：README / 架构 / 接口 / JSONata / 验收 / `perf_protocol.json` 是 CRLF；`PROGRESS.md`、`.gitignore` 是 LF。编辑脚本必须 `open(..., newline="")` 读写。
5. **重定向陷阱**：PowerShell `>` 写 UTF-16 BOM；取 HEAD 内容用 `subprocess` 读 bytes 再解码。
6. **沙箱**：不可写系统临时目录（Cython 编译目录必须放工作区）；文件沙箱剥离目录列举权限会导致 5 条 tmpdir 探测跳过。
7. **测试计数**：不要给 `test_sync_contract` 的 parametrize 加用例（会改变 collected 数 1425）；改 `.gitignore` 会改变沙箱 skipped 数，须同步验收 §1.2/§3.1/§7.1 R7。
8. **性能数字**：单轮比值抖动 ±5–10%，只认「连跑 3 次比值中位数」；`n=92307`（`order_pipeline` 过滤后行数）不是 100000。
9. **teammate 后台轮次可能中断**：恢复方式是重新 send_message 让其按内容（而非行号）复核已落盘项、只补未完成项；写域互斥时不会互相覆盖。
10. **跨文档 `§x.y` 引用是脆弱点**：删/改章节号前先 grep `§<n>.` 全仓（含 `tests/*.py`、`evidence/*.py`），否则会留下悬空引用或让守护测试误读。
11. **评审建议可能含事实错误**：文档类评审先跑探针核对产物再落盘（本轮第 8 项「基座走 `.get` 快链」实测不成立）。
12. **沙箱里 `_tmp` 一类目录删不掉是 ACL 问题，不是权限策略拒绝**：`_tmp` 曾被写入 `Everyone:(I)(CI)(DENY)(S,DC)`（`S`=删除、`DC`=删子项）的显式继承 ACE，进程处于 Low Mandatory Level 且令牌无任何 Se* 特权，导致 `Remove-Item -Recurse`、`cmd /c rd /s /q`、`icacls /remove:d`、`Directory::Move`（子项）、`Directory::Delete` 全部 `Access is denied`；**唯一可行的是 `Directory::Move` 改父目录项本身**（只需父目录 DELETE_CHILD）。`.pytest_cache` 更彻底，连 `icacls` 读 DACL 都拒绝，只能由非沙箱终端删除。
