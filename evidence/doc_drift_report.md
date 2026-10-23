> （历史记录，按用户 2026-10-23 决定保留）

# DCG 文档一致性审计报告（P2）

审计范围：README.md、DCG 接口实现细节文档.md、DCG 整体设计方案与架构文档.md、JSONata 语法.md（4 份 md，共约 6.4k 行）。
审计方法：机械核对 —— 每一条文档断言都在本工作区**实跑**（`python -X utf8`，工作树含未提交 v2.1 改动），或与唯一事实源逐项对表：
`dcg/__init__.py`（公开 API）、`dcg/schema.py` 的 `FIELD_KEYS` / `SHAPES` / `VALUE_KEYS`（schema 规格）、`dcg/jx/policy.py`（函数能力表）、`dcg/runtime.py`（HELPERS / UNDEFINED 语义）、`evidence/*`（证据文件）。
审计约束：**只写报告不改文档**（正文改动由 Lead 统一执行，避免写冲突）。所有行号以审计时刻的磁盘内容为准（README.md 共 1362 行）。

统计口径见文末【总表】：必改 **4** 条 / 建议改 **6** 条 / 明确一致 **41** 条核对项 / 未核实 **3** 条。

---

## 0. 总体结论

1. **本轮 breaking change（`row:"list"` 字段级废弃 → 位置行用 `type:"list"` + `fields`；schema 的 `items` 键移除）在 4 份 md 中零残留。** 四个文件对 `ROW_MODES` / `row_modes` / 字段级 `"row"` 的写法做了全文检索均为 0 命中；`items` 的唯一提及是 README.md:335（报错文案，正确）与 README.md:1055（澄清「报告字段名 ≠ schema 已移除的 items 键」，正确）。整体设计文档.md:223、接口实现细节文档.md:153/155/157/194/372/393/511/555/558 全部已是新语法。
2. **README 的"实现状态"数字（847 / 843 / 4 / 16 PASS / CAP 149/180 / M0 195/210 / ratio 1.019）全部有证据支撑**，其中 847/843/4 与 `evidence/pytest_perf_t11.txt`、16 PASS 与 `evidence/accept_perf_t11.txt`（及 `accept_v21_handoff.txt`、`accept_final.txt`）、CAP/M0 与同文件字符门一致；**唯一需要加限定的是效率比值 1.019 的证据时效**（见 B1，Lead 已补注）。
3. **公开 API 表与 `dcg/__init__.py` 逐名、逐序、逐签名一致**（8 个符号，实测 `dcg.__all__` 完全相同）。
4. 发现的真实漂移集中在**转录型示例**（"实跑输出"块与当前产物的字节不再同源）和**规格旁口径**（迁移器未知键白名单、绝对 µs 注记），不涉及架构或语义错误。

---

## 1. 必改（4 条）

### B1. README.md:976 效率门的锚定 revision 属于旧窗口（**Lead 已修，复核后保留在必改清单以免回归**）

- 位置：`README.md:976`（原文 `锚定 revision：dcg/codegen.py 05F907B43EF5`）。
- 事实：`evidence/row_v2.json:352-363` 的 `sha1` / `sha1_before` 两块**现在记录的是 v2.1 源码**：`dcg/codegen.py = E6B008AFCE4D7B944593C8EE2B8502756FAAB592`、`runtime.py = 87A87C66…`、`schema.py = FC7F429A…`，且 `revision_stable_during_run=true`（`:364`）。全 `evidence/` 目录里 `05F907` 只出现在 `evidence/handoff_gate_report.md:111`（作为"已漂移"的反例列出）。当前工作区 `dcg/codegen.py` 的 sha1 实测同为 `E6B008AFCE4D…`（81034 字节），与 `evidence/pytest_perf_t11.txt` 末尾 `[source sha1[:12]]` 段的 `e6b008afce4d` 一致。
- 矛盾：README 把 `05F907B43EF5` 当作"本节数字的锚定 revision"，但它对应当前的 `evidence/row_v2.json` 已经**不成立**（该文件已被 v2.1 复核重冻结）。
- 现状态：Lead 已在 `README.md:977-978` 补注「该 sha1 属于 2026-10-19 窗口；当前源码 sha1 为 `e6b008afce4d`」，并新增 `README.md:982-986` 的 v2.1 复核段（1.0215 PASS，三轮 1.000/1.022/0.989）。**剩余动作**：若 §6.1 表头（README.md:948-958）仍以 1.0192 为判决值，建议在表标题或首行加一句「本节为 10-19 窗口数据；v2.1 复核见下方 982-986」，否则读者会把两套窗口数字混读。

