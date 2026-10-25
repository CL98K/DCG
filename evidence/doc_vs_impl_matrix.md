# 文档↔实现一致性核实矩阵

> 核实对象：`DCG 接口实现细节文档.md`（732 行）、`DCG 整体设计方案与架构文档.md`（774 行）、`JSONata 语法.md`（591 行）、`README.md`（2402 行）
> 核实基准：**工作区当前内容**（`dcg/` 全部源码 + `tests/`，含未提交修改）
> 核实方式：逐段 `read` + `grep` 定位到行；再用**只读探针**（`python -c` / 小脚本，只 `import dcg`、只 `dcg.generate()` 后 `exec`）实跑文档声明。**未运行 pytest、未运行 examples 目录下的脚本。**
> 每条结论都带 `file:line` 或文档行号；`B 类`（承诺但不存在）另附「grep 了什么、没找到什么」。

---

## 0. 方法与范围

### 0.1 读完了什么

| 文档 | 段落范围 | 行数(实测) |
|---|---|---|
| `DCG 接口实现细节文档.md` | §0 全览、§1 IR 41 符号表、§2.1–§2.6 schema 语义、§3.1–§3.10 表达式层、§4.1–§4.5 代码生成、§5 公开 API、§6–§7 边界与错误 | 732 |
| `DCG 整体设计方案与架构文档.md` | §0–§2 定位与事实、§3 架构、§4 schema、§5 IR、§6 表达式层、§7 代码生成、§8 输出形态、§9 API、§10 决策、§11 边界 | 774 |
| `JSONata 语法.md` | §1–§15 全部（含 §13 扩展规范、§14 覆盖统计、§15 缺陷史） | 591 |
| `README.md` | §1–§5 全部行为段 + 附录 A.8 手册 + B 门禁/测试段 | 2402 |

> 任务书里给的 429/485/462/1817 行是旧值，**工作区当前是 732/774/591/2402 行**；本报告所有行号以当前工作区为准。

源码全量核实：`dcg/__init__.py`、`ir.py`、`schema.py`、`runtime.py`（1055 行）、`codegen.py`（2601 行）、`build.py`、`jx/{lexer,parser,compiler,policy,paths,registry,__init__}.py`。

### 0.2 现有门禁 `tests/test_docs_consistency.py` 覆盖了什么

实测该文件**现为 165 行 / 16 个 `def test_`**（任务书写的「147 行/14 个」已过期）。它只钉三类东西：

1. **数字**：`__version__`==pyproject 版本（:43）、`HELPERS`==84 / `__all__`==94（:98）、`ROW_FUNCS` 46 / `HIGHER_ORDER` 6 / `NONDETERMINISTIC` 4 / `STRUCTURAL` 10（:111）、三表并集 55 且 `$sort` 同时在 A/B（:119/:131）、conformance 50 例（26+24）（:162）。
2. **少量字符串**：README 含「55 个函数」、语法文档含「共 55 个」（:131）；语法文档 §15.1 含「当前为空」（:142）；README 提到每个 manifest 键（:180）。
3. **少量运行期语义**：`_ix_get(None,0) is None`、`_ro(2.5)==3`、`_ct('abc')==1`、`_sp(...)`（:150）；产物含 `_c=_o.append`、不含 `if R:`（:195）。

**它漏掉的东西（正是本次审计发现的全部 P0/P1）：**

- **不校验任何「产物形态」声明**：文档里成百上千条「内联（裸代码）/ helper（`_xx`）」标注、`$.name` 到底发什么、CAP/M0 样例长什么样，一个都没测。
- **不校验任何文档代码样例可执行**：三份文档里的 CAP/M0 产物样例、schema 样例、报错样例，没有任何测试把它们拿去 `generate()` 或 `pytest.raises(match=...)`。
- **门禁自己就有过期断言**：`:105 test_runtime_needs_math_matches_contract` 断言 `{"_fl","_ce","_sq"}`，实测现值确实是这三个（`dcg/runtime.py:1035-1036` 已修好派生式），**该测试现在是对的**；但它是唯一盯着这条的测试，而文档侧的 `import math as _m` 措辞无人管。
- **唯一的强文档门是语法文档的字节门**：`tests/test_jsonata_manual.py:32 test_doc_is_byte_identical_to_renderer` 要求 `JSONata 语法.md` 与 `tests/gen_jsonata_manual.py::render()` **逐字节相同** → 语法文档是**生成物、不能手改**；反过来 `接口文档` 与 `架构文档` 是手写的、**没有任何逐字校验**，这正是它们漂移最严重的结构性原因。
- `evidence/accept.py::g_docs_consistency`（:663）只做 4 件事：文档里必须出现 `{N} 个函数`/`A 类 {N}`/`B 类 {N}`/`C 类 {N}`/`{N} 个 helper`/`__all__ 导出 N 个名字`/`conformance N 例 core N edge N`，再加产物形态字符串。**同样不校验语义与样例。**

### 0.3 严重度定义

- **P0**：文档承诺了用户会直接依赖的行为，实机**失败**（照文档写会出错），或文档/实现里存在会误导架构决策的矛盾。
- **P1**：文档与实机不一致、或文档整块缺失一个已上线的用户可见特性，照文档做会得到不同结果。**必修。**
- **P2**：措辞/样例/数字过期，不影响正确性但损害可信度。

---

## A. 已实现（文档声明 → 源码位置 → 证据要点）

> 「测试守护」= 有自动化断言盯着这条契约的 `file:line`，或「无」。

