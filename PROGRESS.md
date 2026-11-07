# PROGRESS

## 当前状态
```yaml
task: R7 修 C1/C2/D3 三个已登记缺陷 + 删 $match 的 Rust 死代码 + 出 2.2.5
step: R7 收尾：C1/C2/D3 已修 + $match Rust 死代码已删，升版 2.2.5，待 commit/CI/打包
done:
  - C1 双活违约已修（改 Rust：o_eu/o_du 提取失败分支 UNDEF→None）；Lead 独立复核 live/pure 逐值一致，6514 次差分对拍 mismatch 0，真 emoji 仍走 Rust
  - C2 $formatBase(±inf/nan) 已返回 undefined（dcg/runtime.py:942 `_fb` 非有限数分支）；正常值逐值不变，radix 参数按裁决不动
  - D3 $fromMillis 9999 年越界已修（dcg/runtime.py:2612 `_start_first_week` 越界返回 None + 四调用点显式判空）；基线对比 26296 格：401 处异常全消失、0 回归、年份 1..9998 逐值 0 差异
  - $match 的 o_mt 已从 newfn.rs 删除（1857 → 1750 行）；--only 传未知算子名时加 stderr 警告；verdict_counts 变为 {keep: 16, gate_intercept: 3}，deliberately_not_wired 已无实例
  - 验收报告.md 的 $match 四处旧事实同步（verdict_counts 行 / 表 B 判定列 / 读法第 4 条 / --only 复跑命令去掉 ,mt）；tests/test_sync_contract.py 19 passed
  - 升版 2.2.4 → 2.2.5 共 9 处（pyproject.toml / dcg/__init__.py / README manifest 样例 / README §7.3 产物名 / 验收报告 :3 :28 / 接口文档 :843 / test_docs_consistency 注释）；验收报告 :34 版本口径段重写为「计数与门禁在 2.2.5 树上实测，§5.1–§5.6 基准仍取 2.2.3 的 CI 跑次」
  - 两份计数同步至 2249（验收报告 §3.1 40 个测试文件、合计 2249；§3.5 ② 改「跨轮新增守护测试（6 个）」）；全量 2241 passed / 8 skipped / 0 failed（total 2249），gen_jsonata_manual.py exit 0
doing: []
blocked:
  - PyPI 凭据全缺（~/.pypirc 无 / 5 个环境变量 MISSING / pip config 空 / keyring 无条目 / 无 twine），用户明确「先不管」
risk:
  - 构建字节不可复现（PE 时间戳/PDB GUID，SOURCE_DATE_EPOCH 无效）→ 漂移检测只能 warning
  - zp helper 1.270–1.543× 跨 1.3 两侧（CI 失败线 1.0，余量 27%）
  - $formatBase(255, inf) 仍抛 OverflowError（radix 侧刻意不修，有测试钉住）
  - 2.2.5 的 3 个 Unix .so 仍是 2.2.4 的 CI 产物（只本机重建了 _accel.pyd），待 CI 重建后替换并更新 §7.3 尺寸表
next:
  - commit（verify-p2p3 统一做）→ push 触发 CI → 拉回 4 平台二进制替换 dcg/_accel/ → 重打包三件 + twine check → 更新 §7.3 尺寸表与验收报告二进制 sha → GitHub Release v2.2.5 → 终报
eta: R7 剩余「commit/CI/拉回 4 平台 + 重打包发布 + 终报」，约 2 轮 CI
team_mode: 启用（9 teammate：fix-join-rename 在跑，其余 inactive）
updated_at: 2026-11-07T23:47:00+08:00
lead_poll_at: 2026-11-07T23:47:00+08:00
poll_count: 71
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
    last_poll_at: 2026-11-07T23:47:00+08:00
    poll_count: 19
    retry_count: 0
    last_error_type: none
  - id: recon-code
    subtask: task-3 R5：生成代码结构审计（只读），产出 codegen 三项优化与字段名撞名 BUG（R7 未派新活）
    status: inactive
    last_output_at: 2026-11-07T08:20:00+08:00
    last_heartbeat_at: N/A
    last_poll_at: 2026-11-07T23:47:00+08:00
    poll_count: 5
    retry_count: 0
    last_error_type: none
  - id: impl-threshold
    subtask: R5：JSONata 语法.md §16.1 的 RS 列与导出合计同步（R7 未派新活）
    status: inactive
    last_output_at: 2026-11-07T09:10:00+08:00
    last_heartbeat_at: N/A
    last_poll_at: 2026-11-07T23:47:00+08:00
    poll_count: 6
    retry_count: 0
    last_error_type: none
  - id: verify-issues
    subtask: task-4 R5：CI 四平台产物回传 + accel-bench job + 导出集合硬门禁（R7 未派新活）
    status: inactive
    last_output_at: 2026-11-07T10:20:00+08:00
    last_heartbeat_at: N/A
    last_poll_at: 2026-11-07T23:47:00+08:00
    poll_count: 7
    retry_count: 0
    last_error_type: none
  - id: op-inventory
    subtask: R7 Rust 侧：删 newfn.rs 的 o_mt + register() 注释，重建 _accel.pyd（已交付：1750 行 / pyd sha 3d2eebe058aa298f）
    status: inactive
    last_output_at: 2026-11-07T23:31:00+08:00
    last_heartbeat_at: N/A
    last_poll_at: 2026-11-07T23:47:00+08:00
    poll_count: 13
    retry_count: 0
    last_error_type: none
  - id: perf-candidates
    subtask: R7 基准侧：清 evidence/accel_bench.py 与 accel_matrix.py 里 mt 相关条目与措辞（已交付）
    status: inactive
    last_output_at: 2026-11-07T23:35:00+08:00
    last_heartbeat_at: N/A
    last_poll_at: 2026-11-07T23:47:00+08:00
    poll_count: 12
    retry_count: 0
    last_error_type: none
  - id: verify-p2p3
    subtask: task-5 R5：真实配比基准框架 + commit/push/等 CI/拉回 artifact/重打 wheel（R7 待派 2.2.5 打包与 commit）
    status: inactive
    last_output_at: 2026-11-07T11:00:00+08:00
    last_heartbeat_at: N/A
    last_poll_at: 2026-11-07T23:47:00+08:00
    poll_count: 9
    retry_count: 0
    last_error_type: none
  - id: fix-join-rename
    subtask: R7 验收报告.md：$match 四处旧事实 + 2.2.4→2.2.5 九处升版 + 版本口径段重写（R3/R5/R6/R7 五轮已交付）
    status: running
    last_output_at: 2026-11-07T23:47:00+08:00
    last_heartbeat_at: N/A
    last_poll_at: 2026-11-07T23:47:00+08:00
    poll_count: 5
    retry_count: 0
    last_error_type: none
  - id: recon-docs(R3-S7)
    subtask: 终态运行时状态文件 PROGRESS.md / PROGRESS_LOG.md 落盘（R3-S7、R4-S9、R5-S2/S3/S4、R6、R7-S3 共七次已交付）
    status: inactive
    last_output_at: 2026-11-07T23:29:16+08:00
    last_heartbeat_at: N/A
    last_poll_at: 2026-11-07T23:47:00+08:00
    poll_count: 5
    retry_count: 0
    last_error_type: none
```