### B2. README.md:968-969 「hand 三轮均为 0.8691」与当前 `evidence/row_v2.json` 不再匹配

- 位置：`README.md:968-969`（`本轮三项绝对 µs 仅作标定（hand 三轮均为 0.8691）`）、并隐含 `README.md:955-957` 表格的 hand 列。
- 事实：`evidence/row_v2.json` 已按 v2.1 重冻结，`cpu_rounds_detail[0..2].hand` 实测为 `0.791015625 / 0.908203125 / 0.927734375`（见该文件 `cpu_rounds_detail` 段），`ratios.v2_over_hand = 1.021505376344086`、`ratios_by_cpu_round = 1.0 / 1.021505376344086 / 0.9894736842105263`、`margin_over_limit = 0.028494623655914042`、`noise_floor = 0.09677419354838712`、`median_ratio = 1.0`。
- 矛盾：README 的「hand 三轮均为 0.8691」是 10-19 窗口的绝对 µs，与当前 row_v2.json 的三轮值（0.7910/0.9082/0.9277）不同；同一节的 `noise_floor=0.0278`、`margin_over_limit=0.0308` 也与当前文件的 `0.0968` / `0.0285` 不同。
- 判断：这**不是**数字造假（1.019 那套值确实存在于 `evidence/accept*.txt:37` 的 10-19 窗口里），而是**同一 README 小节混用了两个窗口的证据**。必改动作：`README.md:948-971` 整块要么标注「以下为 2026-10-19 窗口（`accept_final.txt:37` / `accept_perf_t11.txt:37`）」，要么改写为 v2.1 复核值；`README.md:968-970` 的「hand 三轮均为 0.8691」「margin 2.6×噪声底」在 v2.1 数据下不再成立（当前 median_ratio=1.0、`confidence_median_aggregate=WITHIN_NOISE`）。

### B3. README.md:616-617（§3.6 批量/高阶函数"实跑输出"）与当前产物不一致

- 位置：`README.md:616-617`。
- 文档：先绑局部变量再复用 ——
  `_xs=_ix_get(R,'xs')`，随后 `_sm(_xs) / _mp(_xs,…) / _fi(_xs,…) / _mx(_xs) / _jn(_mp(_xs,…),'+')`。
- 实测（同一 schema，`dcg.generate` 直接输出，未做任何改写）：
  `_o.append([_sm(_ix_get(R,'xs')),_mp(_ix_get(R,'xs'),lambda x,_i1:_ml(x,2)),_fi(_ix_get(R,'xs'),lambda x,_i1:_gt(x,2)),_mx(_ix_get(R,'xs')),_jn(_mp(_ix_get(R,'xs'),lambda x,_i1:_S(x)),'+')])`
  —— **没有 `_xs` 这个局部变量**，`_ix_get(R,'xs')` 内联出现 5 次。`run` 结果（`README.md:620`）实测一致 ✓。
- 影响：这一节的主题恰好是"编译期编成 lambda / 产物形态"，产物与文档不符会直接误导读者（也会让"零冗余"类门禁的阅读者困惑）。必改：把 616-617 换成实跑产物，或明确标注"以下为节选/旧快照"。

### B4. README.md:626（§3.6 带比较器 `$sort` 与 `$position()` 示例）结果无法复现

- 位置：`README.md:623-627`。文档只给一行 `run: [[[1, 2, 3], [0, 1]]]`，**没有给 schema/输入**。
- 实测（同一节上下文里唯一能自洽的 schema：`a = $sort($.xs, function($a,$b){ $a - $b })`、`b = $map($.xs, function($x,$i){ $i })`，输入 `{"xs":[3,1,2]}`）：
  产物为 `b=_mp(_ix_get(R,'xs'),lambda x,i:i)` / `a=_so(_ix_get(R,'xs'),lambda a,b:_sb(a,b))`，`run` = `[[[3, 1, 2], [0, 1, 2]]]`。
  即：`$sort` 的 comparator 形式实测**不影响**输出顺序（原样返回），`$map` 的下标参数为 `[0,1,2]` 三名。
