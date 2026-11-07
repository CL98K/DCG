# PROGRESS

## 当前状态
```yaml
task: R5 Rust 化 + 真实配比基准 + CI 四平台产物回传 + $match 缓存 + 生成码与不可 Rust 算子审计（对应 user m00885）——① 把值得做的 Python JSONata 算子用 Rust 实现并按真实配比对比（≥1.3× 才接线；$sort 不优化）② $match 若缓存可解则加缓存 ③ 审计生成代码结构与不可 Rust 算子的优化空间 ④ 基准以 GitHub CI 为准，并在 CI 生成 4 个平台二进制后拉回本地（本地无 Mac）；改动完成后 commit；按 PyPI 规范打包
step: R5-S2 技术改动全部落地，进入「commit → CI 构建 → 拉回 4 平台产物」阶段 A
done:
  - Rust 新增 3 个算子 $encodeUrlComponent / $decodeUrlComponent / $formatBase，导出面 13 → 16；$match（o_mt）实现保留但故意不注册（实测 0.56–0.68× 负优化）
  - 三个新算子的门控：_fb 按 |n| >= 10**6（_FB_RUST_MIN）、_ud 按 '%' in v（盈亏平衡点约 2%）、_ue 无条件接
  - 真实配比实测（evidence/accel_bench.py）：ue helper_only 4.567×、ud 4.462×、fb 大值档 1.820× → 全部 ≥1.3× 达标；fb 的 C 基数档与 ud 纯文本档为 gate_intercept（rust_calls=0，即门控正确拦下）
  - codegen 优化三项：_rx 提到模块级（P3）、rows/dict 补 CSE（P1，x1.678–1.830）、dict 重复列共享（P2，x1.975–2.522）
  - 字段名撞内部名 BUG 修复（曾静默错值：名为 U 的字段全返回 undefined）
  - P0 跨平台崩溃修复：入库 3 个 .so 仍是 13 导出 + runtime.py 无守卫 → Linux/macOS 用户 AttributeError 崩溃。修复含 _CAP / _HAS_* 16 个能力标志 + _ue/_ud/_fb 双条件守卫；dcg/_accel/__init__.py 加 REQUIRED_EXPORTS + 加载时校验 + status()["missing"]；ci.yml 加「入库二进制导出集合」硬门禁
  - CI：新增 accel-bench job；accel job 四平台统一上传（artifact 名 accel-<平台>-<架构>）；导出断言改从 accel_matrix.EXPECTED_EXPORTS 单一来源解析（不再硬编码）
  - 全量测试 2185 passed / 8 skipped / 0 failed（total 2193）；tests/test_sync_contract.py 19、三份文档测试 71、tests/test_accel_gate.py 40（36 + TestExportContract 4）
  - 新增测试文件 6 个：test_accel_capability.py（14）、test_codegen_optimizations.py（30）、test_field_name_collision.py（80）、test_runtime_distinct.py（14）、test_runtime_round.py（77）、test_accel_thresholds.py（8）；磁盘 tests/test_*.py 共 38 个
doing:
  - T5 verify-p2p3：执行 commit + push + 等 CI 四平台构建 + 拉回 artifact（阶段 A）
blocked: 入库 3 个 .so（Linux x64 + 2×macOS）仍是 13 导出，待 CI 构建后拉回覆盖；wheel 需用 CI 产物重打
risk:
  - 3 个 .so 仍是 13 导出 → Linux/macOS 上三个新算子暂退回纯 Python（已有 _HAS_* 守卫，不再崩，但拿不到加速）
  - wheel 内 4 个产物全是旧的（11-07 02:54 打的，pyd 仍是 335,872 B 旧版）
  - 字节不可复现：PE 时间戳 / PDB GUID 导致同一源码重建的 sha256 不同，SOURCE_DATE_EPOCH 在本链路无效 → 漂移检测只能作 warning，改由「导出集合字节探测」作硬门禁
  - dist/DCG-2.2.3-src.zip 不合 PyPI sdist 规范（带前缀目录 + 非 PEP 517），twine check 会拒，只能作 GitHub Release 附件
  - 若 2.2.3 已在 PyPI 上线，需先升版本再重打（pyproject.toml:7 与 dcg/__init__.py:10 两处同步）
  - zp helper 波动 1.270–1.543× 跨 1.3 两侧（CI 失败线是 1.0，余量 27%，不阻塞）
  - C1 真·双活违约（R5 只登记不修）：_eu("\ud800") live=undefined / pure=UnicodeEncodeError；_du("\ud800") live=undefined / pure='\ud800'。成因：Rust 侧 extract::<String>() 遇孤立代理项返回 Err → UNDEF，Python 侧 except 只捕 (AttributeError, TypeError)
  - C2 非双活违约（只登记）：_fb(inf) → OverflowError、_fb(nan) → ValueError，live 与 pure 一致（都抛），是 runtime.py 本身没接住
  - $fromMillis 9999 年越界（只登记）：_fms(253402300799999, '[X]') → ValueError: year 10000 is out of range；[X] 365/365 全年抛、[x] 31/365、[w] 5/365、[W] 0；抛点 dcg/runtime.py:2425，调用方 :2454。越界 ms 本身有保护（253402300800000 → undefined），只是 [X]/[x]/[w] 绕过了
next: ① 拉回 4 平台产物并覆盖 dcg/_accel/ → 第二次 commit ② 重打 wheel / sdist ③ 终局全量复测 + evidence/accept.py 17 门禁 + gen_jsonata_manual.py 一致性 ④ 验收报告.md §5.5 补 CI 基准数字 ⑤ 终报
eta: 等 CI 四平台构建完成后拉回产物即可收口
team_mode: 启用（R5 复用在册 9 teammate）
updated_at: 2026-11-07T09:56:52+08:00
lead_poll_at: 2026-11-07T09:56:52+08:00
poll_count: 52
timed_brief_count: 10
timed_full_count: 8
silent_stop_count: 0
escalation_count: 0
teammates:
  - id: recon-docs
    subtask: R5-S2 状态文件落盘（PROGRESS.md / PROGRESS_LOG.md）+ README §7.3 尺寸同步（已交付）
    status: running
    last_output_at: 2026-11-07T09:56:52+08:00
    last_heartbeat_at: N/A
    last_poll_at: 2026-11-07T09:56:52+08:00
    poll_count: 11
    retry_count: 0
    last_error_type: none
  - id: recon-code
    subtask: task-3 R5：生成代码结构审计（只读，报告到 %TEMP%）
    status: inactive
    last_output_at: 2026-11-07T08:20:00+08:00
    last_heartbeat_at: N/A
    last_poll_at: 2026-11-07T09:56:52+08:00
    poll_count: 5
    retry_count: 0
    last_error_type: none
  - id: impl-threshold
    subtask: R5：JSONata 语法.md §16.1 的 RS 列与导出合计（13 → 16）同步（已交付）
    status: inactive
    last_output_at: 2026-11-07T09:10:00+08:00
    last_heartbeat_at: N/A
    last_poll_at: 2026-11-07T09:56:52+08:00
    poll_count: 6
    retry_count: 0
    last_error_type: none
  - id: verify-issues
    subtask: task-4 R5：CI 四平台产物回传 + accel-bench job + 入库二进制导出集合硬门禁（写域 .github/workflows/ci.yml）
    status: running
    last_output_at: 2026-11-07T09:50:00+08:00
    last_heartbeat_at: N/A
    last_poll_at: 2026-11-07T09:56:52+08:00
    poll_count: 6
    retry_count: 0
    last_error_type: none
  - id: op-inventory
    subtask: task-1 R5：Rust 侧 ue/ud/fb 实现 + 注册已有 o_mt（写域 dcg/_accel/**）
    status: inactive
    last_output_at: 2026-11-07T08:45:00+08:00
    last_heartbeat_at: N/A
    last_poll_at: 2026-11-07T09:56:52+08:00
    poll_count: 5
    retry_count: 0
    last_error_type: none
  - id: perf-candidates
    subtask: task-2 R5：$match 缓存 + $formatNumber 解析链 + 不可 Rust 算子审计 + _HAS_* 能力守卫（写域 dcg/runtime.py）
    status: inactive
    last_output_at: 2026-11-07T09:40:00+08:00
    last_heartbeat_at: N/A
    last_poll_at: 2026-11-07T09:56:52+08:00
    poll_count: 6
    retry_count: 0
    last_error_type: none
  - id: verify-p2p3
    subtask: task-5 R5：真实配比基准框架 evidence/accel_bench.py + 阶段 A commit/push/CI/拉回 artifact
    status: running
    last_output_at: 2026-11-07T09:52:00+08:00
    last_heartbeat_at: N/A
    last_poll_at: 2026-11-07T09:56:52+08:00
    poll_count: 7
    retry_count: 0
    last_error_type: none
  - id: fix-join-rename
    subtask: docs/join-design.md 改名后的死链与契约测试修复（R3 已交付，R5 未派新活）
    status: inactive
    last_output_at: 2026-11-07T01:10:00+08:00
    last_heartbeat_at: N/A
    last_poll_at: 2026-11-07T09:56:52+08:00
    poll_count: 3
    retry_count: 0
    last_error_type: none
  - id: recon-docs(R3-S7)
    subtask: 终态运行时状态文件 PROGRESS.md / PROGRESS_LOG.md 落盘（R3-S7、R4-S9 两次已交付）
    status: inactive
    last_output_at: 2026-11-07T03:58:00+08:00
    last_heartbeat_at: N/A
    last_poll_at: 2026-11-07T09:56:52+08:00
    poll_count: 4
    retry_count: 0
    last_error_type: none
```

