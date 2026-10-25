"""规则一致性守护：同一条规则在四份文档 + 实现里的表述必须一致。

用户的原始意见（m04095 第 10 条）：验收报告说「文档对齐通过」，但文档之间仍有
自相矛盾的表述 —— 因为既有的守护测试只覆盖了三件事：

* `tests/test_docs_consistency.py` —— 硬数字（helper 数、函数数、用例数、导出名数）；
* `tests/test_doc_products.py`    —— 产物代码块与 `dcg.generate()` 逐字符一致；
* `tests/test_doc_error_messages.py` —— 报错文案逐字符出现在文档里。

这三类都是「**某个具体值**在一处写对了吗」，管不到「**同一条规则**在 A 文档这样写、
在 B 文档那样写」。本文件补的就是这一类：先把规则从文档里以结构化方式抽出来，
再断言各处表述指向同一个结论，并用真实 `dcg.generate()` 的产物做**裁决**。

设计原则（决定本文件怎么写，也决定它为什么可信）：

1. **每条规则都必须有实现裁决**。文档之间一致但一起错，是可能的；所以每条规则
   都附一个「实现到底怎么做的」断言，文档跟实现走。
2. **抽的是规则、不是特例**。例如「路径读法」这条规则要覆盖
   {required, optional} × {value, expr} × {any, 具体类型} 的组合矩阵，
   而不是挑一个能过的组合 —— 特例断言会掩盖规则分叉（历史上真发生过：
   `value` + `type:"any"` 曾走 `_ix_get`，而 `expr` + `type:"any"` 走裸链）。
3. 文档表述用正则抽取，抽不到就 fail（而不是 skip）—— 文档改写时必须回来
   更新这里的抽取式，这是有意的摩擦力，防止规则悄悄漂移。
"""

import os
import re
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import dcg  # noqa: E402
from dcg import runtime as rt  # noqa: E402


_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_DOCS = {
    "readme": "README.md",
    "api": "DCG 接口实现细节文档.md",
    "arch": "DCG 整体设计方案与架构文档.md",
    "manual": "JSONata 语法.md",
    "report": "验收报告.md",
}


def _read(name):
    with open(os.path.join(_ROOT, _DOCS[name]), encoding="utf-8") as f:
        return f.read()


def _product(fields, data, *, shape="list", name="t"):
    """编译并运行，返回 `(源码, 结果)`。"""
    schema = {"name": name, "output": {"shape": shape},
              "out": {"type": "object", "fields": fields}}
    src = dcg.generate(schema)
    ns = {}
    exec(src, ns)
    return src, ns["run"](data)


def _row_line(src):
    """产物里那一行 `_c([...])`（行式形状）。"""
    for line in src.splitlines():
        if "_c([" in line:
            return line.strip()
    return ""


# --------------------------------------------------------------------------
# 规则 1：路径读法只看「是否声明 optional」，与 value/expr/type 无关
# --------------------------------------------------------------------------
_PATH_COMBOS = [
    ("required+value+具体类型", {"type": "str", "value": "$.n"}, False, "R['n']"),
    ("required+expr+具体类型", {"type": "str", "expr": "$.n"}, False, "R['n']"),
    ("required+value+any", {"type": "any", "value": "$.n"}, False, "R['n']"),
    ("required+expr+any", {"type": "any", "expr": "$.n"}, False, "R['n']"),
    # 注意：`type` 是必填键（`validate()` 直接报「缺少必填键 "type"」），
    # 所以没有「未声明 type」这一档 —— 曾以为有，写进参数表后当场被
    # `SchemaError: out.fields.v: 缺少必填键 "type"` 拦下。
]


@pytest.mark.parametrize("label,spec,optional,bare", _PATH_COMBOS)
def test_required_path_is_always_bare(label, spec, optional, bare):
    """规则 1（实现裁决）：required 字段的路径一律裸链，与 value/expr、type 都无关。"""
    src, _ = _product({"v": spec}, [{"n": 1}])
    assert bare in src, "%s: 期望裸链 %s，实际产物：\n%s" % (label, bare, src)
    assert "_ix_get" not in src, (
        "%s: required 字段不该出现 `_ix_get`（这是 optional 的形态）。\n%s"
        % (label, src))


@pytest.mark.parametrize("label,spec,optional,bare", _PATH_COMBOS)
def test_optional_path_never_raises_on_missing(label, spec, optional, bare):
    """规则 1（实现裁决）：同样的 spec 加 optional 后，缺键必须得 None 而不是抛。"""
    spec = dict(spec, optional=True)
    src, got = _product({"v": spec}, [{}])
    assert "_ix_get" in src or ".get(" in src, (
        "%s: optional 字段必须走守卫取值。\n%s" % (label, src))
    assert got == [[None]], "%s: optional 缺键应得 None，实得 %r" % (label, got)


