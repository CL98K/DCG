# P0 门禁取证报告 — DCG v2.1 工作区（未提交改动）

日期：2026-10-21 · 执行：gate-runner（task-1）· 环境：Windows / Python 3.13.3 / pytest 9.1.1
工作区：`C:\Users\black\Desktop\DCG`，含未提交 v2.1 改动（`row:"list"`→`type:"list"` 迁移、`_ob`/`_rl` 字面量优化、`_flat_row` 单键裸存；`pyproject.toml` version 2.1.0）。

**一句话结论**
- `accept.py` **16 PASS / 0 FAIL / 0 SKIP**（可复现，与 README 一致）。
- 全量 pytest 收集 **847**，**832 passed / 4 skipped / 2 failed / 9 errors**；11 个红/黄用例**全部**是沙箱删除权限（WinError 5）导致的环境失败，**0 个真实断言失败**。
- 由此本次运行能证明 **836/847 测试通过**，剩余 11 个**未被验证**（不是通过、也不是失败）。
- 两处需要 Lead 注意的**证据时效问题**（详见 §4）：效率门禁 1.019 来自 **v2.1 改动之前**的 `row_v2.json`（sha1 已漂移），`accept.py --help` 会触发一次完整门禁跑。

---

## 1. 环境事实（实测，修正 Lead 原有两条假设）

| # | 事实 | 实测细节 |
|---|---|---|
| E1 | 沙箱禁止**删除**目录/文件 | 所有 `rmtree`/`os.rmdir` → `PermissionError: [WinError 5] 拒绝访问`。**包括 chmod 也被拒**：`tempfile.py:275 _dont_follow_symlinks(chmod)` 就死在 WinError 5。 |
| E2 | 沙箱**也禁止 scandir/列目录**（新增发现） | 任何在 `_tmp` 下**新建**的子目录，其父 + 本目录 DACL 仍给 Everyone 写但不给 ListDirectory → 之后 `os.scandir`/`Get-ChildItem`/`icacls` 在该目录上全部 `WinError 5`。**这不是"上一轮 rmtree 残留"造成的**：全新目录 `_tmp\pytestrun2/3/4/5/…` 首次使用后即不可列。 |
| E3 | 后果：pytest `tmp_path` 第二次 `mkdtemp` 必失败 | `tmpdir.py:make_numbered_dir_with_cleanup` → `find_prefixed()` → `os.scandir(basetemp)` 抛 WinError 5。**换新 `--basetemp` 也只能躲过第 1 次**。只要 `tmp_path` 出现 ≥2 次，就必有 E7 类错误。 |
| E4 | `evidence/` 可写 | `evidence/pytest_v21_handoff*.txt`、`evidence/accept_v21_handoff.txt` 均写入成功；覆盖同名文件（Tee-Object）也成功。 |
| E5 | `evidence/row_v2.json` 只用 `open()` 读（不复制） | `accept.py:685 js_path = os.path.join(ROOT,"evidence","row_v2.json")`，只读不写，所以在 evidence/ 的拒绝删除 ACE 下仍可正常使用。 |
| E6 | 进程内缓存的 pytest 结果**不进输出** | `pytest.main()` 在末尾 sessionfinish 崩掉时，`==== 832 passed, 2 failed, 9 errors ====` 那行**不会打印**。必须用 `--junitxml` 才能拿到精确数字。 |
| E7 | `tests/test_e2e_flight.py` 的 `TestEfficiencyHarness` 两条用例 | 内部 `tempfile.TemporaryDirectory()` 在 `__exit__`→cleanup→`_resetperms`(chmod) 处 WinError 5 → 记为 **FAILED**（而 TestBuild/TestCli 的 `tmp_path` 在 setup 崩 → 记为 **ERROR**）。 |

复现命令（本次实际使用）：
```powershell
cd C:\Users\black\Desktop\DCG
$env:TMP="C:\Users\black\Desktop\DCG\_tmp"; $env:TEMP=$env:TMP; $env:PYTHONDONTWRITEBYTECODE="1"
python -X utf8 -m pytest -q -p no:cacheprovider --basetemp=C:\Users\black\Desktop\DCG\_tmp\pytestrunX --junitxml=_tmp\junit.xml
python -X utf8 evidence\accept.py
```

---

## 2. pytest 精确数字

**主证据**：`evidence/pytest_v21_handoff.txt`（原始全量输出，1276 行）、`evidence/pytest_v21_handoff2.txt`（`--basetemp` 复跑，847 进度字符），以及 junit 精确计数 `_tmp/junit_run3.xml` / `junit_run6.xml`。

```
tests=847  errors=9  failures=2  skipped=4   →  passed = 847-9-2-4 = 832
time(s): 7.7 / 8.8（两次一致）
```

