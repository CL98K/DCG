# 文档 ↔ 实现 逐条矩阵（机器重生成）

> **本文件由 `evidence/gen_doc_matrix.py` 重生成，不要手改**：`python -X utf8 evidence/gen_doc_matrix.py --write`。
> 生成时间与下面所有数字都取自**生成那一刻的工作区**；文档一改，重跑即可，不再存在「锚点行号过期」的维护负担。

## 0. 规模与符号（唯一事实源，重跑即得）

| 项 | 实测 |
|---|---|
| `README.md` | 1896 行 / 81412 字节 |
| `DCG 接口实现细节文档.md` | 890 行 / 81990 字节 |
| `DCG 整体设计方案与架构文档.md` | 933 行 / 87421 字节 |
| `JSONata 语法.md` | 642 行 / 62751 字节 |
| `验收报告.md` | 624 行 / 54705 字节 |
| `dcg.__all__` | 9 |
| `dcg.ir.__all__` | 41（KINDS 16 / UNARY_OPS 3 / BIN_OPS 15） |
| `dcg.jx.__all__` | 21 |
| policy 四表 | ROW 46 / HO 6 / NONDET 4 / STRUCT 10 |
| 函数并集（去重后） | 55 |
| runtime `HELPERS` / `__all__` | 86 / 96 |
| `SOURCE_NEEDS_MATH` | `_ce, _fl, _sq` |
| `.jx` 语料 | 242 条（ok 209 / fixed 33） |

签名（`inspect.signature` 实测）：

```
generate(schema: 'dict', *, cython: 'bool' = False, inline_optional: 'bool' = True, registry=None, strict_qualname: 'bool' = True) -> 'str'
build(schema: 'dict', out_dir: 'str', *, name: 'str | None' = None, overwrite: 'bool' = False, registry=None, strict_qualname: 'bool' = True) -> 'dict'
compile_expr(src: 'str', *, loc_base=(1, 1), registry=None) -> 'tuple'
```

## 1. 怎么复核这张表之外的东西

逐条契约的**语义**判断无法机器复核（「这个措辞是否忠实于实现」需要读代码）。所以本表只做两件事：

1. **给每条结论配一条可复跑的命令或 grep**，任何人可以原地验证；
2. **不写死行号**，改写成「在 `grep -n` 的输出里」——行号由读者现查，永不腐烂。

## 2. 2026-10-24 历史审计表的结论去向

原表 40(A) + 5(B) + 16(C) + 22(D) + 10(E) + 21(F) 条，逐条处置如下。

| 原编号 | 结论 | 处置 | 复核方式 |
|---|---|---|---|
| A-19 | `_x` 用 `__import__` + `rfind` 只切最后一点 | **已修**（DEFECT-023）：改为「从长到短试」`importlib.import_module` + 剩余段 `getattr` 属性链，且与钩子侧 `_sh` 同款 | `grep -n 'importlib as _il' dcg/codegen.py`；`python -m pytest tests/test_registry_dispatch.py -q` |
| B-01 | `Registry` 注册的函数进不了产物 | **已修**（v2.1）：`generate(registry=...)` / `build(registry=...)` 显式传入后才发 `_x(...)`，不传仍报 `未知函数`（保纯函数） | `python -c "import dcg,inspect;print(inspect.signature(dcg.generate))"` |
| B-02 | `qualname` 只切最后一点，多级类方法导入失败 | **已修**：同上，`pkg.mod.Class.method` 可分派 | `python -m pytest tests/test_registry_dispatch.py -q` |
| B-03 | `$.m[0]`（required）对 `null` 抛 `TypeError` | **仍成立，且是契约本身**：required = 裸链 = 真实 Python 异常由用户承担 | `grep -n '裸链' 'DCG 接口实现细节文档.md'` |
| B-04 | 内联不改 undefined 语义（类型声明不是转换） | **仍成立，且是契约本身**：`type` 只影响类型判定与内联，不做运行期转换 | `grep -n '声明不是转换' 'DCG 接口实现细节文档.md'` |
| B-05 | `$.a.1.2` 被词法层当小数拒绝 | **已修**：路径上下文里连续整数下标可解析 | `python -c "import dcg;print(dcg.compile_expr('$.a.1.2'))"` |
| C-02 | `aggregate` 在非 dict 形状被静默忽略 | **已修**：改为构建期 `SchemaError`（`_check_aggregate_shape`，覆盖 `generate()` / `build()` / CLI，嵌套 `fields` 逐层查） | `grep -n '_check_aggregate_shape' dcg/codegen.py` |
| C-04 | `SOURCE_PRELUDE` 空串但被文档当作有内容 | **已澄清**：文档改为写「当前为空串，保留为扩展点」 | `grep -n 'SOURCE_PRELUDE' dcg/runtime.py 'DCG 接口实现细节文档.md'` |
| C-06 | `generate()` 不产 trace 产物 | **仍成立，且是有意为之**：`output.trace=true` 只让 `build()` 额外落一个 `<name>.trace.py`，`generate()` 是无副作用纯函数，正式产物与 `trace=false` 逐字节相同 | `grep -n 'trace_artifact' dcg/codegen.py` |
| D 系列 10 项真缺陷 | D-AGG-01 / D-HOOK-01 / D-HOOK-02 / D-OPT-01 / D-NEST-01 / D-MATH-01 / D-COL-01 / D-AGG-02 / D-PLAN-01 / D-SAFE-01 | **全部已修**，回归见 `验收报告.md` §2 | `python -m pytest -q`（全绿） |
| E/F 系列 | 性能与优先级清单 | 已并入 `验收报告.md` §5 | `python -X utf8 evidence/perf_runner.py`（协议复跑） |