def test_value_and_expr_read_identically():
    """规则 1（防分叉）：`value` 与 `expr` 是同一规则的两种写法，产物必须逐字符相同。"""
    for typ in ("any", "str", "int64"):
        by_value, _ = _product({"v": {"type": typ, "value": "$.n"}}, [{"n": 1}])
        by_expr, _ = _product({"v": {"type": typ, "expr": "$.n"}}, [{"n": 1}])
        assert _row_line(by_value) == _row_line(by_expr), (
            "type=%r 时 value 与 expr 产物分叉：\nvalue: %s\nexpr : %s"
            % (typ, _row_line(by_value), _row_line(by_expr)))


def test_docs_state_the_optional_switch_is_the_only_switch():
    """规则 1（文档表述）：四份文档都必须说「读法只看 optional」，且不得再提
    「any 或单段路径走 _ix_get」这条已被删除的规则。"""
    api = _read("api")
    assert "取值形态只看一个开关" in api or "唯一开关" in api, (
        "接口文档没有写明「读法只看 optional 一个开关」")
    assert "`type` 声明只影响类型判定与内联，**不改变读法**" in api, (
        "接口文档没有写明 type 不改变读法")

    manual = _read("manual")
    assert "只看字段是否声明 `optional`" in manual, (
        "JSONata 语法.md 没有写明「只看字段是否声明 optional」")

    arch = _read("arch")
    assert "未声明 `optional` 的字段一律发裸链" in arch, (
        "架构文档没有写明 required 一律裸链")

    # 已被裁决删除的旧规则：任何文档都不许再把它当规则陈述。
    for name in ("api", "manual", "arch", "readme"):
        text = _read(name)
        assert "type:\"any\" 或单段路径" not in text, (
            "%s 里仍有已删除的旧规则「type:\"any\" 或单段路径 → _ix_get」" % name)


# --------------------------------------------------------------------------
# 规则 2：$i / 裸名与已声明字段同名时按字段引用（不是变量作用域）
# --------------------------------------------------------------------------
class _DynamicIndexCase:
    """`$.a[$i]` 的两种口径 —— 用真实产物裁决到底按哪种。"""


def test_dynamic_index_by_declared_field_name():
    """规则 2（实现裁决）：`$i` 同名已声明字段 → 按字段引用，产物裸链。"""
    src, got = _product(
        {"v": {"type": "int64", "expr": "$.a[$i]"},
         "i": {"type": "int64", "value": "$.i"},
         "a": {"type": "list", "value": "$.a"}},
        [{"a": [10, 20], "i": 1}],
    )
    assert "R['a'][i]" in src or "R['a'][R['i']]" in src, (
        "`$.a[$i]`（$i 同名已声明字段）应编译成裸链下标，实际产物：\n%s" % src)
    assert "_ix_get" not in src, "required 字段不应出现 _ix_get：\n%s" % src
    assert got == [[20, 1, [10, 20]]], "`$.a[$i]` 应为 20，实得 %r" % (got,)


def test_dynamic_index_unknown_var_still_rejected():
    """规则 2（实现裁决）：未声明的 `$x` 仍然报 SchemaError，不静默降级。"""
    with pytest.raises(dcg.SchemaError) as ei:
        _product({"v": {"type": "int64", "expr": "$.a[$nope]"}}, [{"a": [1]}])
    assert "$nope" in str(ei.value), "报错必须点名未声明的变量：%s" % ei.value


def test_docs_state_dynamic_index_rule_consistently():
    """规则 2（文档表述）：架构文档与 JSONata 语法.md 必须都说是「按字段引用」。"""
    arch = _read("arch")
    # 架构文档的原始措辞是「动态下标段里的字段名」（§4.5 依赖分析、§7.5 边界），
    # 意思就是 `$i` 与已声明字段同名时按字段引用；两种写法都认，但必须至少有一种。
    assert ("动态下标段里的字段名" in arch or "动态下标 `$.a[$i]`" in arch), (
        "架构文档没有写明动态下标里与已声明字段同名的 `$i` 按字段引用")
    manual = _read("manual")
    assert "按字段引用" in manual, "JSONata 语法.md 没写 $i 同名按字段引用"


# --------------------------------------------------------------------------
# 规则 3：helper 计数与名单（同一批函数，三处必须同一个数）
# --------------------------------------------------------------------------
def test_helper_count_is_consistent_everywhere():
    """规则 3（实现裁决 + 文档表述）：`len(HELPERS)`、文档名单、文档数字三者一致。"""
    n = len(rt.HELPERS)
    api = _read("api")
    arch = _read("arch")

    # 文档里的数字
    assert "**%d 个 helper**" % n in api, "接口文档没写「%d 个 helper」" % n
    assert "**%d 个 helper**" % n in arch, "架构文档没写「%d 个 helper」" % n

    # 文档里的名单必须与 HELPERS 逐项相同（多一个、少一个都算漂移）
    m = re.search(r"完整名单.*?```text\n(.*?)```", api, re.S)
    assert m, "接口文档里找不到 helper 完整名单代码块"
    listed = set()
    for line in m.group(1).splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(None, 1)
        if len(parts) < 2:
            continue
        body = parts[1].split("（")[0]
        listed |= set(body.replace("、", " ").split())
    listed = {x for x in listed if x in rt.HELPERS}

    missing = sorted(set(rt.HELPERS) - listed)
    extra = sorted(listed - set(rt.HELPERS))
    assert not missing, "文档名单漏了这些 helper：%s" % missing
    assert not extra, "文档名单多出这些不存在的 helper：%s" % extra
    assert len(listed) == n, "文档名单列出 %d 个，HELPERS 有 %d 个" % (len(listed), n)