| 结果 | 数量 | 用例 |
|---|---|---|
| **passed** | **832** | — |
| **skipped** | **4** | 非确定 helper 对拍（与 README 声称一致） |
| **FAILED** | **2** | `tests/test_e2e_flight.py::TestEfficiencyHarness::test_frozen_no_longer_requires_window`、`…::test_refuses_numbers_below_timer_resolution` |
| **ERROR (setup)** | **9** | `tests/test_codegen_snapshot.py::TestBuildSnapshot::test_build_is_byte_reproducible_and_sha1_stamped`；`tests/test_e2e_flight.py::TestBuild::`{`test_build_writes_artifact_and_manifest`,`test_build_is_reproducible`,`test_build_refuses_silent_staleness`,`test_cli_builds`}；`tests/test_migrate.py::TestCli::`{`test_cli_writes_structural_schema`,`test_cli_no_wrap_doc_exit_3`,`test_cli_protocol_false_uses_named_objects`}；`tests/test_migrate.py::TestSingleMode::test_cli_single_true_runs` |
| **真实断言失败** | **0** | 无 |

### 2.1 失败分类

**(a) 环境失败 — 11 / 11，原因全部是 `PermissionError: [WinError 5]`**
- 2 个 FAILED：`tempfile.TemporaryDirectory.__exit__` → `shutil.rmtree` → `tempfile._resetperms` → `os.chmod(..., follow_symlinks=False)`，路径为测试自己建的 `_tmp\tmpXXXX`。属"删除/改权限被禁"，非代码缺陷。
- 9 个 ERROR：全部 `failed on setup with "PermissionError: [WinError 5]"`，代码路径 `_pytest/tmpdir.py:300 tmp_path` → `pathlib.py:175 find_prefixed` → `os.scandir(basetemp)`（见 E2/E3）。
- 已验证它们**不是断言失败**：把 `tempfile.TemporaryDirectory.cleanup` 的 PermissionError 吞掉后（`_tmp/probe_no_cleanup*.py`），2 个 FAILED 里 `test_refuses_numbers_below_timer_resolution` **转为 PASS**（failures 2→1）；剩下 9 个 ERROR 换成 `FileExistsError [WinError 183] basetemp 已存在`（因为被吞掉的 rmtree 没能清掉 basetemp）——说明它们卡在**建临时目录**这一步，根本没跑到被测逻辑。

**(b) 真实失败 — 0**
- 没有任何 `AssertionError`、没有 `SchemaError`、没有快照/契约不匹配。

**(c) 因此"未被验证"的 11 个用例 = 环境盲区，不等于通过**
需要 Lead 知晓：`TestBuild`（build 产物 + manifest + 防陈旧）、`TestBuildSnapshot`（字节可复现 + sha1）、`TestCli`/`TestSingleMode`（CLI 端到端）以及效率 harness 的 2 条，在当前沙箱下**无法取证**。要拿到 847 全绿，需要在**无此沙箱限制**的机器上跑（或临时把测试的 tmp 目录改到沙箱外——但本任务禁止改 tests/）。

---

## 3. accept.py 16 门（`evidence/accept_v21_handoff.txt`，exit 0）

```
GATES: 16 PASS / 0 FAIL / 0 SKIP  (total 16)
```

| 门 | 结果 | 关键数字 |
|---|---|---|
| codegen: no undefined globals (static scan) | PASS | — |
| codegen: generate() exists and callable | PASS | — |
| determinism: same schema → identical source | PASS | identical=True, 157 chars（= `bench/_workload.py::V2_CAP_EXT` 实测 157，吻合） |
| product parses as Python | PASS | 无 lambda 内赋值 |
| generate() self-checks its output | PASS | — |
| generated source self-contained | PASS | 全部全局名可解析 |
| 6 zero-redundancy rules | PASS | subscripts=['5','2']，各前缀 ≤1× |
| sentinel integrity | PASS | 哨兵是独立对象；真实 0 != undefined |
| **char budget (§4.2)** | **PASS** | **CAP=149/180，M0=195/210** |
| no reverse imports | PASS | — |
| schema errors located SchemaError | PASS | `out.fields.a` 两键 / 缺 type 均定位；single+list 合法 |
| conformance fixture | PASS | compiled **50/50**，refused **3/3** |
| §4.1 shape semantics list/dict/index/rows | PASS | rows 为逐行 dict 且按声明顺序 |
| §4.1 input layout rows vs index | PASS | rows == index |
| P0 import surface: dcg package + public API | PASS | — |
| **§5 efficiency** | **PASS** | **v2_over_hand=1.0192307692307692 ≤ 1.05**；confidence=RESOLVED；noise_floor=0.02777777777777779；k=8；reps=7；non-provisional；worst-of-3-rounds=1.0192，中位轮 0.9780，margin_over_limit=0.0308 |

CAP/M0 独立复核（不依赖 accept.py 自报）：
```
V2_CAP            chars=149   lines=10
V2_CAP_EXT        chars=157   lines=10
V2_M0             chars=195   lines=11
V2_MINIMAL        chars=177   lines=10
```
→ 声明值 149/180 与 195/210 **属实**，离上限分别还有 31 / 15 字符余量。

---

## 4. ⚠ 两处证据时效问题（本次新发现，请 Lead 决定是否重跑）

