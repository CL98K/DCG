# PROGRESS

## 当前状态
```yaml
task: R5 Rust 化 + 真实配比基准 + CI 四平台产物回传 + $match 缓存 + 生成码与不可 Rust 算子审计（对应 user m00885）——① 把值得做的 Python JSONata 算子用 Rust 实现并按真实配比对比（≥1.3× 才接线；$sort 不优化）② $match 若缓存可解则加缓存 ③ 审计生成代码结构与不可 Rust 算子的优化空间 ④ 基准以 GitHub CI 为准，并在 CI 生成 4 个平台二进制后拉回本地（本地无 Mac）；改动完成后 commit；按 PyPI 规范打包
step: R5-S4 全部收口（6 commit、CI 四轮全绿、wheel/sdist 合规待发布）
done:
  - 五路成果之一｜Rust 导出面 13 → 16：新增 $encodeUrlComponent（ue）/ $decodeUrlComponent（ud）/ $formatBase（fb）；$match（o_mt）实现保留但故意不注册（实测 0.56–0.68× 负优化）。门控：fb 按 |n| >= 10**6（_FB_RUST_MIN）、ud 按 '%' in v（盈亏平衡 2%）、ue 无条件接
  - 五路成果之二｜真实配比实测（evidence/accel_bench.py）：ue helper_only 4.567×、ud 4.462×、fb 大值档 1.820×，全部 ≥1.3× 达标；fb 的 C 基数档与 ud 纯文本档为 gate_intercept（rust_calls=0，门控正确拦下）
  - 五路成果之三｜codegen 优化：_rx 提模块级（P3）、rows/dict 补 CSE（P1，x1.678–1.830）、dict 重复列共享（P2，x1.975–2.522）；字段名撞内部名 BUG 修复（曾静默错值，名为 U 的字段全返回 undefined）
  - 五路成果之四｜P0 跨平台崩溃修复（三路）：_CAP/_HAS_* 16 个能力标志 + _ue/_ud/_fb 双条件守卫；dcg/_accel/__init__.py 加 REQUIRED_EXPORTS 加载校验 + status()["missing"]；ci.yml 加「入库二进制导出集合」硬门禁
  - 五路成果之五｜CI：新增 accel-bench job；accel job 四平台统一上传（artifact 名 accel-<平台>-<架构>）；导出断言改从 accel_matrix.EXPECTED_EXPORTS 单一来源解析
  - 6 个 commit 已 push（29fc3f2..dae52f1）：845d39f feat(accel) 三算子+门控+codegen+CI 回传 / 42b448d fix(accel) 回填 3 平台 16 导出二进制+CI 编码与门禁顺序 / 5f462b3 docs(accel) 回填 Windows 二进制为 CI 产物+验收报告补表 B / 26def4e docs(readme) §7.3 尺寸表随 CI 二进制更新 / 08dc43b chore(progress) R5-S3 终态落盘 / dae52f1 docs(accel) §5.5 读法修正 + newfn.rs 冷门基数注释订正
  - CI 四轮：42326765289（845d39f）failure（9 pytest job + windows accel 红）→ 42329016104（42b448d）success 14/14 → 42330049019（5f462b3）success 14/14 → 42332635002（dae52f1）success 14/14，GATES 16 PASS / 0 FAIL / 1 SKIP，4 平台各 16/16 exports，6 artifact 齐
  - 4 平台二进制全部来自 CI 并已回填仓库：_accel.abi3.so 621,672 B / _accel-macos-arm64.abi3.so 585,680 B / _accel-macos-x64.abi3.so 573,872 B / _accel.pyd 348,160 B，均 16/16 导出
  - 打包完成：dist/ 三件（whl 1,097,911 B / tar.gz 1,353,548 B / src.zip 1,393,624 B）+ 三个 .sha256 已重写；twine check 三项 PASSED
  - 测试基线：2184 passed / 9 skipped / 0 failed（total 2193）；三份文档测试 79 passed / 1 skipped（total 80）；evidence/accept.py 17 PASS / 0 FAIL / 0 SKIP；evidence/accel_matrix.py RESULT: OK（16 名）；tests/gen_jsonata_manual.py exit 0
  - 方法论沉淀（本轮四次踩坑换来的，已写入文档/注释）｜① 语义等价不能证明实验组真的执行了预期分支：逐值对拍 0 不一致只证明两边算得一样，必须有分支命中计数（$parseInteger 单图样 0.67× 假象、$formatBase 位长门控两轮自我更正都栽在这里）② 判据要用 helper-only 而不是端到端（$zip helper 1.650× / 端到端 1.132×，op_share 稀释是结构性结果）③ sha256 不能证明注释没进二进制：Rust 构建非位可复现，要用 PE 段级比对（.text 逐字节相同）④ 改 Rust/源码不能用 PowerShell Set-Content（默认带 BOM）
doing: []
blocked:
  - 发布受阻①：GitHub Release v2.2.3 的 4 个 asset 仍是旧产物（whl 1,034,255 B / src.zip 1,751,744 B 且结构不合规），tag 仍指 4fafad3；等用户选「同版本覆盖」还是「升版 2.2.4」
  - 发布受阻②：PyPI 凭据全缺（~/.pypirc 无、5 个环境变量全 MISSING、pip config 空、keyring 无条目、无 twine）→ 无法上传
risk:
  - Release 旧 asset 与「bench 测的 = 发布的那份」冲突：本地 dist/ 已是 CI 产物重打的新件，但 Release 上挂的仍是旧件，用户从 Release 下载会拿到旧二进制
  - tag v2.2.3 仍指 4fafad3（旧提交），与 HEAD dae52f1 不一致；若同版本号覆盖需重打 tag，若升版需新建 2.2.4 全套
  - 若 2.2.3 已在 PyPI 上线，需先升版本再重打（pyproject.toml:7 与 dcg/__init__.py:10 两处同步），否则同版本号内容不一致
  - 构建字节不可复现：PE 时间戳 / PDB GUID 导致同一源码重建的 sha256 不同，SOURCE_DATE_EPOCH 在本链路无效 → 漂移检测只能作 warning，硬门禁改用「导出集合字节探测」
  - C1 真·双活违约（只登记不修）：_eu("\ud800") / _du("\ud800") 在 Rust 可用时返回 undefined，纯 Python 时分别抛 UnicodeEncodeError / 返回 '\ud800'
  - C2 非双活违约（Python 侧自身未捕获，只登记）：_fb(inf) → OverflowError、_fb(nan) → ValueError，live 与 pure 一致（都抛）
  - $fromMillis 9999 年越界（只登记不修）：[X] 全年 365/365 抛 ValueError: year 10000 is out of range，[x] 31/365、[w] 5/365、[W] 0；抛点 dcg/runtime.py:2425，调用方 :2454
  - zp helper 波动 1.270–1.543× 跨 1.3 两侧（CI 失败线是 1.0，余量 27%，不阻塞）
next:
  - 等用户决策：Release v2.2.3 旧 asset 处置（同版本覆盖 / 升版 2.2.4）与 PyPI 凭据提供
eta: 技术侧全部收口；仅剩发布决策与凭据，等用户
team_mode: 启用（R5 复用在册 9 teammate）
updated_at: 2026-11-07T11:15:13+08:00
lead_poll_at: 2026-11-07T11:15:13+08:00
poll_count: 60
timed_brief_count: 12
timed_full_count: 10
silent_stop_count: 0
escalation_count: 0
teammates:
  - id: recon-docs
    subtask: R5 文档侧：README §7.3 尺寸同步 + P0 崩溃链取证 + 状态文件落盘（R5-S1/S2/S3/S4）
    status: inactive
    last_output_at: 2026-11-07T11:15:13+08:00
    last_heartbeat_at: N/A
    last_poll_at: 2026-11-07T11:15:13+08:00
    poll_count: 13
    retry_count: 0
    last_error_type: none
  - id: recon-code
    subtask: task-3 R5：生成代码结构审计（只读，报告到 %TEMP%），产出 codegen 三项优化与字段名撞名 BUG
    status: inactive
    last_output_at: 2026-11-07T08:20:00+08:00
    last_heartbeat_at: N/A
    last_poll_at: 2026-11-07T11:15:13+08:00
    poll_count: 5
    retry_count: 0
    last_error_type: none
  - id: impl-threshold
    subtask: R5：JSONata 语法.md §16.1 的 RS 列与导出合计（13 → 16）同步
    status: inactive
    last_output_at: 2026-11-07T09:10:00+08:00
    last_heartbeat_at: N/A
    last_poll_at: 2026-11-07T11:15:13+08:00
    poll_count: 6
    retry_count: 0
    last_error_type: none
  - id: verify-issues
    subtask: task-4 R5：CI 四平台产物回传 + accel-bench job + 入库二进制导出集合硬门禁（写域 .github/workflows/ci.yml）
    status: inactive
    last_output_at: 2026-11-07T10:20:00+08:00
    last_heartbeat_at: N/A
    last_poll_at: 2026-11-07T11:15:13+08:00
    poll_count: 7
    retry_count: 0
    last_error_type: none
  - id: op-inventory
    subtask: task-1 R5：Rust 侧 ue/ud/fb 实现 + 注册已有 o_mt（写域 dcg/_accel/**）
    status: inactive
    last_output_at: 2026-11-07T08:45:00+08:00
    last_heartbeat_at: N/A
    last_poll_at: 2026-11-07T11:15:13+08:00
    poll_count: 5
    retry_count: 0
    last_error_type: none
  - id: perf-candidates
    subtask: task-2 R5：$match 缓存 + $formatNumber 解析链 + 不可 Rust 算子审计 + _HAS_* 能力守卫（写域 dcg/runtime.py）
    status: inactive
    last_output_at: 2026-11-07T10:10:00+08:00
    last_heartbeat_at: N/A
    last_poll_at: 2026-11-07T11:15:13+08:00
    poll_count: 7
    retry_count: 0
    last_error_type: none
  - id: verify-p2p3
    subtask: task-5 R5：真实配比基准框架 evidence/accel_bench.py + 阶段 A/B commit、push、等 CI、拉回 artifact、重打 wheel
    status: inactive
    last_output_at: 2026-11-07T11:00:00+08:00
    last_heartbeat_at: N/A
    last_poll_at: 2026-11-07T11:15:13+08:00
    poll_count: 9
    retry_count: 0
    last_error_type: none
  - id: fix-join-rename
    subtask: docs/join-design.md 改名后的死链与契约测试修复（R3 已交付，R5 未派新活）
    status: inactive
    last_output_at: 2026-11-07T01:10:00+08:00
    last_heartbeat_at: N/A
    last_poll_at: 2026-11-07T11:15:13+08:00
    poll_count: 3
    retry_count: 0
    last_error_type: none
  - id: recon-docs(R3-S7)
    subtask: 终态运行时状态文件 PROGRESS.md / PROGRESS_LOG.md 落盘（R3-S7、R4-S9 两次已交付）
    status: inactive
    last_output_at: 2026-11-07T03:58:00+08:00
    last_heartbeat_at: N/A
    last_poll_at: 2026-11-07T11:15:13+08:00
    poll_count: 4
    retry_count: 0
    last_error_type: none
```