| 编号 | 文档出处(文件:行) | 声明内容 | 源码位置(file:line) | 实现要点 | 测试守护 |
|---|---|---|---|---|---|
| A-01 | 接口文档:633-640 | 顶层只导出 8 个符号 | `dcg/__init__.py:22-31` | 实测 `__all__` = validate/SchemaError/generate/build/gen_name/compile_expr/Unsupported/Registry | `tests/test_docs_consistency.py:43` 只测版本 |
| A-02 | 接口文档:653；架构文档:719 | `__version__="2.1.0"` 不是第 9 个契约符号 | `dcg/__init__.py:36` | 实测 2.1.0，与 `pyproject.toml` 一致 | `tests/test_docs_consistency.py:43` |
| A-03 | 接口文档:17；架构文档:407 | `ir.py` 41 个符号（16 节点+3 集合+16 构造器+6 工具） | `dcg/ir.py` `__all__` | 实测 `len(ir.__all__)==41`，16 构造器/KINDS 16/UNARY_OPS 3/BIN_OPS 15 | 无 |
| A-04 | 接口文档:245；架构文档:725 | 表达式层次级公开面 21 个（逐名列全） | `dcg/jx/__init__.py` | 实测 `len(jx.__all__)==21`，21 个名字逐一存在 | 无 |
| A-05 | 接口文档:285-292；架构文档:452 | A 类 46 / B 类 6 / C 类 4，逐名一致 | `dcg/jx/policy.py:63-80/85/93` | 实测 frozenset 大小 46/6/4，逐名与文档一致 | `test_docs_consistency.py:111` |
| A-06 | 接口文档:283；语法文档:240 | 三表并集去重 55（`$sort` 同属 A/B） | `dcg/jx/policy.py:107` | 并集 55，重叠仅 `$sort` | `test_docs_consistency.py:131` |
| A-07 | 接口文档:277 | `BARE_CALL_ALIASES` 6 个：str/int/float/bool/len/abs | `dcg/jx/policy.py:96` | 实测映射 `{str:$string,int:$number,float:$number,bool:$boolean,len:$length,abs:$abs}`；`max($.a)` → `Unsupported: max() 不可逐行编译（只有 $ 函数可以被调用）` | 无（仅计数） |
| A-08 | 接口文档:548 | 84 个 helper、`__all__` 94 名 | `dcg/runtime.py:970-991, 1051-1055` | 实测 `len(HELPERS)==84`、`len(SOURCE)==84`、`len(__all__)==94` | `test_docs_consistency.py:98` |
| A-09 | 接口文档:550-562 | helper 完整名单（逐名） | `dcg/runtime.py:970-991` | 文档列出 84 个 + `_U`/`_cmp_ok` 两个非 helper，与 HELPERS 键集**完全一致**（逐名核对无缺无多） | 无 |
| A-10 | 接口文档:567；架构文档:620 | `SOURCE_NEEDS_MATH` 本快照 = `{"_fl","_ce","_sq"}` | `dcg/runtime.py:1035-1036` | 实测恰为这三个；派生式已修成 `"math" in t or "_M." in t`，另减 `{"_pw"}`（`$power` 内联发 `a**b`） | `test_docs_consistency.py:105` + `tests/test_codegen_snapshot.py:89`（独立派生式复核，最权威） |
| A-11 | 接口文档:570-577；语法文档:469-473 | 六条 undefined 语义 | `dcg/runtime.py:993-1000` | `UNDEFINED_SEMANTICS` 实测 6 元 tuple，逐条文本与文档一致 | `test_docs_consistency.py:150`（部分） |
| A-12 | 接口文档:417-420 | 四种 shape 的 `run(rows)` 返回 | `dcg/codegen.py:2415-2423` | 实跑 `[[1,2],[3,4]]`：list→`[[1,2],[3,4]]`、rows→`[{'a':1,'b':2},{...}]`、dict→`{'a':[1,3],'b':[2,4]}`、index→`{0:[1,2],1:[3,4]}`，**逐字一致** | `tests/test_codegen_snapshot.py:407/413/422` |
| A-13 | 接口文档:443-447；架构文档:678 | before/after 三种写法等价（表达式 / py{} / @函数） | `dcg/codegen.py:2119-2144`、hooks 发射段 | 实测：`= $` → `_d=(lambda D:D)(_d)`；py{} → `_d=(lambda D:_py0(D))(_d)`；`@json.dumps` → `_hq0/_hp0/_h0` 三件套 | `tests/test_readme_hooks.py:35/43/55/108/118` |
| A-14 | 接口文档:454；架构文档:387(接口:387) | `trace=true` 的正式产物与 `trace=false` 逐字节相同 | `dcg/codegen.py:2529-2535` | 实测两产物 `a == b` 为 True；trace 只在 `build()` 写 `<name>.trace.py` | `tests/test_codegen_snapshot.py:581` |
| A-15 | 接口文档:456-466 | `aggregate` 列容器 + 递归 + 父列 `_a`/`_a2` 短名 | `dcg/schema.py:22-32,195-203`；`dcg/codegen.py:1841(_aggregate_direct)/1950(_aggregate_plan)/2404` | 实测两层 aggregate 产物含 `_a=[r[1]for r in _o]`；`aggregate` 只能配 `object`+`fields`，否则 `SchemaError` | `tests/test_codegen_snapshot.py:431-557`（9 个用例，覆盖展开/嵌套/共存/短名提取/三层） |
| A-16 | 接口文档:430-435；架构文档:605-610 | 四种 shape 只差最后一行 `return` | `dcg/codegen.py:2415-2423` | 实测 `list`→`return _o`、`index`→`{i:r for i,r in enumerate(_o)}`、多字段 rows/dict 形态一致 | `tests/test_codegen_snapshot.py:407-428` |
| A-17 | 接口文档:485-494 | 九条形态规则 | `dcg/codegen.py` 全文 | 逐条实跑：规则1 前缀记忆化 ✓；规则2 `_o=[]`/`_c=_o.append` 无条件 ✓；规则5 产物**无** `isinstance`/`_num(`/`_cmp_ok(` ✓；规则7 optional 守卫恰好一次、`inline_optional=False` 改 `is_u()` ✓；规则9 cython 只多 `cdef object` 行 ✓；规则6 `compile` 0 次出现在产物 ✓ | `tests/test_codegen_snapshot.py:209-305`（规则 1–9 各一条） |
| A-18 | 接口文档:506 | `inline_optional` 两形态 | `dcg/codegen.py` `_u_guard` | 实测 True→`None if _t1 is _U else _t1`；False→`None if is_u(_t1) else _t1` | 无 |
| A-19 | 接口文档:501-505 | rfunc `_x` 惰性分派器形态 | `dcg/codegen.py:2106-2113` | 实测 `_xr={}`/`def _x(q,*a):`/`f=_xr.get(q)`/`if f is None:`/`s=q.rfind('.');f=_xr[q]=getattr(__import__(q[:s]),q[s+1:])`/`return f(*a)`，与文档逐字一致 | `tests/test_jx_py.py`（部分） |
| A-20 | 接口文档:329-330；README:813 | 反引号段语法错误带段内偏移 | `dcg/jx/compiler.py` `analyze_py_segment` | 实测 `` `1 +` `` → `第 1 列: 反引号段 Python 语法错误 不可逐行编译（invalid syntax（段内偏移 0）—— 必须是单个 Python 表达式）`；`` `1;2` `` 偏移 2 | 无（README 文案未进测试） |
| A-21 | 接口文档:333；语法文档:428 | `PY_SAFE_MODULES`(25) 只约束裸模块名，产物自动补 import | `dcg/jx/compiler.py:56`；`dcg/codegen.py:2309-2312` | 实测 25 个名字；`` `json.dumps($.0)` `` → 产物首行 `import json`；`` `os.path.join` `` 被当裸名 → `SchemaError` | 无 |
| A-22 | 接口文档:334；语法文档:429 | `__jx` 保留前缀即拒 | `dcg/jx/compiler.py` `RESERVED_PREFIX` | 实测 `` `__jx_a` `` → `Unsupported: 第 1 列: 反引号段含保留前缀 '__jx'` | `tests/test_jx_py_block.py:74` |
| A-23 | 接口文档:339-345；语法文档:213 | py{} 块：`let`+末尾 `return`、局部名 `__jx*`、模块级 `_pyN`、裸 `$` 多接形参 | `dcg/jx/compiler.py` `compile_py_block`；`dcg/codegen.py` pyblock 发射 | 实测 `def _py0(_p0):` / `__jx_0_a = _p0 + 1` / `return __jx_0_a`；钩子 `_py0(D['rows'],D)`、行内 `_py0(R)`；`$$`/`import` 分别报带「契约 §3.7」的 Unsupported | `tests/test_jx_py_block.py`（13 个）+ `README` 钩子段 `tests/test_readme_hooks.py:71/88/138` |
| A-24 | 接口文档:348-356；语法文档:322-332 | 永久拒绝 11 类 | `dcg/jx/policy.py:107` + compiler 各点 | 实测 11 条全部抛 `Unsupported`，feature 名逐个对上（`区间 a..b`/`正则字面量`/`位置绑定 #`/`join 绑定 @`/`变换操作符 ~> / \|...\|`/`排序 ^`/`分组/聚合 {..}`/`$$ 文档根`/`$eval`） | `tests/test_jx_conformance.py`、`examples/jx/11_rejected.jx` |
| A-25 | 接口文档:358；语法文档:400/523 | `$x` 不是永久拒绝：jx 层通过、生成器层报 `SchemaError` | `dcg/jx/compiler.py`（`var` 节点）；`dcg/codegen.py` var 分支 | 实测 `compile_expr("$i + 1")` **通过**；`generate()` 报 `SchemaError: ... 行模式没有变量作用域...` | `examples/jx/05_paths.jx` gap 2 |
| A-26 | 接口文档:421-426；架构文档:679-680 | before/after 整批一次、在行循环前/组装后 | `dcg/codegen.py` `module()` | 实测 `_d=<before>(_d)` 在 `_o=[]` 之前；after 包在 `finish` 外 | `tests/test_readme_hooks.py:118` |
| A-27 | 接口文档:467 | shape 编译期固定、无运行期容器判断 | `dcg/codegen.py:2415-2423` | 实测产物无任何 shape 分支 | `tests/test_codegen_snapshot.py:252` |
| A-28 | 接口文档:394-403 | 生成器次级面与 CLI | `dcg/codegen.py` `gen_name/canonical_json/schema_sha1/load/_main`；`dcg/build.py` | 实测：`_main` 参数 `<schema>`/`-o --out`/`--overwrite`/`--stdout` 逐项一致；打印 name/schema_sha1/artifact/lines/chars | `tests/test_codegen_snapshot.py:593` |
| A-29 | 接口文档:380-392 | manifest 10 键 | `dcg/codegen.py:2536-2547` | 实测 `build()` 返回**恰 10 键且与落盘 `.manifest.json` 逐字节同源**；`generator_version="2.1.0.g1"` | `test_docs_consistency.py:180`（README 提到每个键） |
| A-30 | 接口文档:151-161 | `parse_value_path` 路径语法 6 行表 | `dcg/schema.py` | 实测 16 个正反例全部一致（`$`→`()`、`$.a.0.b`→`('a',0,'b')`、`$["x y"]`→`('x y',)`、`$.a..b`→`('a','b')`、`[1,2]`→SchemaError） | `tests/test_schema.py:106-141` |
| A-31 | 架构文档:462-495 | 函数字面量：最多 2 个位置参数、只能作高阶实参 | `dcg/jx/compiler.py`；`dcg/codegen.py:118-121` | 实测 3 个形参 → `Unsupported: 函数字面量参数过多（回调最多接收 2 个参数）`；直接当字段 → `SchemaError: 只能作为高阶函数的实参`；形参名**不被改写**（`function($item)`→`lambda item,_i1:`） | 无 |
| A-32 | 接口文档:319-323；语法文档:293-309 | C 类非确定函数可注入固定源 | `dcg/runtime.py:883-930(CLOCK/RANDOM)` | 实测 `rt.CLOCK`/`rt.RANDOM` 可整体替换；文档 FixedClock 需实现 `now_ms/random/shuffle` 三方法 | `tests/test_codegen_snapshot.py:135` 附近的非确定跳过项 |
| A-33 | 架构文档:637-653 | 三级类型证明表 | `dcg/codegen.py:808-858` | 实测 `1+2`→`(1+2)`、`hA+hB`（都声明 int64）→`(hA+hB)`、`$.a` 不可证→helper | `tests/test_codegen_snapshot.py`（内联相关） |
| A-34 | 接口文档:5.?/README:290-295 | optional × type 四形态 | `dcg/codegen.py` `field_value/get_text/safe_text` | 实测四形态全部与 README 表一致（`.get(k,_U)` vs `_ix_get`，各带一次守卫） | `README` 段 `tests/test_readme_complex.py`、`tests/test_codegen_snapshot.py:845-932`（5 个用例） |
| A-35 | 接口文档:648 | `gen_name(schema)->str` = `schema["name"]` | `dcg/codegen.py` | 实测 `gen_name` 对 `cap`/`cap-1`/`Cap_X` 均原样返回 | `tests/test_codegen_snapshot.py:593` |
| A-36 | 接口文档:365 | conformance 冻结集 50 例（core 26 + edge 24） | `tests/fixtures/conformance.json` | 实测 50 例，核心 26 + 边界 24 | `test_docs_consistency.py:162` |
| A-37 | 语法文档:500-517 | .jx 语料 15 个文件共 242 例（ok 207 / gap 2 / fixed 33） | `tests/jx_corpus.py:73-82`；`examples/jx/*.jx` | 实测 `all_samples()` 恰 242 条，state 分布完全对上 | `tests/test_jx_examples.py`、`test_jsonata_manual.py:72` |
| A-38 | 接口文档:709-711 | `Unsupported(feature, loc, msg)` 三属性 | `dcg/jx/policy.py:29-46` | 实测属性 `feature/loc/msg/col` 齐备 | 无 |
| A-39 | 架构文档:762-772 | 能力边界 10 行 | 多处 | 实测：未登记 `$xxx`→Unsupported（:394 一致）、反引号多语句→Unsupported、shape 运行期不可切、裸字符串非路径、未知键拒绝 —— 逐条对得上 | 分散 |
| A-40 | 架构文档:160 | 生成期报错带列号/JSON 路径 | `dcg/jx/policy.py:49-57`；`dcg/schema.py` | 实测 `第 N 列` / `out.fields.x.expr` 两种定位都在 | 无 |

