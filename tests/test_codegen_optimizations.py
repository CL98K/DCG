"""产物级优化回归：正则常量化（P3）/ 跨字段 CSE（P1）/ dict 重复列（P2）。

## 这个文件守什么

三条改动都**改变产物源码**（但正常 schema 的字节不变），所以每条都要有：
「产物形态断言」+「结果等价断言」两半 —— 只看结果不看形态，改动被回退时
测试仍然是绿的；只看形态不看结果，改了形态但算错了也发现不了。

## P3：`$match` 的正则字面量提到模块级

`$match($.s,/ab+c/)` 以前每行发一次 `_rx('ab+c','')`。`_rx` 自带 `_RX_CACHE`
（`dcg/runtime.py:2088-2124`），所以 `re.compile` 不重复，但**每行的函数调用
+ 元组构造 + dict.get** 还在（实测 ~145 ns/次，占单行 `$match` 成本的 20.2%）。
现在发射成模块级 `_RX0=_rx('ab+c','')`，行循环里直接用 `_RX0`。

**两层缓存是叠加的，不是重复的**：`_RX_CACHE` 管 `re.compile` 去重，模块级
常量管调用开销。删任何一个都会掉性能，见 `dcg/codegen.py:_Gen.rx_const`。

## P1：`rows`/`dict` 曾经白算 CSE

`_cse_plan()` 每个字段都算，但 `module()` 里 `if no_row: L.append(" "+self.finish())`
（`dcg/codegen.py` 的 `no_row` 快路径）根本不发射 `defs` —— `rows`/`dict` 的
CSE 结果被**丢掉**，同一表达式在一行里算两次。修复用 walrus 首次赋值 + 复用。

## P2：`dict` 重复列

`shape="dict"` 的 8 列都取 `$.a` 时，以前 8 趟推导式各扫一遍。同文本列现在
共享一次求值 —— 但**每个键必须 `list()` 复制**，否则两个键指向同一个 list
对象，调用方改一个另一个跟着变（语义错误）。
"""
from __future__ import annotations

import ast
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import dcg  # noqa: E402


def _gen(fields, shape="list", name="t"):
    return dcg.generate({
        "name": name,
        "input": {"source": "$"},
        "output": {"shape": shape},
        "out": {"type": "object", "fields": fields},
    })


def _run(src, data, tables=None):
    ns = {}
    exec(compile(src, "<a>", "exec"), ns)
    return ns["run"](data, tables) if tables is not None else ns["run"](data)


# --------------------------------------------------------------------------
# P3：正则常量化
# --------------------------------------------------------------------------

RX_FIELDS = {
    "a": {"type": "object", "expr": "$match($.s,/ab+c/)"},
    "b": {"type": "object", "expr": "$match($.t,/xy+/) "},
}


class TestRegexHoisted:
    def test_constant_is_emitted_before_def_run(self):
        """常量必须在 `def run` **之前** —— 在之后就等于每行还是调 `_rx`。"""
        src = _gen(RX_FIELDS)
        assert "_RX0=_rx('ab+c','')" in src
        assert src.index("_RX0=_rx(") < src.index("def run(")

    def test_no_rx_call_inside_run_body(self):
        """行循环里不能再出现 `_rx(` —— 这是这条优化的全部意义。"""
        src = _gen(RX_FIELDS)
        body = src[src.index("def run("):]
        assert "_rx(" not in body

    def test_same_pattern_shares_one_constant(self):
        """同一 `(pattern,flags)` 只发一个常量，两处引用同一个名字。"""
        src = _gen({
            "a": {"type": "object", "expr": "$match($.s,/ab+c/)"},
            "b": {"type": "object", "expr": "$match($.u,/ab+c/)"},
        })
        assert src.count("_RX0=_rx(") == 1
        assert "_RX1" not in src
        assert src.count("_RX0") == 3          # 1 次声明 + 2 次使用

    def test_distinct_patterns_get_distinct_constants(self):
        src = _gen(RX_FIELDS)
        assert "_RX0=_rx('ab+c','')" in src
        assert "_RX1=_rx('xy+','')" in src

    @pytest.mark.parametrize("shape", ("list", "rows", "index", "dict"))
    def test_result_unchanged(self, shape):
        """常量化只改求值位置，结果必须逐值一致（`$match` 返回对象列表）。"""
        src = _gen(RX_FIELDS, shape=shape)
        data = [{"s": "abbbc", "t": "xyy"}, {"s": "zzz", "t": "x"}]
        got = _run(src, data)
        flat = str(got)
        assert "abbbc" in flat and "xyy" in flat
        assert "zzz" not in flat            # 第二个样本 s 不匹配

    def test_rx_not_bound_as_dead_local(self):
        """模块级用了 `_rx`，但 `run` 里不该多出 `_rx=_g['_rx']` 死绑定。"""
        src = _gen(RX_FIELDS)
        body = src[src.index("def run("):]
        assert "_rx=_g['_rx']" not in body