## 验证状态
```yaml
验证状态:
  note: "R5-S4 终态实测值；技术侧全部通过，仅发布动作待用户决策"
  collected: 2193
  passed: 2184
  skipped: 9
  failed: 0
  skip_detail: tests/test_codegen_snapshot.py:155 四条非确定 helper + tests/test_doc_samples.py:107 四条非确定函数（$millis/$now/$random/$shuffle）+ 1 条环境相关
  doc_suites: "三份文档测试 79 passed / 1 skipped（total 80）"
  head: "dae52f1"
  committed: true
  upstream_sync: "origin/main = dae52f1（已同步）"
  worktree: "clean（git status --porcelain -uall 空）"
  commits_since_29fc3f2: 6
  ci_runs:
    "42326765289": "failure（845d39f；9 pytest job + windows accel 红）"
    "42329016104": "success 14/14（42b448d）"
    "42330049019": "success 14/14（5f462b3）"
    "42332635002": "success 14/14（dae52f1；GATES 16 PASS / 0 FAIL / 1 SKIP；4 平台各 16/16 exports；6 artifact）"
  gates_local: "17 PASS / 0 FAIL / 0 SKIP"
  gates_ci: "16 PASS / 0 FAIL / 1 SKIP（CYTHON_BENCH_GATE，CI 未装 [cython] extra）"
  accel_matrix: "RESULT: OK（16 名）"
  gen_jsonata_manual: "exit 0"
  rust_exports: "13 → 16（新增 ue / ud / fb；mt 故意不注册）"
  docs_lines: {"README.md": 1958, "JSONata 语法.md": 1008, "架构": 882, "接口": 946, "验收报告": 696, "Join": 432}
  dist: {"whl": 1097911, "tar.gz": 1353548, "src.zip": 1393624, "sha256": "三个 .sha256 已重写", "twine_check": "三项 PASSED"}
  accel_bins:
    "_accel.abi3.so": {"size": 621672, "sha256_16": "2835a6fe01062610", "exports": "16/16", "source": "CI"}
    "_accel-macos-arm64.abi3.so": {"size": 585680, "sha256_16": "fe4f5033c9b5ff14", "exports": "16/16", "source": "CI"}
    "_accel-macos-x64.abi3.so": {"size": 573872, "sha256_16": "03767ffc33a1504b", "exports": "16/16", "source": "CI"}
    "_accel.pyd": {"size": 348160, "sha256_16": "5602a3f5e23bdb66", "exports": "16/16", "source": "CI"}
  release: "GitHub Release v2.2.3 的 4 个 asset 仍是旧产物（whl 1,034,255 / src.zip 1,751,744 且不合规），tag 仍指 4fafad3"
  pypi: "凭据全缺（~/.pypirc 无 / 5 个环境变量 MISSING / pip config 空 / keyring 无条目 / 无 twine）"
```

## 说明
* 每个检查点覆写当前状态区；日志追加 `PROGRESS_LOG.md`。
* 恢复只读当前状态区。
* 计数口径：poll_count 为本会话累计轮询次数（R5-S3 为 58，本轮 +2 = 60）；silent_stop_count 0；escalation_count 0。
* 本轮四条方法论沉淀（R5-S4）已写入 `done` 末条：分支命中计数 / helper-only 判据 / PE 段级比对 / 禁用 PowerShell Set-Content 改源码。
* R5 写域划分：dcg/_accel/**（task-1）、dcg/runtime.py（task-2）、.github/workflows/ci.yml（task-4）、evidence/ 新增文件（task-5）、文档（recon-docs / impl-threshold）、只读（task-3）。