- 矛盾：文档示例的 `[[1, 2, 3], [0, 1]]` 既不是这个输入的结果（排序未生效、下标长度也不对），也没有给出能让它成立的前置条件。必改：补 schema + 输入，或按实测改写这一行。

---

## 2. 建议改（6 条）

### S1. README.md:1210 迁移器"未知字段键"白名单漏了 `row`

- 位置：`README.md:1210`（`不在 FieldSpec 白名单里（type/optional/default/value/const/expr/fields）`）。
- 事实源：`dcg/schema.py` 的 `FIELD_KEYS` 包含 `row`（v2.0 兼容别名）；`src` 侧对 `fields` 存在时 `"row": "list"` 仍被接受。故 `row` **目前不会**报"未知键"，白名单列举不全会让人误判。
- 建议：写成 `type/optional/default/value/const/expr/fields`（+ `row`，v2.0 兼容别名，新写法请用 `type:"list"`）。

### S2. README.md:1246 「共 226 个文件」无法在本沙箱核实

- 位置：`README.md:1246`。实测口径受未跟踪目录（`V1/`、`_tmp/`）与部分目录不可列影响，计数不可比。建议标注"（不含未跟踪目录）"或给出统计命令。

### S3. README §2.5 / §2.6 对"算出但不输出"的表述可补一句

- 位置：`README.md:344-345`（`v2 没有「算出来但不输出」的机制`）与 `README.md:1203`。
- 实测：`{"shape":"index"}` 下把 `$map($.xs, function($x){ $x*2 })` 作为一个普通字段声明，产物为 `_o.append([_ad(_ix_get(R,'a'),1),_mp(...)])`、`run` 得 `{0: [2, [2, 4]]}` —— 表达式被**内联**进输出行，而不是"算了不输出"。表述本身正确，但建议补一句"内联进引用它的字段（§7.5 首选手法）"，避免读者以为"内联"会丢掉计算。

### S4. README.md:661 `$exists` 的写法需注明 `$` 前缀

- 位置：`README.md:661`（`#.a → [[{'a': 1}, False, undefined]]（$exists → False）`）。
- 实测：`compile_expr("$exists($.zz)")` → `('call','$exists',[…],(1,8))`，产物 `_ex(_ix_get(R,'a'))`；而 `exists($.zz)` 被拒：`第 7 列: exists() 不可逐行编译（只有 $ 函数可以被调用）`。表格里写的是裸 `$exists` 但上下文是 IR 片段，建议统一写全 `$exists($.a)`。

### S5. 整体设计文档.md:409 `$.items` 与 schema 已移除的 `items` 键易混

- 位置：`DCG 整体设计方案与架构文档.md:409`（`$sum([$.5.a.1, $.a.c]) 与 $sum($.items) 同属这一类`）。
- 事实：这里的 `$.items` 是**数据路径**（读行里名为 items 的键），与 v2.1 移除的 schema `items` 键无关；技术上不矛盾。建议加括注区分，避免读者把它当成被移除语法的残留。

### S6. README.md:768-786 trace 示例的字符数与当前产物需要复核一次

- 位置：`README.md:772`（manifest `lines: 6, chars: 71`）、`README.md:786`（`正式产物只有 71 字符`）。
- 实测：同一 2 字段 trace schema 主产物为 71 字符（与 `README.md:768-786` 一致 ✓），trace 产物为 `TRACE=[]` + 循环内 `TRACE.append((R,_o[-1]))` 两行，与文档一致 ✓。**这条建议的只是**：manifest 里的 `artifact`/`trace_artifact` 路径按读者机器会不同，建议注明"路径按本机生成目录改写"。属措辞级。

---

## 3. 明确一致（41 项核对项，均已实跑或逐项对表）