**A 类共 40 条**（合并了同一契约在多处文档重复声明的条目）。核心结论：**引擎实现本身是完整且自洽的**——三张能力表、84 helper、9 条形态规则、四种 shape、钩子三写法、py{}/反引号段、aggregate、错误定位，全部有实现且被测试覆盖。问题不在引擎，在**文档描述层**。

---

## B. 未实现 / 缺失（文档承诺但源码无对应实现）

| 编号 | 文档出处(文件:行) | 承诺内容 | 源码检索证据（证明不存在） | 严重度 | 有无测试守护 | 建议 |
|---|---|---|---|---|---|---|
| B-01 | 接口文档:244；语法文档:404-419；README:681-695 | 注册进 `Registry` 的自定义函数可用于**字段表达式**（「不改变 schema 结构」「编译成 rfunc 节点」） | `grep -n "compile_expr(" dcg/codegen.py` → `dcg/codegen.py:747: node = self._exprs[text] = compile_expr(text)` —— **不传 `registry=`**；`generate()` 签名（:2447-2451）也没有 registry 参数。实测 schema 的 `expr` 写 `$bump($.0)` → `Unsupported: 第 6 列: 未知函数 $bump 不可逐行编译（不在任何能力表内）`。`grep -rn "registry" dcg/codegen.py` 只有这一处且是 `compile_expr(text)` 的默认 `None` | **P1** | 无 | 二选一：①给 `generate/build` 加 `registry=` 并穿到 `dcg/codegen.py:747`；②在接口 §3.1、语法 §13.2、README §4.2 明确写「Registry 只服务表达式层 `compile_expr`，**不能**注入 schema 产物」 |
| B-02 | 接口文档:648；架构文档:719；README:682 | 产物运行期「按 qualname 做 `import` + `getattr`」 | `dcg/codegen.py:2113`（`_x` 分派器与 `@fn` 钩子共用）都是 `s=q.rfind('.'); getattr(__import__(q[:s]), q[s+1:])` —— **同一套只切最后一个点**。实测 `json.dumps`→OK、`os.path.join`→OK（靠 import 副作用）、**`json.decoder.JSONDecoder.decode` → `ModuleNotFoundError: No module named 'json.decoder.JSONDecoder'; 'json.decoder' is not a package`**。而 `dcg/jx/registry.py:23 _IMPORTABLE_QUALNAME` 明确允许任意长度点分名（含 `Class.method`） | **P1** | 无 | 改为迭代「最长可导入前缀 + getattr 走剩余属性链」（`importlib.import_module` 逐段回退）；或在三份文档 + README 写明「只能写 `模块.顶层函数`，不支持类方法与嵌套属性」 |
| B-03 | 语法文档:570 | 「`$.m[0]` 已修复：`_ix_get` 的 TypeError 分支对标量返回自身，`null[0] == null`」 | helper 层确实修了（`dcg/runtime.py` `_ix_get`），但**生成器把它内联掉了**：`dcg/codegen.py:587-597` 的 INDEX 常量链在 `self.safe=False` 时 `return self.emit_path(path)` → 裸 `_m[0]`。实测 `{'m': None}` → **`TypeError: 'NoneType' object is not subscriptable`**；缺键（非 optional）→ `TypeError: '_Undef' object is not subscriptable`；`type:"list"` 同样抛 | **P1** | 无（`test_sentinel_integrity.py` 只测 `_ix_get` helper 本身，不测生成器内联结果） | 改文档（把 §15.2 该行改为已知限制，或调到 §15.1「未修缺陷」）；若要兑现承诺，需让常量下标链在 `safe`/非 safe 两种模式下都对 `None`/`_U` 做守卫 |
| B-04 | 接口文档:537 | 「内联只改产物形态、**不改 undefined 语义**」 | 实测反例：`n` 声明 `float64`、数据给 `'abc'` → `$floor(n)` 内联成 `math.floor(n)` → `TypeError: must be real number, not str`；而 `type:"any"` 时走 `_fl(n)` → 返回 `undefined`。同类：`n+1`（int64、数据 `'5'`）→ `TypeError: can only concatenate str (not "int") to str` | **P1** | 无 | 改文档：明确「内联后运算符/内建函数对**违反声明**的数据会抛 Python 异常，不返回 undefined；要 undefined 出口请声明 `optional`/`any` 或显式写 helper 路径」；接口 §4.3 L537 与架构 §7.5 L655-659 的 EAFP 段同步修 |
| B-05 | 接口文档:274；接口文档:678；README:1296；语法文档:76-77 | 「路径长度不限」「任意深度」「数值段与符号段可混用」 | 实测 `$.5.a.1.b` ✓、`$.a.b.c.d` ✓；但**连续数字段** `$.a.1.2` → `Unsupported: 第 2 列: 路径段非法 1.2 不可逐行编译`。根因 `dcg/jx/lexer.py:56` 的 num 正则 `\d+(?:\.\d+)?...` 把 `1.2` 吞成一个小数：`lex("$.a.1.2")` → `[...,('num','1.2',5),('eof','',8)]`。可用写法只有 `$.a[1][2]` | P2 | 无 | 文档补一句「连续数字段必须写成 `$.a[1][2]`，`$.a.1.2` 会被词法层当成小数 1.2 拒绝」；或修 lexer 让路径上下文里的数字不吃小数点 |
| B-06 | 架构文档:419-421、:462-484 反复出现 | 「`$$` 在 jx 层拒绝」「`$x` 在生成器层拒绝」等 11 类永久拒绝 | 全部**存在**（实测逐条抛对）。**本条不成立，保留为交叉验证记录**：架构 §6.4、语法 §12.5/§13.1、接口 §3.9 三处口径一致，无缺失 | — | `test_jx_conformance.py`、`11_rejected.jx` | 无需动作（列出以证明「不是所有承诺都错」） |

