# PROGRESS — DCG v2.1.0 项目进度

> 本文件长期入库维护（用户 m02343 决定）：每轮开工先看这里，收尾时更新。
> 规则来源：`AGENTS.md`。裁决记录：`_tmp/fix_plan/DECISIONS_v*.md`（不入库）。

## 当前状态（第十六轮增补，收尾）

**主题**：Cython 产物实测正式化（回答用户 m04377「我记得 cython 还没有测试？」）+ 13 项性能快照重跑（用户 m04359 选「1」）。

### 已完成

1. **Cython 生成侧测试原本就有**（不是空白）：`tests/test_codegen_snapshot.py:296`（每个局部都有 `cdef`）、
   `tests/test_e2e_flight.py:359/:376`（形态与行数）、`tests/test_examples_artifacts.py:74`（示例产物逐字节一致）、
   `tests/codegen_rules.py:390`。**缺的是性能实测**——验收 R4 原写「本机无 C 编译器（`cl` 不在 PATH）、预期 0 收益」。
2. **本机可实测**：只是无 MSVC `cl`；MinGW-w64 `gcc 15.1.0`（`D:\LenovoSoftstore\MinGW64\bin\gcc.exe`）+ CPython 3.13.3
   头文件/`libs\python313.lib` 可用。13 个 `.pyd` 全部编译导入成功，`run()` 输出与纯 Python 产物**逐值相同（`eq=True` × 13）**。
3. **新增正式证据**：`evidence/bench_cython.py`（三路交错：Cython 产物 / 纯 Python 产物 / 等价手写；协议同上「5 遍
   trimmed mean + 连跑 3 次取比值中位数」；无 C 编译器时写 `status=skipped` 且不失败）与 `evidence/cython_protocol.json`。
4. **实测结论**：13 项 `cy/py` 中位数 **1.02×**、均值 ≈1.00×、11 项在 ±10% 内；唯一实质收益 `order_pipeline`
   **0.71×**（6.63 → 4.77 µs/行，行容器负载）；唯一略慢 `hires_reduce` 1.13×。→ Cython 保留为可选产物形态、
   **不宣称加速**。验收报告新增 §5.6 专节；R4 从「未验证」结案。
5. **文档同步**：架构 §2.1 决策 4 / §2.2 第 4 条经验值「1.2× 上下 / 1.13–1.18×」改本机实测口径；README 四处
   `cython` 说明补「不宣称加速」并指向验收 §5.6；`.gitignore` 加 `build/`（Cython 编译中间产物，约 5.5 MB）。
6. **13 项性能快照重跑并回填**：`generated_at` = 2026-10-27T00:17:27+0800；判定分布 **2 快 + 5 持平 + 5 慢**
   （原 2 快 + 4 持平 + 6 慢；`optwide_missing` 1.12× → 1.00× 跨回持平）。验收 §5.1/§5.2/§5.3/§5.5/§0/§7.1/§8 同步。
7. **测试计数口径**：沙箱「未跟踪证据清单检查」在干净树跳过、有待入库文件时才执行 → 稳定口径沙箱 **1415 passed / 10 skipped**（有待入库证据文件时 1416/9）
   （total 1425；正常机 1420 passed / 5 skipped）。`build/` 已入 `.gitignore`。
8. **Cython 纳入发布门禁（用户 m04645，15 → 17 项）**：`evidence/accept.py` 新增
   `cython: product is pure source + cdef only`（剥 `cdef` 行后必须与纯 Python 产物逐字节相同，**不需编译器**）与
   `cython: bench_cython.py smoke (compile + eq)`（有 gcc 时真编译两个 `.pyx` 比对输出，并校验冻结证据 JSON 的 13 项
   `eq=True` 与中位数区间）；无 C 编译器/只有 MSVC 时后者 **SKIP 不阻塞**（唯一 `ACCEPTABLE_SKIPS`），
   证据被篡改则 **FAIL 并指名用例**——三条路径均以探针反证。`tests/test_docs_consistency.py` 加防漂移断言。