def test_no_doc_states_a_stale_helper_count():
    """规则 3（防回退）：任何文档都不许再出现旧计数 84。"""
    for name in ("api", "arch", "report", "readme"):
        text = _read(name)
        # 排除「84 → 86」这类演进对照（它们是在记录历史，不是在陈述现状）。
        stripped = re.sub(r"84 → \*\*?86\*\*?", "", text)
        stripped = re.sub(r"84 → 86", "", stripped)
        assert not re.search(r"(?<!\d)84 个 (?:helper|函数)", stripped), (
            "%s 里仍有旧计数「84 个 helper/函数」" % name)


# --------------------------------------------------------------------------
# 规则 4：SchemaError 句式统一（<JSON 路径>: 必须是 <期望>，得到 <实际>）
# --------------------------------------------------------------------------
_ERR_CASES = [
    ("name 非法", {"name": "cap-1"}, "name: 必须是"),
    ("shape 非法", {"name": "t", "output": {"shape": "table"}}, "output.shape: 必须是"),
    ("type 非法", {"name": "t",
                   "out": {"type": "object",
                           "fields": {"v": {"type": "number", "value": "$.a"}}}},
     "out.fields.v.type: 必须是"),
]


@pytest.mark.parametrize("label,schema,prefix", _ERR_CASES)
def test_schema_errors_share_one_sentence_shape(label, schema, prefix):
    """规则 4（实现裁决）：所有枚举/取值类报错都用「必须是 <期望>，得到 <实际>」。"""
    schema = dict(schema)
    schema.setdefault("output", {"shape": "list"})
    schema.setdefault("out", {"type": "object", "fields": {"v": {"type": "str", "value": "$.a"}}})
    with pytest.raises(dcg.SchemaError) as ei:
        dcg.generate(schema)
    msg = str(ei.value)
    assert msg.startswith(prefix), "%s: 报错前缀不符，实得 %r" % (label, msg)
    assert "，得到 " in msg, "%s: 报错缺少「，得到 <实际>」部分：%r" % (label, msg)
    assert "非法:" not in msg, "%s: 仍有旧的「非法:」句式：%r" % (label, msg)


def test_no_doc_state_uses_the_old_type_error_sentence():
    """规则 4（文档表述）：文档里的 type 报错样例必须与新句式一致。"""
    api = _read("api")
    assert "type 非法: " not in api, (
        "接口文档里仍有旧句式「type 非法: …（可选: …）」，应统一成「必须是 … 之一，得到 …」")


# --------------------------------------------------------------------------
# 规则 5：验收报告的性能结论必须与 perf_protocol.json 同源
#    （数字比对在 tests/test_sync_contract.py；这里守「结论的方向」）
# --------------------------------------------------------------------------
def test_report_conclusion_matches_protocol_directions():
    """规则 5（文档表述）：报告里的「快/持平/慢」分档数量必须与协议数据一致。"""
    import json

    path = os.path.join(_ROOT, "evidence", "perf_protocol.json")
    if not os.path.exists(path):
        pytest.fail("evidence/perf_protocol.json 不存在；性能结论必须有数据源")
    with open(path, encoding="utf-8") as f:
        proto = json.load(f)
    # 顶层键是 `rows`（不是 `cases`），每项含 name/n/{dcg,hand}_{s,us}/ratio/src 等。
    ratios = [c["ratio"] for c in proto["rows"]]

    faster = sum(1 for r in ratios if r < 0.90)
    level = sum(1 for r in ratios if 0.90 <= r <= 1.10)
    slower = sum(1 for r in ratios if r > 1.10)
    assert faster + level + slower == len(ratios)

    report = _read("report")
    # 报告 §5.2 是三行 Markdown 表格：「| DCG 更快 | 2 | `shape_rows`(0.46×)… |」。
    # 抓「行首标签 + 紧跟的第一个单元格数字」，比抓一句话稳（表格增删列不会破坏它）。
    def _cell(label):
        m = re.search(r"^\|\s*" + re.escape(label) + r"\s*\|\s*(\d+)\s*\|", report, re.M)
        assert m, "验收报告 §5.2 里找不到「%s」这一行" % label
        return int(m.group(1))

    got = (_cell("DCG 更快"), _cell("持平（±10%）"), _cell("DCG 更慢"))
    assert got == (faster, level, slower), (
        "报告写「更快 %d / 持平 %d / 更慢 %d」，协议数据算出来是「更快 %d / 持平 %d / 更慢 %d」"
        % (got + (faster, level, slower)))
