# PROGRESS

## 当前状态
```yaml
task: R8 两件工程债（CI 绿 job 假 annotation / 文档版本守护覆盖面）已修并发布到 main
step: R8-S2 已 commit + push（abdb79f），CI 42396038435 全绿 14/14，failure 级 annotation 归零
done:
  - C1 双活违约已修（改 Rust：o_eu/o_du 提取失败分支 UNDEF→None）；Lead 独立复核 live/pure 逐值一致，6514 次差分对拍 mismatch 0，真 emoji 仍走 Rust
  - C2 $formatBase(±inf/nan) 已返回 undefined（dcg/runtime.py:942 `_fb` 非有限数分支）；正常值逐值不变，radix 参数按裁决不动
  - D3 $fromMillis 9999 年越界已修（dcg/runtime.py:2612 `_start_first_week` 越界返回 None + 四调用点显式判空）；基线对比 26296 格：401 处异常全消失、0 回归、年份 1..9998 逐值 0 差异
  - $match 的 o_mt 已从 newfn.rs 删除（1857 → 1750 行）；--only 传未知算子名时加 stderr 警告；verdict_counts 变为 {keep: 16, gate_intercept: 3}，deliberately_not_wired 已无实例
  - 2.2.5 已打包并发布：三件产物 whl 1,098,565 B / tar.gz 1,362,108 B / src.zip 1,403,534 B，twine check 3/3 PASSED；4 平台二进制回填（CI run 42390397831 success 14/14、6 artifact 齐）；README §7.3 尺寸表按 wheel 内真实 compress_size 更新（249,397 / 257,148 / 271,147 / 163,831，合计 2,129,304 B / ≈941,523 B）；GitHub Release v2.2.5 已发布（id 437694791，3 asset 全 uploaded，tag 指 abb8f5f）
  - T-A（.github/workflows/ci.yml）：pytest 与 acceptance gates 两步的 `::error::` 循环改为 `if [ "$rc" -ne 0 ]` 才执行，并加 `PIP_DISABLE_PIP_VERSION_CHECK`——此前无论成败都刷 error annotation，绿 job 也带红标。验收读数：14 个 check-run 合计 27 条 annotation，**failure 级 = 0**（15 warning + 12 notice 全是 GitHub 自身 Node.js 20 / ubuntu-latest 提示），对比上一轮 42392119927 的 188 条 / 161 failure；py3.13 job 日志 `##[error]` 行数 44 → 0
  - T-B（tests/test_docs_consistency.py）：`_VERSION_MARKER` 增补 `DCG 接口实现细节文档.md`，新增 `_ALL_DOCS` 六元组，「不许出现旧包版本」那条从扫 2 份扩到 6 份，并新增第三条正则；该文件用例数 20 → 25
  - 计数同步 2249 → 2254（验收报告 11 处：§0 表①、版本口径段、验收命令表、skipped 口径段、两口径表两行、§3.1 代码块、test_docs_consistency 行 20→25、合计行、§3.5 ② 标题、§8 ① 索引）；全量 2245 passed / 9 skipped / 0 failed（total 2254）
  - commit abdb79f `fix(r8): CI 绿 job 不再刷假 error annotation + 文档版本守护补齐四份`（5 files +104/−45）已 push，origin/main = abdb79f；CI run 42396038435 success 14/14，6 artifact 齐
  - 验收报告 §1.2 补 CI 差异旁述（CI 不装 [dev] extra → test_jx_conformance 的 importorskip("jsonata") 多跳 1 条，故 CI 10 跳过 / 本机 9；收集总数 2254 一致）
doing: []
blocked:
  - PyPI 凭据全缺（~/.pypirc 无 / 5 个环境变量 MISSING / pip config 空 / keyring 无条目 / 无 twine），用户明确「先不管」