## 验证状态
```yaml
验证状态:
  note: "R5-S2 实测数字；阶段 B（拉回 CI 产物 + 重打 wheel）后需再复测一次"
  collected: 2193
  passed: 2185
  skipped: 8
  failed: 0
  skip_detail: tests/test_codegen_snapshot.py:155 四条非确定 helper + tests/test_doc_samples.py:107 四条非确定函数（$millis/$now/$random/$shuffle）
  skip_range: "range(8, 10)"
  gates: "evidence/accept.py 17 项（阶段 B 后复跑）"
  gen_jsonata_manual: "阶段 B 复跑一致性"
  worktree: "92 = M33 / D24 / ??35"
  committed: false
  head: "29fc3f2"
  test_files: 38
  key_suites: "test_sync_contract 19 / 三份文档测试 71 / test_accel_gate 40（36 + TestExportContract 4）"
  docs_lines: {"README.md": 1958, "JSONata 语法.md": 1008, "架构": 882, "接口": 946, "验收报告": 645, "Join": 432}
  clone_threshold: $clone 口径已在三处收口（元素类型未控制，_CL_MIN_SEQ 维持 8、_CL_MIN_DICT 维持 2）
  accel_exports: "源码 16（EXPECTED_EXPORTS）；入库二进制 Windows 16 / Linux 13 / macOS×2 13 —— 待 CI 覆盖"
```

## 说明
* 每个检查点覆写当前状态区；日志追加 `PROGRESS_LOG.md`。
* 恢复只读当前状态区。
* 计数口径：poll_count 为本会话累计轮询次数；silent_stop_count 0；escalation_count 本轮 0（历史轮曾因 32 分钟零落盘催办 1 次）。
* R5 写域划分：dcg/_accel/**（task-1）、dcg/runtime.py（task-2）、.github/workflows/ci.yml（task-4）、evidence/ 新增文件（task-5）、文档（recon-docs / impl-threshold）、只读（task-3）。
