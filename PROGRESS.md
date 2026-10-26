# PROGRESS — DCG 文档一致性治理（第八轮）

> 会话断点恢复用。每次关键阶段覆写。

## 当前任务
用户 m02150 第八轮评审：6 项设计裁决 + 10 项文档对不齐 + 6 项写作收敛，全部文档级。
裁决源：`_tmp/fix_plan/DECISIONS_v5.md`（§0 ground truth / §1 canonical / §2 逐文件 / §3 收尾）。

## 已完成
- [x] ground truth 实测：optional+value/expr 已同形（引擎无缺陷，接口 §4.3:619 与 JSONata:643 是陈旧文档）；钩子产物必须带 `_hb/_ha`；`$map` 无 position 走推导式；before 两条报错文案逐字取得。
- [x] 裁决落盘 DECISIONS_v5.md；PROGRESS.md 建立。
- [x] 三 teammate 并行编辑完成：t2 README（1907 行，钩子产物补 `_hb/_ha` 12 处、`$函数名(...)`、`$map` 两形态、§2.4 拆正常/成环、§2.3 落值句）；t3 接口 897 行 + 架构 932 行（§4.3 expr optional 落 None、§4.1 钩子报错拆四条、§4.4 读值三句、§7.3 旧口径改 canonical、§7.6.3 补全文）；t4 JSONata 658 行（§1.1 两条落值、附录 A :649、§6/§8/§12.2 前提句、`$count` 前提；gen 脚本同步）。
- [x] 验收报告：§4.6 落值形态段、§6.3 第八轮 7 条、§7.2 真实清单、§6.1 实测规模（README 1 907/82 069、接口 897/85 638、架构 932/88 153、JSONata 658/66 394、验收 651/60 748）。
- [x] 首轮全量 pytest **1415 passed / 10 skipped / 0 failed（1425）**、accept **15 PASS**；matrix 78 行 §0 与 §6.1 一致。

## 进行中
- [ ] 最后一轮回归（§6.1/§7.2 改后复跑）→ commit → 📊 汇总。

## 下一步
- [ ] `git add -A && git commit`（信息：docs 第八轮一致性（S1–S6/A1–A6/B1–B6）：optional 落值口径统一、钩子产物补守卫、示例表前提句）
- [ ] 交付 📊 任务汇总报告（22 项 + 3 处偏离裁决 + 1 观察项）。

## 坑 / 注意事项
- `generate(validate(sch))` 对含钩子 schema 会二次校验报错；用 `generate(schema)`。
- PowerShell 内联 `python -c` 含中文/反引号会转义失败 → 一律写 `_tmp/fix_plan/*.py` 再跑。
- 字节统计用磁盘值（`Get-Item Length` / matrix），勿用 Python `len(encode())`（CRLF 少算行数）。
- 文档守护：test_doc_products 按 `# dcg:` 块逐字节比对；test_docs_consistency 钉 86/96/55/1425；test_sync_contract 钉报告计数与未跟踪清单（提交前先 `git add` 可让未跟踪清单检查 skip）。