risk:
  - 构建字节不可复现（PE 时间戳/PDB GUID，SOURCE_DATE_EPOCH 无效）→ 漂移检测只能 warning
  - zp helper 1.270–1.543× 跨 1.3 两侧（CI 失败线 1.0，余量 27%）
  - $formatBase(255, inf) 仍抛 OverflowError（radix 侧刻意不修，有测试钉住）
  - 无 CHANGELOG（发布说明只写在 GitHub Release body）
  - 无稳定版声明与版本策略（README 未写支持矩阵 / 弃用策略）
  - Join 文档与 JSONata 语法里的「能力交付版本」属历史语义，仍靠人工判断（版本守护已覆盖四份主要文档）
  - `$match` 的纯 Python 侧 `_mt`（`dcg/runtime.py:2327`）仍在——正确，未删（只删了 Rust 死代码）
next:
  - 等用户指示（PyPI 凭据 / 是否进入稳定版流程）
eta: R8 已收口（commit + push + CI 全绿）
team_mode: 启用（9 teammate：全部 inactive）
updated_at: 2026-11-08T01:45:00+08:00
lead_poll_at: 2026-11-08T01:44:00+08:00
poll_count: 76
timed_brief_count: 13
timed_full_count: 11
silent_stop_count: 0
escalation_count: 0
teammates:
  - id: recon-docs
    subtask: R7 文档侧：JSONata 语法.md:627 RS 列 + 架构文档:625 措辞 + §19.2 判定（已交付）
    status: inactive
    last_output_at: 2026-11-07T23:33:00+08:00
    last_heartbeat_at: N/A
    last_poll_at: 2026-11-08T00:38:00+08:00
    poll_count: 20
    retry_count: 0
    last_error_type: none
  - id: recon-code
    subtask: task-3 R5：生成代码结构审计（只读），产出 codegen 三项优化与字段名撞名 BUG（R7 未派新活）
    status: inactive
    last_output_at: 2026-11-07T08:20:00+08:00
    last_heartbeat_at: N/A
    last_poll_at: 2026-11-08T00:38:00+08:00
    poll_count: 5
    retry_count: 0
    last_error_type: none
  - id: impl-threshold
    subtask: R5：JSONata 语法.md §16.1 的 RS 列与导出合计同步（R7 未派新活）
    status: inactive
    last_output_at: 2026-11-07T09:10:00+08:00
    last_heartbeat_at: N/A
    last_poll_at: 2026-11-08T00:38:00+08:00
    poll_count: 6
    retry_count: 0
    last_error_type: none
  - id: verify-issues
    subtask: task-4 R5：CI 四平台产物回传 + accel-bench job + 导出集合硬门禁（R7 未派新活）
    status: inactive
    last_output_at: 2026-11-07T10:20:00+08:00
    last_heartbeat_at: N/A
    last_poll_at: 2026-11-08T00:38:00+08:00
    poll_count: 7
    retry_count: 0
    last_error_type: none
  - id: op-inventory
    subtask: R7 Rust 侧：删 newfn.rs 的 o_mt + register() 注释，重建 _accel.pyd（已交付：1750 行）
    status: inactive
    last_output_at: 2026-11-07T23:31:00+08:00
    last_heartbeat_at: N/A
    last_poll_at: 2026-11-08T00:38:00+08:00
    poll_count: 13
    retry_count: 0
    last_error_type: none
  - id: perf-candidates
    subtask: R7 基准侧：清 evidence/accel_bench.py 与 accel_matrix.py 里 mt 相关条目与措辞（已交付）
    status: inactive
    last_output_at: 2026-11-07T23:35:00+08:00
    last_heartbeat_at: N/A
    last_poll_at: 2026-11-08T00:38:00+08:00
    poll_count: 12
    retry_count: 0
    last_error_type: none
  - id: verify-p2p3
    subtask: R7 打包发布：commit/push、等 CI、拉回 4 平台 artifact、重打三件 + twine check、建 GitHub Release v2.2.5（已交付）
    status: inactive
    last_output_at: 2026-11-08T00:33:00+08:00
    last_heartbeat_at: N/A
    last_poll_at: 2026-11-08T00:38:00+08:00
    poll_count: 10
    retry_count: 0
    last_error_type: none
  - id: fix-join-rename
    subtask: R7 验收报告.md：$match 四处旧事实 + 2.2.4→2.2.5 九处升版 + 版本口径段重写 + §7.3 尺寸表与终态落盘（R3/R5/R6/R7 六轮已交付）
    status: inactive
    last_output_at: 2026-11-08T00:33:00+08:00
    last_heartbeat_at: N/A
    last_poll_at: 2026-11-08T00:38:00+08:00
    poll_count: 6
    retry_count: 0
    last_error_type: none
  - id: recon-docs(R3-S7)
    subtask: 终态运行时状态文件 PROGRESS.md / PROGRESS_LOG.md 落盘（R3-S7、R4-S9、R5-S2/S3/S4、R6、R7-S3 共七次已交付）
    status: inactive
    last_output_at: 2026-11-07T23:29:16+08:00
    last_heartbeat_at: N/A
    last_poll_at: 2026-11-08T00:38:00+08:00
    poll_count: 5
    retry_count: 0
    last_error_type: none