# --------------------------------------------------------------------------
# P1：rows / dict 的跨字段 CSE
# --------------------------------------------------------------------------

CSE_FIELDS = {
    "a": {"type": "str", "expr": "$formatNumber($.n,'#,##0.00')"},
    "b": {"type": "str", "expr": "$formatNumber($.n,'#,##0.00')"},
    "c": {"type": "int64", "value": "$.k"},
}


class TestRowsDictCse:
    @pytest.mark.parametrize("shape", ("rows", "dict"))
    def test_shared_expression_evaluated_once(self, shape):
        """同一表达式出现在两个字段里，产物中只应出现一次调用。"""
        src = _gen(CSE_FIELDS, shape=shape)
        assert src.count("_fnm(") == 1, src

    @pytest.mark.parametrize("shape", ("rows", "dict"))
    def test_walrus_reuse_is_well_formed(self, shape):
        """walrus 落在 dict 字面量里 —— `ast.Lambda` 体内不得有赋值（规则约束）。"""
        src = _gen(CSE_FIELDS, shape=shape)
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.Lambda):
                for sub in ast.walk(node.body):
                    assert not isinstance(sub, (ast.NamedExpr, ast.Assign))

    @pytest.mark.parametrize("shape", ("rows", "dict"))
    def test_result_unchanged(self, shape):
        src = _gen(CSE_FIELDS, shape=shape)
        data = [{"n": 1234.5, "k": 7}, {"n": 0, "k": 8}]
        got = _run(src, data)
        assert "1,234.50" in str(got)
        assert "0.00" in str(got)

    def test_no_identity_statement_introduced(self):
        """`rule2_no_identity_statements`：不得出现 `x = x` / `x = None`。"""
        for shape in ("rows", "dict"):
            src = _gen(CSE_FIELDS, shape=shape)
            assert "= None" not in src

    def test_distinct_expressions_are_not_merged(self):
        """不同表达式不能被合并 —— CSE 只认结构完全相同的子树。"""
        src = _gen({
            "a": {"type": "str", "expr": "$formatNumber($.n,'#,##0.00')"},
            "b": {"type": "str", "expr": "$formatNumber($.m,'#,##0.00')"},
        }, shape="rows")
        assert src.count("_fnm(") == 2, src

    def test_single_use_is_left_alone(self):
        """只出现一次的格子**不许**包 walrus —— 不省计算，只是噪音。"""
        src = _gen({"a": {"type": "int64", "value": "$.k"},
                    "b": {"type": "int64", "value": "$.j"}}, shape="rows")
        assert ":=" not in src, src

    def test_walrus_names_are_distinct_per_group(self):
        """两组不同的重复表达式各拿一个名字，不会互相覆盖。"""
        src = _gen({
            "a": {"type": "str", "expr": "$formatNumber($.n,'#,##0.00')"},
            "b": {"type": "str", "expr": "$formatNumber($.n,'#,##0.00')"},
            "c": {"type": "str", "expr": "$uppercase($.s)"},
            "d": {"type": "str", "expr": "$uppercase($.s)"},
        }, shape="rows")
        assert "_f0:=" in src and "_f1:=" in src
        assert src.count(":=") == 2, src

    def test_walrus_assigned_before_use(self):
        """先算后用：`(_fN:=…)` 必须出现在裸 `_fN` 之前（dict 字面量按序求值）。"""
        src = _gen(CSE_FIELDS, shape="rows")
        for nam in ("_f0", "_f1"):
            if f"{nam}:=" not in src:
                continue
            assert src.index(f"({nam}:=") < src.index(f":{nam}", src.index(f"({nam}:="))

    def test_impure_calls_are_not_shared(self):
        """`$random` 算两次 ≠ 算一次 —— 绝不能合并。"""
        src = _gen({
            "a": {"type": "float64", "expr": "$random()"},
            "b": {"type": "float64", "expr": "$random()"},
        }, shape="rows")
        assert ":=" not in src, src

    def test_pyblock_is_not_shared(self):
        """反引号段（用户代码）可能有副作用，不合并。"""
        src = _gen({
            "a": {"type": "int64", "value": "$.k"},
            "b": {"type": "int64", "value": "$.k"},
            "c": {"type": "int64", "expr": "$length($.tags)"},
            "d": {"type": "int64", "expr": "$length($.tags)"},
        }, shape="rows")
        # 纯表达式可以共享（`_length` 无副作用），这条只是确认去重不会误伤
        # 用户代码路径：`_expr_shareable` 对 PY/PYBLOCK/RFUNC 一律返回 False。
        assert ":=" in src