**B 类真正成立的 5 条：B-01 ~ B-05，其中 P1 四条、P2 一条。**

---

## C. 文档缺失（源码有实现但文档未写，尤其用户可见行为）

| 编号 | 源码位置(file:line) | 实现内容 | 影响的用户可见行为 | 有无测试守护 | 建议补写位置 |
|---|---|---|---|---|---|
| C-01 | `dcg/schema.py:22-32`；`dcg/codegen.py:1841/1950/2404` | `aggregate: true` 列容器特性（递归展开 + 父列 `_a`/`_a2` 短名） | 决定 `shape:"dict"` 下嵌套 object 是「列」还是「具名行列表」，直接改变输出 JSON 结构 | `tests/test_codegen_snapshot.py:431-557`（9 例） | **架构文档 §7.3 / §8 缺这张表**（`grep -c aggregate DCG 整体设计方案与架构文档.md` = **0**）；接口 §4.1、README 已有 |
| C-02 | `dcg/schema.py:195-203` | `aggregate` 在非 `dict` shape 下**静默忽略**（`shape:"list"` + `aggregate:true` 正常生成 `return _o`，无报错） | 用户标了 aggregate 却得不到列式输出，且无任何提示 | 无 | 接口文档 §4.1 L456 补一句「其它 shape 下忽略该键」；或让它报 SchemaError |
| C-03 | `dcg/codegen.py:2313-2314` | 内联 `math.*` 时产物**首行插 `import math`**（不是 `import math as _m`） | 产物头部形态；`$floor/$ceil/$sqrt` 内联后能跑 | `tests/test_e2e_flight.py:1137`（只测 `startswith("import math\n")`） | 接口 §4.4 L567 与架构 §7.4 L620 的 `as _m` 措辞统一成 `import math`（见 D-05） |
| C-04 | `dcg/runtime.py:1044` | `SOURCE_PRELUDE = ""`（空字符串，已废弃的哨兵常量） | 无运行期影响，但它是 `__all__` 的 94 名之一，读者会以为有前导常量 | 无 | 接口 §4.4 名单里注明「`SOURCE_PRELUDE` 已空置，仅为兼容保留」 |
| C-05 | `dcg/codegen.py:2536-2547` | `build()` 的 manifest `"cython": False` **是硬编码**，`build()` 没有 cython 参数 | 用户以为 `build()` 能产 Cython 版本，实际 manifest 永远是 False | 无 | 接口 §4.1 manifest 表 `cython` 行补「`build()` 恒为 False；Cython 产物请用 `generate(cython=True)` 自行落盘」 |
| C-06 | `dcg/codegen.py:2447-2451` + `2529-2535` | `dcg.generate()` 没有 trace 参数，`output.trace=true` 时 `generate()` 产物与 false **逐字节相同**；trace 代码只在 `build()` 里生成 | 用户想用 `generate()` 拿 trace 版本会静默拿到普通版本 | `tests/test_codegen_snapshot.py:581`（只测 build） | 接口 §4.1 L454 补「trace 只有 `build()` 会写 `<name>.trace.py`，`generate()` 恒不产 trace」 |
| C-07 | `dcg/codegen.py:587-597` | optional 字段的**兄弟表达式**里的常量下标链（`$.m[0]`/`$.m.k`）在非 safe 模式发**裸读**，绕过 `_ix_get` → 可抛 TypeError | optional 的「缺值⇒None」承诺在 `expr` 里取不到 | 无 | 接口 §4.3 判定顺序表 #8 / §3.4 L275-276 补「optional 只保护字段自身取值；表达式里的常量下标链仍是裸读」 |
| C-08 | `dcg/jx/registry.py:48/56/91-103` | `Registry` 还有 `unregister` / `qualname` / `names` / `__contains__` / `__getitem__` / `__len__` / `__repr__` | 表达式层扩展的可用面被文档低估（接口 §3.1/§5.1 只写 `fn`/`get`） | `tests/test_docs_consistency.py:62/79`（只测拒/纳） | 接口 §3.1 的 `Registry` 代码块补齐 |
| C-09 | `dcg/jx/policy.py:148/152` | `is_higher_order` / `is_nondeterministic` 两个谓词已导出实现，但**不在 `policy.__all__`** | 外部想按类别判函数只能自己抄表 | 无 | 接口 §5.2 策略表、语法 §13.10 补两名（并决定是否加进 `__all__`） |
| C-10 | `dcg/runtime.py:883-930` | `CLOCK` / `RANDOM` 的**类契约**：替换对象必须实现 `now_ms()` / `random()` / `shuffle(xs)` | 用户按文档「换成固定实现」会不知道要实现哪些方法（语法 §12.4 的 FixedClock 代码块是唯一线索，接口文档没有） | 无 | 接口 §3.7 补三个方法的签名 |
| C-11 | `dcg/codegen.py:1191-1197` | `$sort` 只在「同数值字面量数组」或「全字符串字面量数组」时内联 `sorted()` | 文档只说「`$sort`（同类型字面量数组）→`sorted()`」，没说混合/字段数组一定走 `_so` | 无 | 语法 §12.2 的 `$sort` 行补「混合类型/字段数组 → `_so`」 |
| C-12 | `dcg/codegen.py:118-121` + compiler | 回调第二形参缺省时生成器自动补 `_i1`（`function($x){...}` → `lambda x,_i1:`） | 产物里出现 `_i1` 这个未文档化的固定名；若将来占位符命名调整会破用户快照 | 无 | 语法 §12.3 补一句「隐式第二形参固定为 `_i1`」 |
| C-13 | `dcg/build.py` 全文 | `python -m dcg.build` 只有 10 行壳（`from .codegen import _main` + `raise SystemExit(_main())`） | 想知道 CLI 入口在哪的人会找到空文件 | 无 | 接口 §4.1 L403 已提，可加一句「`dcg/build.py` 只是薄壳」 |
| C-14 | `tests/conftest.py:10-49` | 沙箱环境自动跳过的机制（`DCG_SANDBOX_COMPAT` 开关、`_NEEDS_LISTABLE_TMPDIR`、`dcg_sandbox_unavailable` marker） | 用户在本机看到「14 skipped」会困惑 | 无 | README 附录 B 补一小节 |
| C-15 | `dcg/runtime.py:1021-1036` | `SOURCE_NEEDS_MATH` 的**语义**是「会被内联的 math 依赖」，故显式排除 `_pw`（`$power` 内联发 `a**b`） | 读者按字面理解「依赖 math 的 helper」会疑惑 `_pw` 为何不在集合里 | `tests/test_codegen_snapshot.py:89`（测到了排除 `_pw`，但没解释给读者） | 接口 §4.4 L567 补「排除 `_pw`：`$power` 内联直接发 `a**b`」 |
| C-16 | `dcg/codegen.py:941(_never_undef)` / `:1632(_read_safe)` | 「字面量 or helper」的双判据（值不会是哨兵 + 读这一格不会抛） | 直接决定 `{'k':v}` 裸字面量 vs `_ob([...])`，进而决定产物长度与 undefined 丢弃行为；**这是 README 产物样例过期的根因**（见 D-02） | `tests/test_codegen_snapshot.py:845-932`（5 例，间接） | 接口 §4.2 规则 3 补一句判据，或至少在 §4.5 说明「嵌套字面量在不可证时退回 `_ob`」 |