```

## 验证状态
```yaml
验证状态:
  note: "R8 终态；本机 collected 2254、2245 passed / 9 skipped / 0 failed（干净树口径）；CI run 42396038435 为 2244 passed / 10 skipped（多出的 1 条是 CI 不装 [dev] extra 导致 importorskip(\"jsonata\") 跳过，非回归）"
  version: "2.2.5（已发布：GitHub Release 437694791）"
  ci_run: 42396038435
  ci_jobs: 14/14 success
  annotations_failure: 0
  collected: 2254
  passed: 2245
  skipped: 9
  failed: 0
  skip_detail: tests/test_codegen_snapshot.py 四条非确定 helper + tests/test_doc_samples.py 四条非确定函数（$millis/$now/$random/$shuffle）；工作区有未跟踪测试文件时「未跟踪清单」那条执行而非跳过，故为 8；干净树为 9
  head: "abdb79f"
  committed: true
  worktree: "干净（origin/main = abdb79f）；本轮仅待落盘 PROGRESS.md / PROGRESS_LOG.md / 验收报告.md 三份状态与旁述"
  commits_since_29fc3f2: 11
  commit_list: ["845d39f", "42b448d", "5f462b3", "26def4e", "08dc43b", "dae52f1", "a65ba59", "dfea3ec", "abb8f5f", "2db3bed", "abdb79f"]
  ci_runs:
    "42326765289": "failure（845d39f；9 pytest job + windows accel 红）"
    "42329016104": "success 14/14（42b448d）"
    "42330049019": "success 14/14（5f462b3）"
    "42332635002": "success 14/14（dae52f1）"
    "42337233796": "success 14/14（dfea3ec；GATES 16 PASS / 0 FAIL / 1 SKIP；4 平台各 16/16 exports；6 artifact）"
    "42392119927": "（T-A 修复前对照轮）annotation 188 条 / 其中 failure 级 161 条"
    "42396038435": "success 14/14（abdb79f；COUNT_SUMMARY 2244 passed / 10 skipped；GATES 16 PASS / 0 FAIL / 1 SKIP；annotation 27 条全为 warning/notice，failure 级 0）"
  gates_local: "17 PASS / 0 FAIL / 0 SKIP"
  gates_ci: "16 PASS / 0 FAIL / 1 SKIP（CYTHON_BENCH_GATE，CI 未装 [cython] extra）"
  accel_matrix: "RESULT: OK（16 名）"
  gen_jsonata_manual: "exit 0"
  rust_exports: "16（ue / ud / fb 已接线；mt 的 Rust 实现已于 2.2.5 删除，从未注册，故导出数不含它）"
  r7_new_tests: "tests/test_accel_surrogate.py 13 条 / tests/test_runtime_edge_cases.py 43 条（均未跟踪，已登记进验收报告 §3.1 与 §3.5 ②）"
  docs_lines: {"README.md": 1959, "JSONata 语法.md": 1009, "架构": 883, "接口": 947, "验收报告": 710, "Join": 433}
  dist:
    whl: {"name": "dcg-2.2.5-py3-none-any.whl", "size": 1098565, "sha256_16": "786e246230090f5d"}
    tar_gz: {"name": "dcg-2.2.5.tar.gz", "size": 1362108, "sha256_16": "5827267c36360b9e"}
    src_zip: {"name": "DCG-2.2.5-src.zip", "size": 1403534, "sha256_16": "98de827891bd9753"}
    twine_check: "三项 PASSED（2.2.5，显式三文件名）"
  accel_bins:
    "_accel.abi3.so": {"size": 621592, "sha256_16": "dd04a054986f462b", "exports": "16/16", "source": "CI（2.2.5，run 42390397831）"}
    "_accel-macos-arm64.abi3.so": {"size": 585680, "sha256_16": "8872c912307210f3", "exports": "16/16", "source": "CI（2.2.5，run 42390397831）"}
    "_accel-macos-x64.abi3.so": {"size": 573872, "sha256_16": "2f767e3df8ad3088", "exports": "16/16", "source": "CI（2.2.5，run 42390397831）"}
    "_accel.pyd": {"size": 348160, "sha256_16": "ec2616506eab43c5", "exports": "16/16", "source": "CI（2.2.5，run 42390397831）"}
  release: "GitHub Release v2.2.5 已发布 https://github.com/CL98K/DCG/releases/tag/v2.2.5（id 437694791，3 asset 全 uploaded，tag 指 abb8f5f；旧 v2.2.3 / v2.2.4 保留）"
  pypi: "凭据全缺（~/.pypirc 无 / 5 个环境变量 MISSING / pip config 空 / keyring 无条目 / 无 twine）；用户明确「先不管」"