## 验证状态
```yaml
验证状态:
  note: "R7 收尾本机实测值；三缺陷已修、$match 的 Rust 死代码已删、已升版 2.2.5；等 commit / CI / 重打包 / Release"
  version: "2.2.5（2.2.5 待发布：commit/CI/打包未做）"
  collected: 2249
  passed: 2241
  skipped: 8
  failed: 0
  skip_detail: tests/test_codegen_snapshot.py 四条非确定 helper + tests/test_doc_samples.py 四条非确定函数（$millis/$now/$random/$shuffle）；工作区有未跟踪测试文件时「未跟踪清单」那条执行而非跳过，故为 8；干净树为 9
  head: "0e4eef1"
  committed: false
  worktree: "dirty：M 架构文档 / JSONata 语法.md / dcg/runtime.py / dcg/_accel/src/newfn.rs / dcg/_accel/_accel.pyd / evidence/accel_bench.py / evidence/accel_matrix.py / tests/test_accel_gate.py / 验收报告.md；?? tests/test_accel_surrogate.py、tests/test_runtime_edge_cases.py"
  commits_since_29fc3f2: 8
  commit_list: ["845d39f", "42b448d", "5f462b3", "26def4e", "08dc43b", "dae52f1", "a65ba59", "dfea3ec"]
  ci_runs:
    "42326765289": "failure（845d39f；9 pytest job + windows accel 红）"
    "42329016104": "success 14/14（42b448d）"
    "42330049019": "success 14/14（5f462b3）"
    "42332635002": "success 14/14（dae52f1）"
    "42337233796": "success 14/14（dfea3ec；GATES 16 PASS / 0 FAIL / 1 SKIP；4 平台各 16/16 exports；6 artifact）"
  gates_local: "17 PASS / 0 FAIL / 0 SKIP"
  gates_ci: "16 PASS / 0 FAIL / 1 SKIP（CYTHON_BENCH_GATE，CI 未装 [cython] extra）"
  accel_matrix: "RESULT: OK（16 名）"
  gen_jsonata_manual: "exit 0"
  rust_exports: "16（ue / ud / fb 已接线；mt 的 Rust 实现已于 2.2.5 删除，从未注册，故导出数不含它）"
  r7_new_tests: "tests/test_accel_surrogate.py 13 条 / tests/test_runtime_edge_cases.py 43 条（均未跟踪，已登记进验收报告 §3.1 与 §3.5 ②）"
  docs_lines: {"README.md": 1959, "JSONata 语法.md": 1009, "架构": 883, "接口": 947, "验收报告": 708, "Join": 433}
  dist:
    whl: {"name": "dcg-2.2.4-py3-none-any.whl", "size": 1097908, "sha256_16": "7def19fed6355e73"}
    tar_gz: {"name": "dcg-2.2.4.tar.gz", "size": 1353544, "sha256_16": "eda6b608fe24cc79"}
    src_zip: {"name": "DCG-2.2.4-src.zip", "size": 1393612, "sha256_16": "2ee9620aa65806b0"}
    twine_check: "三项 PASSED（2.2.5 需重打）"
  accel_bins:
    "_accel.abi3.so": {"size": 621672, "sha256_16": "2835a6fe01062610", "exports": "16/16", "source": "CI（2.2.4）；2.2.5 需 CI 重建"}
    "_accel-macos-arm64.abi3.so": {"size": 585680, "sha256_16": "fe4f5033c9b5ff14", "exports": "16/16", "source": "CI（2.2.4）；2.2.5 需 CI 重建"}
    "_accel-macos-x64.abi3.so": {"size": 573872, "sha256_16": "03767ffc33a1504b", "exports": "16/16", "source": "CI（2.2.4）；2.2.5 需 CI 重建"}
    "_accel.pyd": {"size": 348160, "sha256_16": "3d2eebe058aa298f", "exports": "15/15（o_mt 已删）", "source": "本机重建 23:25:33；2.2.5 需 CI 重建"}
  release: "GitHub Release v2.2.4 已发布 https://github.com/CL98K/DCG/releases/tag/v2.2.4（id 437325295，3 asset 全 uploaded，tag 指 dfea3ec；旧 v2.2.3 保留）"
  pypi: "凭据全缺（~/.pypirc 无 / 5 个环境变量 MISSING / pip config 空 / keyring 无条目 / 无 twine）；用户明确「先不管」"
```