**C 类共 16 条**，其中 C-01（架构文档整块缺 aggregate）与 C-06/C-07（生成器与文档行为差异）对用户影响最大。

---

## D. 文档与实现不符（直接矛盾）

| 编号 | 文档出处与原文 | 源码出处与实情 | 判定该改哪边 | 有无测试守护 | 建议 |
|---|---|---|---|---|---|
| **D-01** | 接口文档:583-596：CAP 样例含 `from dcg.runtime import *` 首行、`_ix_get(_ix_get(_ix_get(R,5),'a'),1)` 长链；**:584-585 明确写「`hourD`/`hourE` 是 `expr`，所以它们**不**复用 `_a1`/`_a2`，而是各自重新走 `_ix_get` 链 —— 这是 optional 安全的取值口径，不是冗余读」**；架构文档:567-578 + **:596-599 同款断言** | 实测 `dcg.generate(V2_CAP)`（`tests/workload.py:64-76`）产物 9 行：`_a=R[5]['a']` / `_a1=_a[1]` / `_a2=_a[2]` / `_c([_a1,_a2,(str(_a1)+str(_a2)),(_a1+_a2)])` / `return _o`。**`expr` 复用了 `_a1`/`_a2`，且无 import 行**（纯内联负载按 `dcg/codegen.py:2315-2317` 不发 `from dcg.runtime import *`） | **改文档**（实现的口径更新、更短，且 `_a1` 本身来自裸读，语义与内联一致） | `tests/test_codegen_snapshot.py` 的规则1 只测记忆化，**没有任何测试拿 CAP 样例逐字对拍** | 用真实产物替换接口 §4.5 与架构 §7.3 的 CAP 样例，并改写 :584-585/:596-599 的「不复用」解释 |
| **D-02** | README:156-174：「产物（15 行 / 798 字符）」+ 裸 dict 字面量 `{'from':_dep,'to':_arr,'span':total}` / `{'count':1,'span':total,...}` | 实测 `dcg.generate(examples/readme_complex.SCHEMA)` = **15 行 / 820 字符**；同段嵌套对象发 `_ob([('from',_dep),('to',_arr),('span',total)])`、`_ob([('count',1),('span',total),('deep',{...})])`。根因：工作区新增 `_read_safe`/`_never_undef` 双判据（`dcg/codegen.py:941/1632`）收紧了字面量放行 | **改 README** | `tests/test_readme_complex.py` 只断言行为与 `_a`/`_a2` 片段，**不断言整段产物文本** | 更新该段产物与字符数；并删掉「798 字符」（README:2381 自己说字符数字已删，自相矛盾） |
| **D-03** | README:1210-1249（组合矩阵 rows 产物「9 行 / 303 字符」、`{'sku':_sku,'qty':_qty}`、`_ml(_ix_get(R,'qty'),_ix_get(R,'price'))`）；README:1937-1981（aggregate A.8.5 的 `{'s':_sku,'n':R['n']}`、`'stat':{'n':[r['n']for r in [r[1]for r in _o]]}`） | 实测分别是 **9 行 / 292 字符**、`_ob([('sku',_sku),('qty',_qty)])`、`_ml(_qty,R['price'])`；aggregate 段实测 `_c([R['sku']])` + `'stat':{'n':[_ix_get(r,'n') for r in _d]}`（走 `_aggregate_direct` 直读 `_d`，更短） | **改 README** | 行为断言有（`tests/test_readme_complex.py:47-58`），**产物文本无** | 同 D-02，一次性全量重跑 README 里的产物样例 |
| **D-04** | 语法文档:143：`$string(a)` → 「内联（裸代码）」 | 实测 `_c([a,_S(a)])` —— `$string` **永不内联**（`dcg/codegen.py` `_inline_ok` 无 `$string` 分支；`_inline_text` 也无）；架构文档:650 说 `$string`（数值可证）→ `str()`，**同样不存在**（实测 `$string(42)`→`_S(42)`） | **改文档**（两处） | 无 | 语法文档是生成物 → 改 `tests/gen_jsonata_manual.py` 的示例表后重跑渲染；架构 §7.5 L650 手改 |
| **D-05** | 接口文档:178-181：`$boolean(a)`、`$not(a)`（输入 `{'a': 1}`）→「内联（裸代码）」 | 实测 `a` 声明 `any` 时 `_B(a)`/`_nt(a)`；只有类型可证（`$boolean(0)`→`bool(0)`）才内联。表格没交代类型前提 | **改文档**（补前提） | 无 | 生成器里给该行补字段类型声明 |
| **D-06** | 语法文档:72/74/75/76/77/80：`$.name`、`$.5`、`$.5.a`、`$.5.a.1`、`$.5.a.1.b`、`$.a[0]` → 「helper（`_ix_get`）」 | 实测全部是**裸下标链**：`$.name`（声明 str）→ `_name`；`$.5`→`R[5]`；`$.5.a.1`→`R[5]['a'][1]`；`$.5.a.1.b`→`R[5]['a'][1]['b']`；`$.a[0]`→`_a[0]`。**`_ix_get` 只出现在动态下标 `$.a[b]`/`$.a[$.b]` 上** | **改文档**（生成物） | `test_jsonata_manual.py:32` 的字节门只保证「文档==渲染器」，渲染器本身把标注写错了 | 修 `tests/gen_jsonata_manual.py` 的 `product_shape` 判定（见 F-11） |
| **D-07** | 语法文档:82、:91 与 :338、:523-524：`$.a[$i]`、`$i + 1` 列成「编译期拒绝」，同时说明「生成器层报 SchemaError」 | 实测 `dcg.compile_expr("$i + 1")` **通过**（返回 var IR）；`dcg.generate()` 才抛 `SchemaError`。「编译期拒绝」与「生成器层」是两级，表格列名与说明自相矛盾 | **改文档**（列名改成「生成器层拒绝」） | `examples/jx/05_paths.jx` gap 2 用例断言了行为 | 生成器里把该列文案统一成「生成器层 SchemaError」 |
| **D-08** | 接口文档:180-193：「`validate` 返回深拷贝，默认值补齐」的**归一化结果样例**仍是 `"value": "$.5.a.1"`（字符串）、`"input":{"source":"$"}` | 实测归一化后：`value` = tuple `(5,'a',1)`、`input.source` = `()`、每个字段被补 `optional: False` | **改文档** | `tests/test_schema.py:37/49` 测默认值补齐，**没测这两个具体形态** | 用真实 `validate()` 输出替换该样例 |
| **D-09** | 接口文档:215-220 四条报错示例 | 实测 4/4 文案不同：`out.fields.sum: ...（给了 2 个: value、const）`、`out.fields.hourD.type: type 非法: 'number'（可选: ...）`、`out.fields.a.value: ...，得到 'a'；v2 里裸字符串不是路径`、`out.fields.hourD.expr: 裸名 'foo' 不是本次 schema 声明的字段；已声明: [...]（行内取值请写 $.foo 或 $.路径）` | **改文档** | `tests/test_schema.py` 用 `pytest.raises(SchemaError)` 不 match 文案 | 用实现文案替换 |
| **D-10** | 接口文档:701-706 §7.1 六条示例 | 同上：4 条不符，仅 `output.before` 那条逐字一致（`:706`） | **改文档** | 无 | 同 D-09 |
| **D-11** | 接口文档:716-717 §7.2：`str(e)` 三形态按「单行 / 多行」区分 | `dcg/jx/policy.py:52`：`where = f"第 {col} 列: " if line == 1 else f"第 {line} 行第 {col} 列: "` —— 判据是 **`loc` 的行号是否为 1**，与源是否多行无关 | **改文档** | 无 | 改成「loc 行号为 1 → 只报列；否则报行列」 |
| **D-12** | 接口文档:726：`$eval` 的 feature 是「未知函数 `$eval`」 | 实测 feature = `$eval`：`第 6 列: $eval 不可逐行编译（运行期求值任意表达式 = 运行期解释器入口 + 注入面）` | **改文档** | 无 | 用实现文案 |
| **D-13** | 接口文档:356：`$$` 在 jx 层报「`var` 节点 `$$`」 | 实测 feature = `$$ 文档根`：`第 1 列: $$ 文档根 不可逐行编译（行模式没有整篇文档，只有当前行）`（语法文档:331 写的才是对的） | **改接口文档** | 无 | 对齐语法文档 §12.5 |
| **D-14** | 接口文档:567；架构文档:620：「用到才生成 **`import math as _m`**」 | `dcg/codegen.py:2313-2314`：`if "math." in src and not src.startswith("import math\n"): src = "import math\n" + src` —— 发的是 **`import math`**，无 `as _m`（`_M` 是 `dcg/runtime.py:33` 的模块级别名，产物里不出现） | **改文档**（接口文档:541 自己写的就是 `import math`，两处需统一） | `tests/test_e2e_flight.py:1137`（测的是 `import math`） | 两处统一成 `import math` |
| **D-15** | README:1363「tests/ 900 tests（891 passed / 9 skipped）」；README:2377「891 passed / 9 skipped / 0 failed（900 collected）」 | 实测 `tests/` 的 `def test_` 总数 = **366**（14 个文件，逐文件计数见 `evidence/_check_testscale.py` 输出）；`evidence/pytest_v21_final.txt:21` 的历史日志是「878 passed / 14 skipped（total 892）」，且其中 4 条来自**已删除**的 `tests/test_migrate.py`（:32）。三套数字互不相同、无一是当前值 | **改 README** | `tests/test_docs_consistency.py` 不校验测试规模 | 把两处改成实测值，或明确标注「历史快照（日期/commit）」 |
| **D-16** | 接口文档:285 小标题「A 类｜行内函数（46 个，**直接发射运行期 helper**）」 | 同一文档 :517 的内联清单说其中 20+ 个会内联；实测 `$length/$uppercase/$lowercase/$trim/$abs/$contains/$split/$substring*/$replace/$pad/$sum/$min/$max/$average/$reverse/$sort/$boolean/$not/$floor/$ceil/$power/$sqrt` 均可内联 | **改文档** | 无 | 小标题改为「可按需内联或发射 helper」 |
| **D-17** | README:1296「深路径（expr / 反引号 / `value` 全侧）✅ 任意深度」 | `expr` 侧连续数字段不可写（B-05）；`value` 侧（`parse_value_path`）点号数字段**合法**（`$.a.0.b`→`('a',0,'b')`）→ 两套语法口径不同，README 用「全侧」合并了 | **改 README** | `tests/test_schema.py:106`（value 侧）有，expr 侧无 | 拆成两行分别描述 |
| **D-18** | README:1354「runtime.py ★ 产物运行期 helpers（**产物第一行** `from dcg.runtime import *`）」 | 实测纯内联负载（CAP/M0）**首行直接是 `def run(_d):`**，只有用到 helper 才发 import（`dcg/codegen.py:2315-2317`）；与接口文档:496、架构文档:561 的说法冲突 | **改 README** | 无 | 改为「用到 helper 时才发」 |
| **D-19** | 架构文档:129（mermaid 节点标签）、:130-131 | 数据流图里把「DCG 接口实现细节文档 §1 IR（纯函数）」整句塞进 mermaid 节点标签 | 渲染出错位/语义混乱 | 无 | 手改该 mermaid 块 |
| **D-20** | 架构文档:235 表的「1.13–1.18×」vs §2.1 事实表「1.2× 上下」 | 同一测量两个区间，口径不一 | **改架构文档** | 无 | 统一成一个数或注明是不同 workload |
| **D-21** | 接口文档:274 与 :678「`$.5.a.1.b` 编成**同一种**下标链」 | 实测 `$.5.a.1.b` → `R[5]['a'][1]['b']`（**裸链**）；而 `$.5.a[b]` → `_ix_get(...)`（helper）——「同一种」只在常量段内成立 | **改文档**（补「常量段 vs 动态段」区分） | 无 | 补一句 |
| **D-22** | 架构文档:771「schema 里出现 `row` 一律未知键」vs README:1988 / 语法文档:494「`row` 是**保留字段名**（被禁用）」 | 两者说的不是同一层（一个是 schema 字段**键**、一个是行字段**名**），但措辞几乎相同，读者极易认为矛盾。实测：`{"row": {...}}` 作为字段名 → `SchemaError: 字段名 'row' 与生成码内建名冲突`；作为字段 spec 的键 → 未知键 | **改文档**（两处各加限定词） | `tests/test_schema.py:457/501` 测了 `row` 被拒 | 架构 §11 加「指 schema 键」、README 加「指行字段名」 |