| # | 文档位置 | 核对方式 | 结论 |
| --- | --- | --- | --- |
| 1 | README.md:12-14 实现状态（847/843/4、16 PASS、CAP/M0） | 对 `evidence/pytest_perf_t11.txt:3,7`、`evidence/accept_perf_t11.txt:37` | 一致 |
| 2 | README.md:40-65 快速开始 schema/产物/manifest | 实跑 generate + build | 逐字一致 |
| 3 | README.md:72-80 CLI 与 schema_sha1 | 实跑 `-m dcg.build` | 一致（sha1 语义 = 规范化后 schema） |
| 4 | README.md:191-229 §2.4 嵌套只能有 fields | 实跑 + `tests/test_codegen_snapshot.py` 四个新用例 | 一致 |
| 5 | README.md:236-254 内层引用外层、:258-271 遮蔽、:273-277 裸名报错 | 实跑 | 逐字一致 |
| 6 | README.md:279-292 nested 成环报错 | 实跑 nested = `out.fields.doc.fields: 字段依赖成环: a -> b -> a` | 逐字一致 |
| 7 | README.md:295-321 `type:"list"` + `fields` 位置行 | 实跑（含 dict 形状 `{'doc':_o}`） | 一致 |
| 8 | README.md:323-340 §2.5 数组、`items` 键报错、`"row"` 建议 | 实跑 | 逐字一致 |
| 9 | README.md:342-351 schema 即输出契约 + 旧版字段键报错 | 实跑 | 逐字一致 |
| 10 | README.md:353-368 §2.7 前向引用 | 实跑 | 一致 |
| 11 | README.md:376-447 §2.8 八种 type 表 + 裸代码/helper 对照 | 实跑 generate | 一致 |
| 12 | README.md:474-487 §3.1 符号表 | `compile_expr` 实跑 | 逐字一致 |
| 13 | README.md:489-505 §3.2 IR 三例 + rfunc | 实跑（三条 IR 元组逐字符比对） | 逐字一致 |
| 14 | README.md:543-573 §3.5 A/B/C 45+6+4=54、永久拒绝 10 项 | 与 `dcg/jx/policy.py` 的 `ROW_FUNCS/HIGHER_ORDER/NONDETERMINISTIC/STRUCTURAL` 双向差集 | 零差集 |
| 15 | README.md:565-573 八条拒绝消息（含列号） | `compile_expr` 实跑 | 逐字一致 |
| 16 | README.md:579 Registry.fn 名字校验 | 实跑 | 逐字一致 |
| 17 | README.md:589-620 §3.6 agg 示例的 run 结果 | 实跑 | 一致（产物行见 B3） |
| 18 | README.md:629-652 §3.7 非确定注入 + 产物字节可复现 | 实跑 `FixedClock/FixedRandom`，`generate==generate` | 一致 |
| 19 | README.md:654-678 §3.8 动态下标/undefined 产物 | 实跑 | 逐字一致 |
| 20 | README.md:684-692 §4 四种 shape 返回 | 实跑 | 一致 |
| 21 | README.md:700-706 dict 产物 | 实跑 | 一致 |
| 22 | README.md:708-720 §4.1 single 三条校验报错 | 实跑 | 逐字一致 |
| 23 | README.md:722-733 §4.2 filter 真值表 | 实跑 | 一致 |
| 24 | README.md:735-766 §4.3 post 产物 + 报错 | 实跑 | 逐字一致 |
| 25 | README.md:768-786 §4.3 trace 产物/字节相同 | 实跑 | 一致（措辞见 S6） |
| 26 | README.md:788-795 §4.4 input.source | 实跑 | 一致 |
| 27 | README.md:797-871 §4.5 十条组合 + 307 字符 / 9 行 | 实跑全部 9 种配置 | 一致（rows 产物逐行一致） |
| 28 | README.md:873-875 trace manifest for orders | 实跑 build | 一致 |
| 29 | README.md:877-893 filter×single 五组合、零行容器差异 | 实跑 | 一致 |
| 30 | README.md:895-908 六条校验期拒绝 + 三条正交规则 | 实跑 | 逐字一致 |
| 31 | README.md:912-936 §5 能力边界表 12 行 | 实跑 + 与 §9 交叉 | 一致 |
| 32 | README.md:942-986 §6 性能（除 B1/B2 的窗口标注） | 对 `evidence/*.txt`、`row_v2.json` | 数值均可溯源 |
| 33 | README.md:1006-1059 §7.1 CLI 参数/退出码/报告结构 | 实跑 `-m dcg.migrate`（含 `--check`） | 一致 |
| 34 | README.md:1061-1096 §7.2 最小例的 item 形状与三档汇总行 | 实跑自造 describe | 结构逐字一致（counts 随输入，文档已声明"节选"） |
| 35 | README.md:1110-1113 examples 产物样例用 `--no-wrap-doc` 生成 | 读 `examples/flight_migration_report.json`（21 items、`manual: output.post`） | 一致 |
| 36 | README.md:1115-1172 §7.3 文档外层键映射 | `examples/flight.json` 实跑 `run[doc][0]` | 一致（该 schema 的 4 字段变体实跑 144 字符、`[{'doc':...}]` 形态相同） |
| 37 | README.md:1177-1222 §7.4-7.6 迁移能力/人工项/不做项 | 与 `dcg/migrate.py` 的 report 文案逐条比 | 一致（白名单见 S1） |
| 38 | README.md:1226-1242 §8 公开 API 表 | 对 `dcg.__all__` + `inspect.signature` | 逐名逐签名一致 |
| 39 | README.md:1251-1326 §8.1 目录结构（tests/evidence/bench 清单） | 实列目录 | 一致（文件总数见 S2） |
| 40 | README.md:1328-1353 §9 验证结果与剩余边界 7 条 | 实跑 + 证据 | 一致 |
| 41 | JSONata 语法.md §12.1/§12.2/§12.3/§12.4/§13/§14.1/§14.2 | 与 `policy.py`/`runtime.py`/`examples/jx` 逐项对 | 一致（45/6/4=54、15 文件 234 例、3 条 defect、30 条 fixed） |