## 说明
* 每个检查点覆写当前状态区；日志追加 `PROGRESS_LOG.md`。
* 恢复只读当前状态区。
* 计数口径：poll_count 为本会话累计轮询次数（R5-S4 为 60 → R6 为 66 → R7 为 70）；silent_stop_count 0；escalation_count 0；timed_brief_count 13 / timed_full_count 11。
* R5 四条方法论沉淀已写入 `done` 第 9 条：分支命中计数 / helper-only 判据 / PE 段级比对 / 禁用 PowerShell Set-Content 改源码。
* R6 版本升版：A 类 9 处必改已全部落地；B 类历史陈述（「2.2.3 起 / 已在 2.2.3 移除」等 19 条记录）保持不动，避免把历史陈述改假。
* 已知守护缺口（只登记）：`tests/test_docs_consistency.py` 的 `_VERSION_MARKER` 只守 `README.md` 与 `验收报告.md`，接口文档 / Join 文档 / JSONata 语法 / 架构文档的版本串无自动化守护。
* R7-S3：三缺陷（C1 / C2 / D3）已修并独立验收；`验收报告.md` 现 703 行、collected 2249、passed 2241 / skipped 8；`newfn.rs` 从 1857 → **1750 行**，`o_mt` 与 `#[pyo3(name="mt")]` 已彻底删除，`_accel.pyd` 已本机重建（`3d2eebe058aa298f`，348,160 B，23:25:33），待 CI 回填 4 平台。
* C1 语义订正（派单时曾写错）：修完后 `_ue` / `_du` 在**孤立代理项**上 live 与 pure **一致**——`_ue('\ud800')` 两边都抛 `UnicodeEncodeError`、`_du('\ud800')` 两边都返回 `'\ud800'`；**不是「都返回 undefined」**。根因：`dcg/runtime.py:857 _ue` 的 `except (AttributeError, TypeError)` 不含 `UnicodeError`，而 `newfn.rs` 的 `o_eu` 改为「提取失败返回 None」让 Python 接管、异常原样透出。
* 写域划分（R5/R6）：dcg/_accel/**（task-1）、dcg/runtime.py（task-2）、.github/workflows/ci.yml（task-4）、evidence/ 新增文件（task-5）、文档（recon-docs / impl-threshold）、只读（task-3）。