**D 类共 22 条：P0 一条（D-01），P1 八条（D-02~D-04、D-06、D-08~D-10、D-15），其余 P2。**

---

## E. 三份文档之间的互相矛盾（同一契约两处说法不同）

| 编号 | 契约 | 说法 A | 说法 B | 谁对 | 严重度 |
|---|---|---|---|---|---|
| E-01 | `$round` 是否内联 | 语法文档:28 与 `tests/gen_jsonata_manual.py:695` 把 `round()` 与 `math.floor/ceil` 并列为「有语义一致的 Python 内建等价」 | 语法文档:42「#9 `$round` 2 参」、:164「`$round` → helper `_ro`」、:571、:588「不由 Python round() 承担」；接口文档:517「**`$round` 不在内联表内**」；架构文档:651「`$round` 不内联走 `_ro`」 | **B 对**；实测 `$round(2.5)`→`_ro(2.5)`=3。**且 `tests/test_e2e_flight.py:1132-1133` 期望 `round(3.5)`/结果 2，是过期方** | **P1** |
| E-02 | 策略表规模 | 接口文档:665 列 **7 名**（`ROW_FUNCS/HIGHER_ORDER/NONDETERMINISTIC/STRUCTURAL/BARE_CALL_ALIASES/is_row_func/rejection_reason`，漏 `Unsupported`） | 架构文档:728 列 **8 名**（多 `Unsupported`） | **架构对**；实测 `dcg/jx/policy.py:17-26` `__all__` 恰 8 名 | P2 |
| E-03 | 产物 math import 写法 | 接口文档:541「产物首行额外加 `import math`」 | 接口文档:567 / 架构文档:620「生成 `import math as _m`」 | **:541 对**（`dcg/codegen.py:2313-2314` 发 `import math`） | P2 |
| E-04 | CAP 样例说明 | 接口文档:584-585、架构文档:596-599 都主张「`expr` 不复用 `_a1/_a2`，各走 `_ix_get`」 | 同文件的样例产物本身也不一致（接口:594 与架构:578 字面略有差异）；实测两者都与实现不符 | **两边都错**，见 D-01 | **P1** |
| E-05 | 两种语料数字 | conformance 50 例（接口:365、`tests/fixtures/conformance.json`） | .jx 语料 242 例（语法文档:15、:500-517） | **都对**，是两套语料；但没有任何文档解释二者关系 → 读者误判为矛盾 | P2 |
| E-06 | 测试规模 | README:1363「900 tests / 891 passed」 | README:2377「891 passed / 9 skipped / 900 collected」；`evidence/pytest_v21_final.txt:21`「878 passed / 14 skipped（total 892）」 | **都不对**（实测 366），见 D-15 | P1 |
| E-07 | `$string` 可否内联 | 架构文档:650「`$string`（数值可证）→ `str()`」 | 接口文档:518 保留 helper 清单含「字符串编码类」；实测永不内联 | **接口文档对**，架构文档:650 需删 | P2 |
| E-08 | 接口文档内部：A 类「直接发射 helper」 | 接口文档:285 | 接口文档:517-518 内联清单 | **:517 对** | P2 |
| E-09 | 三个能力表 vs `STRUCTURAL` | 语法文档:396-399「十类 11 条，`$$` 额外一类」 | 接口文档:348-356 只列 5 行（把 7 类合并成一行） | 说法不同但**不矛盾**（粒度不同）；建议接口文档注明「合并行的展开见语法 §12.5」 | P2 |
| E-10 | `$sqrt` 内联条件 | 接口文档:517「`$sqrt`（可证非负）→ `math.sqrt`」 | 接口文档:518「`$sqrt`（不可证非负）→ helper」；架构文档:651「`$floor/$ceil/$sqrt`→`math.*`」（无前提限定） | 前两者是同一判据的两面（对）；**架构文档:651 缺前提**，读者会以为 `$sqrt($.a)` 也内联 | P2 |

