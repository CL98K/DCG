# PROGRESS — DCG v2.1.0 项目进度

> 本文件长期入库维护（用户 m02343 决定）：每轮开工先看这里，收尾时更新。
> 规则来源：`AGENTS.md` v3.0（状态锚点、检查点汇报、滚动 ETA、防静默熔断）。
> 裁决记录：`_tmp/fix_plan/DECISIONS_v*.md`（不入库）。

## 一、当前状态区（每个检查点覆写）

- **task**: 第二十一轮 — 生成器 `tests/gen_jsonata_manual.py` 与手写正文 `JSONata 语法.md` 对齐（用户 m05214 授权「清掉生成器残留」）
- **step**: 编辑完成 → 复验完成（`render()` 逐节 DIFF=0 / stale=0）→ 测试完成（全绿）→ 待提交
- **done**:
  - §3 `$.a[hour].v` / `$.a[hour]` 两条示例：输入行补 `hour` 键；`$.a[hour]` 的 `fields_extra` 改 `value: "$.hour"` + required 说明（产物行首绑定 `hour=R['hour']`）
  - §8 `$count('abc')` 说明：「与 `$length` 口径一致」→「`$length` 不同：对标量返回 undefined」
  - §12.2 `$sum([true,1])` 说明：改为 `sum()` 可累加 + 不可累加抛 `TypeError: $sum: 元素必须是数值` + bool 为 int 子类的自然结果 + 算术算子同口径
  - 附录 A 易错点：`$count` 行已正确；`$foo 未声明` / `$exists 不能探测缺失键` 两条同步为文档口径（含 `default`）
  - `examples/jx/14_fixed_regressions.jx:230` 的 `$count` note（附录 A「修复说明」行数据源）同步为文档 `JSONata 语法.md:643` 口径
  - §3 `$x + 1` 行说明换成文档 `:130` 全文；§12.5 `$x + 1` 行尾改「见 §1 的「`$name` 只有一种命运」段」并去掉生成器追加的分类后缀
  - §12.5 `$$` 行尾同步为文档 `:391`（§1.1 + 附录 A 引用 + jx 编译层 `Unsupported`），同样去掉分类后缀
  - 机制：`CONSTRUCTS` / `CODEGEN_REJECTS` 支持第 4/5 项为 `None` 表示「不加分类后缀」，循环改 `_entry` 解包
- **doing**: commit + 📊 简报
- **blocked**: 无
- **risk**: 低。改动仅在生成器与 `examples/jx` 语料说明文本，不改引擎/文档正文；`tests/test_jx_examples.py` 与 6 个文档守护、全量回归全绿
- **next**: `git commit` → 回复用户 m05272「下一步完成了么？」
- **eta**: 约 2 分钟
- **updated_at**: 2026-10-27T10:46+08:00

## 二、变更日志（追加，不覆盖）

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
| 本轮 | 第二十一轮：生成器与手写正文对齐（`$count` 口径 / `$sum` / `hour` 输入行 / `$x`·`$$` 行 / 附录 A 易错点） |
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

- 第二十一轮提交后无未决事项；生成器与手写正文在已比对小节（§3/§7/§8/§12.2/§12.5/附录 A）无漂移。
- 说明：生成器 `render()` 输出仍比手写正文少若干**纯排版/额外章节**（正文是手写权威，生成器只作回填工具），
  逐字节比对不成立；如需回填，先跑 `_tmp/fix_plan/verify_round21_align.py` 与 `verify_round21_tail.py` 确认 DIFF=0。

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