```

## 说明
* 每个检查点覆写当前状态区；日志追加 `PROGRESS_LOG.md`。
* 恢复只读当前状态区。
* 计数口径：poll_count 为本会话累计轮询次数（R5-S4 为 60 → R6 为 66 → R7 为 72 → R8-S1 为 74 → R8-S2 为 76）；silent_stop_count 0；escalation_count 0；timed_brief_count 13 / timed_full_count 11。
* R5 四条方法论沉淀已写入 `done` 第 9 条：分支命中计数 / helper-only 判据 / PE 段级比对 / 禁用 PowerShell Set-Content 改源码。
* R6 版本升版：A 类 9 处必改已全部落地；B 类历史陈述（「2.2.3 起 / 已在 2.2.3 移除」等 19 条记录）保持不动，避免把历史陈述改假。
* 守护缺口（R8 已修）：`tests/test_docs_consistency.py` 的 `_VERSION_MARKER` 原只守 `README.md` 与 `验收报告.md`，R8 的 T-B 已扩到六份文档（新增 `DCG 接口实现细节文档.md` 与 `_ALL_DOCS` 六元组），该文件用例数 20 → 25。
* R7：三缺陷（C1 / C2 / D3）已修并独立验收；`newfn.rs` 从 1857 → **1750 行**，`o_mt` 与 `#[pyo3(name="mt")]` 已彻底删除。2.2.5 已打包（whl 1,098,565 B / tar.gz 1,362,108 B / src.zip 1,403,534 B，twine check 3/3 PASSED），4 平台二进制已回填（CI run `42390397831`，success 14/14，6 个 artifact 齐）；`验收报告.md` 现 710 行、collected 2254、passed 2245 / 9 skipped（本机干净树口径）。
* C1 语义订正（派单时曾写错）：修完后 `_ue` / `_du` 在**孤立代理项**上 live 与 pure **一致**——`_ue('\ud800')` 两边都抛 `UnicodeEncodeError`、`_du('\ud800')` 两边都返回 `'\ud800'`；**不是「都返回 undefined」**。根因：`dcg/runtime.py:857 _ue` 的 `except (AttributeError, TypeError)` 不含 `UnicodeError`，而 `newfn.rs` 的 `o_eu` 改为「提取失败返回 None」让 Python 接管、异常原样透出。
* 写域划分（R5/R6）：dcg/_accel/**（task-1）、dcg/runtime.py（task-2）、.github/workflows/ci.yml（task-4）、evidence/ 新增文件（task-5）、文档（recon-docs / impl-threshold）、只读（task-3）。