**E 类共 10 条。**

---

## F. 优先修复清单（P0 → P2）

### P0

- **F-01｜D-01**：把接口文档 §4.5（:583-596）与架构文档 §7.3（:567-578 及 :596-599 解释）的 CAP/M0 样例换成**当前 `dcg.generate()` 的真实产物**，并删掉「`expr` 不复用 `_a1/_a2`、各走 `_ix_get`」这段已被实现推翻的解释。产物样例是用户判断「引擎长什么样」的第一入口，错一个就是全盘误解。

### P1

- **F-02｜B-01**：`Registry` 注册的函数进不了 schema 产物（`dcg/codegen.py:747` 不传 registry）。要么加 `registry=` 参数穿透，要么在三份文档 + README 明写「仅表达式层可用」。
- **F-03｜B-02 + F-11**：`_x` 分派器 / `@fn` 钩子的 `rfind('.')+__import__` 不支持类方法与嵌套属性（`json.decoder.JSONDecoder.decode` → ModuleNotFoundError），而 `registry.py:23` 的 `_IMPORTABLE_QUALNAME` 允许。修实现（最长可导入前缀 + getattr 链）。
- **F-04｜B-03**：语法文档 §15.2:570「`$.m[0]` 已修复」与实机不符（仍抛 TypeError）。改文档或改生成器（常量下标链加 null/`_U` 守卫）。
- **F-05｜B-04**：接口文档:537「内联不改 undefined 语义」被 `$floor('abc')→TypeError` 等反例削弱。补一条「违反声明的数据会抛 Python 异常」的显式说明。
- **F-06｜D-02 + D-03**：README 的 3 处产物样例（:156-174、:1237-1249、:1937-1981）与实测不符（`_ob` 化、字符数、`_aggregate_direct` 直读 `_d`）。全量重跑一遍 README 产物块并同步字符数，同时删掉 :156 的「798 字符」（与 :2381 自相矛盾）。
- **F-07｜D-04 + D-06 + D-07**：`JSONata 语法.md` 是生成物，必须在 `tests/gen_jsonata_manual.py` 里修三件：①`product_shape` 的判定（当前靠「产物源码里有没有 `_xx(`」猜，导致 `$.5.a.1` 被标成 helper、`$string(a)` 被标成内联）；②:695 把 `round()` 列进内建等价清单；③:82/:91 的「编译期拒绝」→「生成器层拒绝」。改完重跑渲染让字节门通过。
- **F-08｜D-08 + D-09 + D-10 + D-11 + D-12 + D-13**：接口文档所有「原文照抄」的报错/归一化样例（:180-193、:215-220、:701-706、:709-727）用实测文案替换。建议顺手加一个测试：把文档里的每条形如 `path: msg` 的样例抽出来，对着真实异常 `str(e)` 断言。
- **F-09｜D-15 + E-06**：README:1363/:2377 的「900 tests / 891 passed」改成实测值（当前 366），或明确标注为历史快照并给日期；`evidence/pytest_v21_final.txt:32` 里的 `tests/test_migrate.py` 跳过项是已删除文件的残留，需重跑一次证据日志。
- **F-10｜C-01**：架构文档**整块缺 `aggregate`**（grep 命中 0）。它决定 `shape:"dict"` 下嵌套对象的输出结构，必须在 §8 输出形态里补一张表。
- **F-11｜E-01**：修 `tests/test_e2e_flight.py:1132-1133` 的过期期望（当前期望 `round(3.5)` 且 `$round(2.5)==2`，与 half-up 语义直接冲突），并修生成器 `tests/gen_jsonata_manual.py:695` 的 `round()` 误列。