补充说明（不计入 41 项，均为"一致"的细节）：
- `dcg/migrate` 的 CLI 入口函数名是 `main`（**不是** `_main`）；README 只写 `python -m dcg.migrate`，不受影响，但审计脚本调用时需注意。
- `dcg.codegen.load(path, mod_name=None)` 只用于加载 build 产物，**不在** `dcg.__all__` 里；把它当 JSON 读取会 `AttributeError: 'NoneType' object has no attribute 'loader'`。README 未提及它，属"未收口"而非矛盾。

---

## 4. 未核实（3 条）

| # | 条目 | 原因 |
| --- | --- | --- |
| U1 | README.md:1246 「共 226 个文件」 | 当前工作树有未跟踪 `V1/`、`_tmp/`，且沙箱对部分目录拒绝列举，无法得到可比计数 |
| U2 | 整体设计文档.md 7.5 的实测收益表（_ad/_ml/_eq 等 1.79–2.42× 的 ns 级数字） | 需要专用 benchmark 探针与稳定机器；本次只核对了"形态"（内联成什么），未复跑 ns 数字 |
| U3 | README.md:945 「旧的静默窗口要求已作废」等历史决策口径 | 属历史决策记录，无对应的可执行断言 |

---

## 5. 附：与 Lead 的口径对齐（同步记录）

- pytest 数字：`evidence/pytest_perf_t11.txt:3` 的 cmd 用 `--basetemp=.tmp_perf_t11c`（**不在 `_tmp` 下**），源码 7 个 sha1 与当前工作区逐字节一致 ⇒ 847(843p/4s) 是**当前代码的有效证据**，README 不必改。Lead 在沙箱内另测得的 833p/1f/9e 差异，根因是**临时目录位置差异**（`_tmp` 下新建的子目录 ListDirectory 权限被剥离），不是漏 `-p aclfix` —— 本报告按此口径记录。
- §3.5.x 章节号悬空引用（`dcg/codegen.py:116/1106/1117`、`tests/test_jx_policy.py:44/47`、`tests/test_codegen_snapshot.py:130`、`tests/test_e2e_flight.py:674` 共 7 处）已由 Lead 修复，全仓无残留；文档结构是 §3.5 下的 A/B/C 类、展开在 §3.6/§3.7。
- README.md:292 成环路径经复核**不是问题**（nested 与 top 两种实跑路径均与文档一致），已从清单撤回。