### 验证记录

- 文档守护（`test_doc_products` / `test_docs_consistency` / `test_doc_structure` / `test_doc_error_messages` /
  `test_sync_contract`）= **95 passed / 1 skipped**（未跟踪清单检查跳过）
- 全量 `python -X utf8 -m pytest -q` = **1415 passed / 10 skipped / 0 failed（1425）** exit 0（日志 `_tmp/pytest_round17c.log`）
- `python -X utf8 evidence/accept.py` = **17 PASS / 0 FAIL / 0 SKIP**（新增两条 Cython 门禁；无 C 编译器时第 17 项 SKIP 且不阻塞，三路已反证）
- `evidence/gen_doc_matrix.py --write` 后 `evidence/doc_vs_impl_matrix.md` 72 行，与各文档逐值一致

## 归档提交（最近在前）

| 提交 | 内容 |
|---|---|
| （本轮待提交） | 第十六轮增补：Cython 纳入验收门禁（GATES 15 → 17，无编译器 SKIP 不阻塞） |
| `cbba51a` | 第十六轮：Cython 产物实测并结案 R4 + 13 项快照重跑回填 |
| `d480f83` | 第十五轮：接口 3.4 交叉引用验收 2.2 动态下标四形态 |
| `2249970` | 第十五轮：路径下标口径与引用纠偏（7 项） |
| `e814346` | 第十四轮：`order_pipeline` 纳入协议 + 连跑 3 次取中位数 |
| `84c8964` | 第十三轮：重跑 12 项性能快照 |
| `ef517e9` | 第十二轮：24 项一致性 |
| `4b0bdba` | 第十一轮：26 项一致性 |
| `d02e4bf` | 第十轮：去行号/去尺寸 + 尺寸自述反向守护 |

## 下一步

- 第十六轮增补待提交：`git add -A && git commit`（信息见验收 §7.2）
- 之后无未决事项；性能快照为 2026-10-27T00:17:27+0800 口径，如需更新重跑 `evidence/bench_run.py`
- 输出 📊 汇总：完成项 / 未完成项 / 验证 / 风险 / 待决策

## 踩过的坑（长期有效）

1. **`generate(validate(raw))` 会报错**：`validate()` 返回规范形（钩子二元组、路径 tuple），`generate()` 入口已 fail-fast
   拒绝（第九轮裁决 B）。原始 schema 直接传 `generate()`/`build()`。
2. **PowerShell 内联 `python -c` 会吞反引号/`$`**：一律写 `_tmp/fix_plan/*.py` 脚本再跑。
3. **行数统计**：`Get-Content | Measure-Object -Line` 不准；用 Python `splitlines()` 或 `gen_doc_matrix.py` 口径。
4. **换行口径**：README / 架构 / 验收 / `perf_protocol.json` 是 CRLF；`PROGRESS.md`、`.gitignore` 是 LF。
   编辑脚本必须 `open(..., newline="")` 读写，多行插入用对应换行符拼接，否则整文件换行被改写。
5. **重定向陷阱**：PowerShell `>` 写 UTF-16 BOM；取 HEAD 内容用 `subprocess` 读 bytes 再解码。
6. **沙箱**：不可写系统临时目录（Cython 编译目录必须放工作区）；文件沙箱剥离目录列举权限会导致 5 条 tmpdir 探测跳过。
7. **测试计数**：不要给 `test_sync_contract` 的 parametrize 加用例（会改变 collected 数 1425）；改 `.gitignore` 会改变
   沙箱 skipped 数（干净树检查），须同步本文与验收 §1.2/§3.1/§7.1 R7。
8. **性能数字**：单轮比值抖动 ±5–10%，只认「连跑 3 次比值中位数」；`n=92307`（`order_pipeline` 过滤后行数）不是 100000。