### P2

- **F-12**：接口文档:274/:678 补「连续数字段必须写方括号」（B-05）。
- **F-13**：接口文档:567 / 架构文档:620 的 `import math as _m` → `import math`；并补「排除 `_pw`」的理由（C-15）。
- **F-14**：manifest 表（接口:392）注明 `cython` 由 `build()` 恒为 False（C-05）；`generate()` 不产 trace（C-06）。
- **F-15**：接口文档补 `Registry` 完整方法面（C-08）、`policy.is_higher_order/is_nondeterministic`（C-09）、`CLOCK/RANDOM` 三方法契约（C-10）。
- **F-16**：接口文档:285 小标题改「可按需内联」（D-16）；架构文档:650 删 `$string→str()`（E-07）；架构文档:651 给 `$sqrt` 补前提（E-10）。
- **F-17**：架构文档:129 mermaid 节点标签清洗（D-19）、:235 与 §2.1 的倍数口径统一（D-20）。
- **F-18**：README:1354「产物第一行 `from dcg.runtime import *`」改为「用到 helper 时才发」（D-18）；README:1296「全侧任意深度」拆成 expr/value 两行（D-17）。
- **F-19**：接口文档:665 策略表 7 名 → 8 名（E-02）；语法/接口文档补一句「242（.jx 语料）与 50（conformance 冻结集）是两套」（E-05）。
- **F-20**：`evidence/doc_drift_report.md` 整份已过期（称 README 1362 行 / 引用已删的 `migrate`、`ROW_MODES`、字段级 `row`），建议在文件头加过期标注或删除。
- **F-21**：清理残留：`dcg/__pycache__/migrate.cpython-313.pyc`、`tests/__pycache__/test_migrate.cpython-313-pytest-9.1.1.pyc`、`_tmp/audit_migrate.py` 等三个会 import 已删模块的脚本（跑一次就 ModuleNotFoundError）。

### F 附：**无测试守护的文档契约**（按风险排序，给出该补的测试）

> 这一列是本次审计的第二个结论：**文档里绝大多数「产物形态」「错误文案」「样例正确性」契约都没有自动化守护**。语法文档靠字节门保住了「文档==渲染器」，但渲染器本身的标注错了没人发现（D-04/D-06 就是这么来的）；接口文档与架构文档完全无门。

| 风险序 | 无守护的契约 | 现状 | 该补的测试（文件 / 函数名 / 断言） |
|---|---|---|---|
| 1 | CAP/M0/schema 产物样例（D-01/D-02/D-03） | 三份文档 + README 的手抄样例全部漂移 | `tests/test_docs_consistency.py::test_documented_artifacts_match_real_output`：从文档里按围栏块抽出 `from dcg.runtime import *`/`def run(` 开头的代码块，对同名 schema `dcg.generate()` 后逐字符比对；至少钉住 `tests/workload.py:V2_CAP` 与 `examples/readme_complex.SCHEMA` |
| 2 | 报错文案样例（D-09/D-10/D-12/D-13） | 4/6 与实现不同 | 新增 `tests/test_doc_error_messages.py::test_documented_schema_errors` / `::test_documented_unsupported_features`：把文档里 `path: msg` 与 `第 N 列: feature 不可逐行编译` 形态的行抽出，对真实 `str(e)` 断言 |
| 3 | 语法文档「产物形态」列（D-04/D-05/D-06） | 生成器的 `product_shape` 用子串猜 | 改 `tests/gen_jsonata_manual.py:210-229` 为「以 `_inline_ok/_inline_text` 的判定结果为准」；再加 `tests/test_jsonata_manual.py::test_product_shape_column_matches_codegen`：对每条示例调用真实的 codegen 判定函数 |
| 4 | `_x` 分派器 qualname 能力（B-02） | 无 | 新增 `tests/test_codegen_snapshot.py::test_dispatch_supports_class_method_qualname`：注册 `json.decoder.JSONDecoder.decode` 之类，`generate` 后 `run` 不抛 ModuleNotFoundError |
| 5 | `Registry` 是否能进产物（B-01） | 无 | `tests/test_codegen_snapshot.py::test_registry_is_expression_layer_only`：断言 `generate()` 对注册函数的反应（无论选哪种修法，都要把行为钉死） |
| 6 | `$.m[0]` 对 null 的行为（B-03/D-01） | 无 | `tests/test_sentinel_integrity.py::test_constant_index_on_null_field`：`{'m':None}` 经 `generate()` 后 `run` 的行为（现在抛 TypeError，文档说给 None） |
| 7 | README 测试规模（D-15） | 无 | `tests/test_docs_consistency.py::test_readme_test_count_matches_collected`：用 `pytest --collect-only -q` 的条目数与 README 断言的数字比对（或直接删掉 README 里的固定数字，改为「见 CI」） |
| 8 | 四种 shape 的单字段短形态（C-类） | 只测多字段 | `tests/test_codegen_snapshot.py::test_single_field_shape_tails`：断言 `rows`→`[{'a':r} for r in _o]`、`dict`→`{'a':_o}` |
| 9 | `aggregate` 在非 dict shape 下静默忽略（C-02） | 无 | `tests/test_schema.py::test_aggregate_outside_dict_shape_is_rejected_or_documented`：钉住当前行为 |
| 10 | `build()` manifest `cython` 恒 False（C-05） | `tests/test_codegen_snapshot.py:581` 只测 sha1 戳 | `::test_manifest_cython_is_always_false` |
| 11 | `generate()` 不产 trace（C-06） | 无 | `tests/test_codegen_snapshot.py::test_generate_never_emits_trace` |
| 12 | `inline_optional` 两形态（A-18） | 无 | `tests/test_codegen_snapshot.py::test_inline_optional_switch_changes_guard_form`：断言 `None if _t1 is _U` vs `is_u(_t1)` |
| 13 | 策略表 7 vs 8 名（E-02） | 无 | `tests/test_docs_consistency.py::test_policy_export_surface_is_documented` |
| 14 | 沙箱跳过机制（C-14） | 无 | 文档侧补说明即可，不需测试 |

---

## 一句话总结

**实现是完整的，文档是过期的。** 引擎侧（`dcg/`）的三张能力表、84 helper、9 条形态规则、4 种 shape、钩子三写法、py{}/反引号段、aggregate、错误定位**全部有真实实现且被 `tests/` 大量覆盖**（A 表 40 条）；真正的问题集中在**描述层**：三份文档 + README 里的产物样例、报错文案、"内联/helper" 标注、测试规模数字，全部停留在某个历史快照上，而**除语法文档的字节门外没有任何自动化守护**——字节门只保证「文档 == 渲染器」，渲染器自己的标注错了（`$string` 内联、`$.5.a.1` 走 `_ix_get`）照样发布。四个 P1 级实现缺口（Registry 进不了产物、qualname 只切最后一点、`$.m[0]` 仍抛 TypeError、内联绕过 undefined 语义）都不影响「文档写的那条路能用」，但都影响「文档承诺这条路的边界」。