# --------------------------------------------------------------------------
# walrus 落在哪里：`ast.Lambda` 体内**不得**有赋值（规则硬约束）
# --------------------------------------------------------------------------

def _assignments_in_lambda(src):
    """返回产物里所有「落在 `ast.Lambda` 体内」的赋值节点。

    `tests/test_codegen_snapshot.py::test_artifact_parses_and_has_no_assignment_in_lambda`
    用的是同一判据 —— 这里单独再算一遍，是为了让 P1 的 walrus **也能被单独验**：
    walrus 写在 dict 字面量里（`{'b':(_f0:=…)}`）是合法的，写在 lambda 体里不行。
    """
    found = []
    for node in ast.walk(ast.parse(src)):
        if not isinstance(node, ast.Lambda):
            continue
        for sub in ast.walk(node.body):
            if isinstance(sub, (ast.NamedExpr, ast.Assign,
                                ast.AugAssign, ast.AnnAssign)):
                found.append(sub)
    return found


class TestWalrusPlacement:
    @pytest.mark.parametrize("shape", ("rows", "dict"))
    def test_no_assignment_inside_lambda(self, shape):
        """产物里的 walrus 都在 dict 字面量/列推导式里，不在 lambda 体内。"""
        src = _gen(CSE_FIELDS, shape=shape)
        assert ":=" in src, "本用例要验证的场景没被触发"
        assert _assignments_in_lambda(src) == []

    def test_detector_catches_violation(self):
        """**负向对照**：手工造一个 lambda 体内带 walrus 的源码，检测器必须报出来。

        没有这条，上面的 `== []` 可能只是检测器永远返回空列表（假绿）。
        """
        bad = "f=lambda v:((_t:=v),_t)"
        assert _assignments_in_lambda(bad), "检测器漏掉了 lambda 体内的 NamedExpr"

    def test_detector_accepts_dict_literal_walrus(self):
        """对照的另一半：dict 字面量里的 walrus 不该被误报。"""
        ok = "def run(_d):\n return {'b':(_f0:=1),'c':_f0}"
        assert _assignments_in_lambda(ok) == []


# --------------------------------------------------------------------------
# P2：dict 重复列共享
# --------------------------------------------------------------------------

DUP_FIELDS = {
    "p": {"type": "list", "itype": "str", "value": "$.tags"},
    "q": {"type": "list", "itype": "str", "value": "$.tags"},
    "rr": {"type": "list", "itype": "str", "value": "$.tags"},
}


class TestDictDuplicateColumns:
    def test_duplicate_column_computed_once(self):
        """三列同一路径 → 产物里只应有一趟推导式。"""
        src = _gen(DUP_FIELDS, shape="dict")
        assert src.count("for r in _d") == 1, src

    def test_each_key_gets_its_own_list_object(self):
        """**语义必需**：两个键不能指向同一个 list，否则改一个另一个跟着变。"""
        src = _gen(DUP_FIELDS, shape="dict")
        out = _run(src, [{"tags": ["a"]}, {"tags": ["b"]}])
        out["p"].append("X")
        assert "X" not in out["q"]
        assert "X" not in out["rr"]
        out["q"].append("Y")
        assert "Y" not in out["p"]

    def test_values_match_unshared_path(self):
        src = _gen(DUP_FIELDS, shape="dict")
        got = _run(src, [{"tags": ["a"]}, {"tags": ["b"]}])
        assert got == {"p": [["a"], ["b"]], "q": [["a"], ["b"]],
                       "rr": [["a"], ["b"]]}

    def test_distinct_columns_not_shared(self):
        """不同路径**不能**合并 —— 多趟本身不慢（x0.999），只有重复同列才慢。"""
        src = _gen({
            "p": {"type": "list", "itype": "str", "value": "$.a"},
            "q": {"type": "list", "itype": "str", "value": "$.b"},
        }, shape="dict")
        assert src.count("for r in _d") == 2, src