## 3. 2026-10-25 ~ 26 新增/收敛的契约（原表未覆盖）

| 主题 | 现口径 | 复核方式 |
|---|---|---|
| 读法主判据 | 只有 `optional`（含 `default`）决定读法；`type` **不参与**。三者优先级：**声明口径**（该路径被唯一一种方式声明过）→ **引用点口径**（同一路径 required / optional 都声明过时按引用处） | `python -m pytest tests/test_req_safe_prefix.py tests/test_value_expr_parity.py -q` |
| 字段观测值 | optional 字段的值**恰好一次** undefined 判断后落 `None`（`value` / `expr`、四种 shape 一致）；要观察 undefined 本身须用 `$exists(<expr>)` | `grep -n '恰好一次' 'DCG 接口实现细节文档.md'` |
| 前缀提升 | required 单段也提升；子树 required/optional 并存时局部名绑成**裸读**（`_buyer=R['buyer']`），安全端从中派生 `.get(k,_U)`；**反向禁止**「安全口径绑定被 required 消费」；**N2 已修**：表达式里的安全链统一走 `safe_ref()` 令牌化，`plan()` 之后由 `text_of()` 从最近的前缀绑定派生逐段 `_ix_get`，绑定被消费端复用；数据级钩子上下文（`_data_var=="D"`）仍就地拼文本 | `grep -n 'safe_ref' dcg/codegen.py` + `grep -n '_eff_safe' dcg/codegen.py`；`python -m pytest tests/test_req_safe_prefix.py -q` |
| `$name` | 一律 `SchemaError`，**没有**「与已声明字段同名就按字段读」的降级（用户裁决 2026-10-25） | `grep -n '没有同名降级' 'DCG 接口实现细节文档.md'`；`python -m pytest tests/test_rule_consistency.py -q` |
| `aggregate` 非 dict | 构建期 `SchemaError`，不静默失效 | 见上 C-02 |
| 永久拒绝 | **10 个键 / 13 条现象**（`$$` 不在 10 键内，另计一条） | `python -c "from dcg.jx import policy as p;print(len(p.STRUCTURAL))"` |
| qualname 导入 | 钩子 `_sh` 与 rfunc `_x` 用**同一套**「从长到短试」，`pkg.mod.Class.method` 支持 | 见上 A-19 |
| `generate` / `build` | 都带 `registry=` 与 `strict_qualname=`（见 §0 签名实测） | 见上 B-01 |

## 4. 已知不设断言的项（如实登记）

| 项 | 为什么不断言 |
|---|---|
| 五份文档自身的行数 / 字节 | 每次修订必然变化，写成硬断言会让改文档就要改测试；改由本脚本重算（§0） |
| `README.md` 两个 manifest 例子（`lines` / `chars`） | 例子里没有附 schema，无法由生成器复算；只看形态不看数字 |
| `order_pipeline` 的绝对 µs | 依赖机器状态（同进程手写基线一起漂移），只断言**比值方向**与 `perf_protocol.json` 一致 |
| `Cython` 路径 | 本机无 `cl`，只做「剥掉 cdef 后与 py 逐字节相同」的文本等价断言 |