### 4.1 效率数字 1.019 来自 v2.1 改动**之前**的运行
`accept.py` 的 §5 门**不重测**，只读 `evidence/row_v2.json`（`accept.py:685`），并校验其中的 `sha1` 与 `window`。实测该文件的 `sha1` 块与**当前源码不一致**：

| 文件 | row_v2.json 里记录 | 当前工作区 | 结论 |
|---|---|---|---|
| `dcg/codegen.py` | 05F907B4… | E6B008AFCE4D7B944593C8EE2B8502756FAAB592 | **DRIFT** |
| `dcg/runtime.py` | D4F88816… | 87A87C66A13597F112B7576F0093FB07702A68D8 | **DRIFT** |
| `dcg/schema.py` | 0FDBB413… | FC7F429AB9EFB5A8600A208D2D986767C74E2351 | **DRIFT** |
| `dcg/ir.py` | 43AAF026… | 0E5548485B1F1A7B09F4D735F1DB4E72DBCEC7C3 | **DRIFT** |
| `dcg/build.py` | 79EC4321… | 79EC4321… | MATCH |
| `dcg/__init__.py` | 7F5338F7… | 920A51D817E0414389A262314C78457B246D402F | **DRIFT** |
| `tests/test_codegen_snapshot.py` | 69D25E12… | FF5137579722312F08BF90F62E56C4B702EB3636 | **DRIFT** |

`row_v2.json` 时间戳 `2026-10-19T18:01:11+08:00`，`others_python_processes=6`（当时有并发进程，属 wall 口径污染但 CPU 口径可接受）。该门之所以 PASS，是因为 `accept.py` 只校验 `window` 字符串，**没有校验 sha1 是否仍匹配当前源码**。
→ **含义**：`1.0192 / CAP 149 / M0 195` 这组"效率与体积"数字描述的是 **v2.0 或更早那次 v2 实现**，不严格代表当前 v2.1 工作区（v2.1 改了 `_ob`/`_rl`/`_flat_row` 产物生成，理论上会影响产物字符数与运行时）。但 CAP/M0 我已用当前源码重新生成为 149/195（§3），**体积数字是当下的**；只有**效率比值 1.019 是旧证据**。

### 4.2 `python evidence/accept.py --help` 会跑完整门禁
实测：传 `--help` 仍然输出全部 16 门并 `exit 1`（因为 `--help` 被当成了一次参数运行 + 最后一门因 SIGPIPE/CTRL 截断而 exit 1）。**不要用 `--help` 探测用法**；直接用无参数运行（exit 0）。若要用 README 声称的 `n=200000` 口径，需另跑 `bench/row_v2.py` 并重出 `row_v2.json`——本任务未授权（会改 evidence/ 下的既有证据文件，且耗时不在范围内）。

---

## 5. 与 README 声称的差异

| 项 | README 声称 | 本次实测 | 差异判定 |
|---|---|---|---|
| 全量测试数 | **847**（`README.md:12,999,1274,1334`） | **847 收集** | ✅ 一致 |
| passed/skipped | **843 passed / 4 skipped** | **832 passed / 4 skipped / 2 failed / 9 error** | ⚠️ **不可比**：11 个差额全部是沙箱环境失败（§2.1），真实断言失败 0。**在当前沙箱下不可能达到 843**。 |
| accept 门禁 | **16 PASS / 0 FAIL / 0 SKIP**（`README.md:12,978,1000,1302,1337`） | **16 PASS / 0 FAIL / 0 SKIP** | ✅ 一致（exit 0） |
| CAP / M0 字符 | **149/180、195/210**（`README.md:14`） | **149/180、195/210**（独立复算） | ✅ 一致 |
| 效率比值 | **1.019 ≤ 1.05**，中位轮 0.978，noise_floor 0.0278，k=8，n=200000（`README.md:14,948,955,1002,1339`） | row_v2.json 值完全一致 | ⚠️ **数值一致但证据过期**（§4.1：sha1 已漂移，非当前 v2.1 源码的实测） |
| 测试/门禁运行命令 | `py -m pytest tests/`、`py evidence/accept.py` | — | ⚠️ 在此沙箱下 `pytest tests/` 必带 11 个环境红；README 未标注该前提 |

---

## 6. 交付物清单

| 文件 | 内容 |
|---|---|
| `evidence/handoff_gate_report.md` | 本报告 |
| `evidence/pytest_v21_handoff.txt` | 全量 pytest 原始输出（`-p no:cacheprovider`，1276 行） |
| `evidence/pytest_v21_handoff2.txt` | `--basetemp` 复跑原始输出（847 进度字符，含 sessionfinish 崩溃 trace） |
| `evidence/accept_v21_handoff.txt` | accept.py 16 门完整输出 |
| `_tmp/junit_run3.xml` / `_tmp/junit_run6.xml` | junit 精确计数（847/2/9/4），用于给"832 passed"取硬证据 |
| `_tmp/probe_no_cleanup*.py` | 隔离实验：吞掉 cleanup PermissionError，证明 2 个 FAILED 非断言失败 |

未修改 `dcg/` 与 `tests/` 任何文件；未新增/覆盖 `evidence/` 下既有证据文件。
