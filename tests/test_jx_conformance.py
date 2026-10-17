# -*- coding: utf-8 -*-
"""jx 一致性：冻结基准 bench/frozen/conformance.json 的 50 例（契约 §3 / §4.4）。

**用例不复制粘贴**，直接读冻结文件，避免以后漂移。

由于 ``dcg/runtime.py`` 属于 parser-dev 的写入域、与 jx 并行开发，本文件内置一个
**最小 IR 解释器**（实现契约 §4.4 的 undefined 语义），用它把编译出来的 IR 跑出结果。
它验证的是"IR 是否表达了正确的语义"，而不是 codegen/runtime 的实现质量 —— 后者由
task-3 的端到端测试独立复核。

有 ``jsonata`` 包时额外做交叉复核（``importorskip``）；官方探针对复合对象表达式有已知
缺陷（``ValueError: Only JSON types ...``），命中即记录跳过，不让它污染结果。
"""
from __future__ import annotations

import json
import pathlib

import pytest

from dcg.jx import Unsupported, compile_expr

FIXTURE = pathlib.Path(__file__).resolve().parents[1] / "bench" / "frozen" / "conformance.json"


# ---------------------------------------------------------------------------
# 本地 IR 解释器（契约 §4.4 的 undefined 语义唯一实现，仅测试用）
# ---------------------------------------------------------------------------
class _Undef:
    __slots__ = ()

    def __repr__(self):
        return "undefined"

    def __bool__(self):
        return False


UNDEF = _Undef()


def _jstr(v):
    if v is True:
        return "true"
    if v is False:
        return "false"
    if v is None:
        return "null"
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


def _truthy(v):
    if v is UNDEF or v is None or v is False or v == "" or v == 0:
        return False
    if isinstance(v, list) and not v:
        return False
    return True


def _arith(op, a, b):
    if a is UNDEF or b is UNDEF:
        return UNDEF
    if a is None or b is None:
        raise TypeError(f"JSONata: null 不能参与 {op!r} 运算")
    return {"+": lambda: a + b, "-": lambda: a - b, "*": lambda: a * b,
            "/": lambda: a / b, "%": lambda: a % b}[op]()


def _index(a, idx):
    if a is UNDEF:
        return UNDEF
    if not isinstance(idx, int):
        return UNDEF
    if isinstance(a, list):
        return a[idx] if -len(a) <= idx < len(a) else UNDEF
    return a if idx == 0 else UNDEF


def _call(fname, args):
    if fname == "$not":
        return UNDEF if args[0] is UNDEF else (not _truthy(args[0]))
    if fname == "$exists":
        return args[0] is not UNDEF
    if fname == "$count":
        a = args[0]
        return 0 if a is UNDEF else (len(a) if isinstance(a, list) else 1)
    if fname == "$length":
        return UNDEF if args[0] is UNDEF else len(args[0])
    if fname == "$uppercase":
        return UNDEF if args[0] is UNDEF else args[0].upper()
    if fname == "$lowercase":
        return UNDEF if args[0] is UNDEF else args[0].lower()
    if fname == "$trim":
        return UNDEF if args[0] is UNDEF else args[0].strip()
    if fname == "$string":
        return UNDEF if args[0] is UNDEF else _jstr(args[0])
    if fname == "$number":
        return UNDEF if args[0] is UNDEF else float(args[0])
    if fname == "$boolean":
        return UNDEF if args[0] is UNDEF else _truthy(args[0])
    if fname == "$abs":
        return UNDEF if args[0] is UNDEF else abs(args[0])
    if fname == "$floor":
        import math

        return UNDEF if args[0] is UNDEF else float(math.floor(args[0]))
    if fname == "$ceil":
        import math

        return UNDEF if args[0] is UNDEF else float(math.ceil(args[0]))
    if fname == "$round":
        return UNDEF if args[0] is UNDEF else float(round(args[0]))
    if fname == "$sqrt":
        import math

        return UNDEF if args[0] is UNDEF else math.sqrt(args[0])
    if fname == "$power":
        return UNDEF if UNDEF in args else args[0] ** args[1]
    if fname == "$split":
        return UNDEF if args[0] is UNDEF else (list(args[0]) if not args[1] else args[0].split(args[1]))
    if fname == "$join":
        return UNDEF if args[0] is UNDEF else args[1].join(_jstr(x) for x in args[0])
    if fname == "$contains":
        return UNDEF if UNDEF in args else args[1] in args[0]
    if fname == "$substring":
        a, start = args[0], int(args[1])
        end = None if len(args) < 3 else int(args[2])
        if a is UNDEF:
            return UNDEF
        return a[start:] if end is None else a[start : start + end]
    if fname == "$substringBefore":
        if UNDEF in args:
            return UNDEF
        i = args[0].find(args[1])
        return args[0][:i] if i >= 0 else args[0]
    if fname == "$substringAfter":
        if UNDEF in args:
            return UNDEF
        i = args[0].find(args[1])
        return args[0][i + len(args[1]) :] if i >= 0 else ""
    if fname == "$replace":
        import re

        if args[0] is UNDEF:
            return UNDEF
        return re.sub(re.escape(str(args[1])), str(args[2]), str(args[0]))
    if fname in ("$sum", "$max", "$min", "$append", "$reverse", "$sort", "$distinct"):
        xs = args[0] if fname != "$append" else args[0] + args[1]
        if fname == "$sum":
            return sum(xs)
        if fname == "$max":
            return max(xs)
        if fname == "$min":
            return min(xs)
        if fname == "$reverse":
            return list(reversed(xs))
        if fname == "$sort":
            return sorted(xs)
        if fname == "$distinct":
            out = []
            for x in xs:
                if x not in out:
                    out.append(x)
            return out
        return xs
    raise NotImplementedError(f"测试解释器未实现 {fname}")


def ev(node, ctx):
    tag = node[0]
    if tag == "lit":
        return node[1]
    if tag == "field":
        return ctx.get(node[1], UNDEF)
    if tag == "root":
        return ctx
    if tag == "var":
        return ctx.get("$" + node[1], UNDEF)
    if tag == "unary":
        op = node[1]
        v = ev(node[2], ctx)
        if op == "not":
            return UNDEF if v is UNDEF else (not _truthy(v))
        if v is UNDEF:
            return UNDEF
        return -v if op == "-" else +v
    if tag == "bin":
        op = node[1]
        a, b = ev(node[2], ctx), ev(node[3], ctx)
        if op in "+-*/%":
            return _arith(op, a, b)
        if op == "&":
            return _jstr("" if a is UNDEF else a) + _jstr("" if b is UNDEF else b)
        if op in ("=", "!="):
            if a is UNDEF or b is UNDEF:
                return False
            return a == b if op == "=" else a != b
        if op in ("<", "<=", ">", ">="):
            if a is UNDEF or b is UNDEF:
                return UNDEF
            return {"<": a < b, "<=": a <= b, ">": a > b, ">=": a >= b}[op]
        if op == "and":
            if a is UNDEF or b is UNDEF:
                return False
            return bool(_truthy(a) and _truthy(b))
        if op == "or":
            return bool((False if a is UNDEF else _truthy(a)) or (False if b is UNDEF else _truthy(b)))
        if op == "in":
            if a is UNDEF or b is UNDEF:
                return False
            return a in b
        raise NotImplementedError(op)
    if tag == "cond":
        return ev(node[2], ctx) if _truthy(ev(node[1], ctx)) else ev(node[3], ctx)
    if tag == "index":
        return _index(ev(node[1], ctx), ev(node[2], ctx))
    if tag == "array":
        return [v for v in (ev(x, ctx) for x in node[1]) if v is not UNDEF]
    if tag == "object":
        out = {}
        for k, v in node[1]:
            kk = k if isinstance(k, str) else _jstr(ev(k, ctx))
            vv = ev(v, ctx)
            if vv is not UNDEF:
                out[kk] = vv
        return out
    if tag == "call":
        return _call(node[1], [ev(a, ctx) for a in node[2]])
    raise NotImplementedError(tag)


def norm(v):
    """把本地哨兵归一成 None，便于与冻结基准比较。"""
    if v is UNDEF:
        return None
    if isinstance(v, list):
        return [norm(x) for x in v]
    if isinstance(v, dict):
        return {k: norm(x) for k, x in v.items()}
    return v


# ---------------------------------------------------------------------------
# 冻结基准
# ---------------------------------------------------------------------------
def _load():
    with FIXTURE.open(encoding="utf-8") as fh:
        return json.load(fh)


DATA = _load()
CORE = [c for c in DATA["cases"] if c["group"] == "core"]
EDGE = [c for c in DATA["cases"] if c["group"] == "edge"]
ALL_CASES = DATA["cases"]


def _case_id(case):
    return f'{case["group"]}:{case["expr"]}'


def test_fixture_shape():
    assert len(CORE) == 26, f"core 例数应为 26，实际 {len(CORE)}"
    assert len(EDGE) == 24, f"edge 例数应为 24，实际 {len(EDGE)}"
    assert len(ALL_CASES) == 50
    assert len(DATA["refused"]) == 3
    assert sum(1 for c in ALL_CASES if c["expect"].get("null_or_undefined")) == 7
    assert sum(1 for c in ALL_CASES if "error" in c["expect"]) == 2


@pytest.mark.parametrize("case", ALL_CASES, ids=_case_id)
def test_conformance(case):
    ir = compile_expr(case["expr"])          # 不支持的表达式在这里就会炸
    expect = case["expect"]
    if "error" in expect:
        with pytest.raises(Exception):
            ev(ir, case["ctx"])
        return
    got = norm(ev(ir, case["ctx"]))
    assert got == expect["value"], f'{case["expr"]}: got {got!r}, want {expect["value"]!r}'


def test_undefined_six_semantics():
    """契约 §4.4 六条（+ 额外三条）逐条用 IR 断言，防止基准只覆盖不显式。"""
    empty = {"a": 1}
    # 1. & 不传播，undefined 当空串
    assert norm(ev(compile_expr("miss & 'x'"), empty)) == "x"
    assert norm(ev(compile_expr("'x' & miss"), empty)) == "x"
    # 2. or 的 undefined 参与运算
    assert norm(ev(compile_expr("miss or true"), empty)) is True
    assert norm(ev(compile_expr("false or miss"), empty)) is False
    # 3. = / != 遇 undefined 返回 False
    assert norm(ev(compile_expr("miss = miss"), empty)) is False
    assert norm(ev(compile_expr("miss != miss"), empty)) is False
    # 4. 大小比较遇 undefined 返回 undefined
    for expr in ("miss < 5", "miss <= 5", "miss > 5", "miss >= 5"):
        assert norm(ev(compile_expr(expr), empty)) is None, expr
    # 5. 数组字面量跳过 undefined
    assert norm(ev(compile_expr("[miss, a]"), empty)) == [1]
    assert norm(ev(compile_expr("[miss, miss]"), empty)) == []
    # 6. 对象字面量丢弃 undefined 键
    assert norm(ev(compile_expr("{'k': miss}"), empty)) == {}
    # 额外：null 参与算术是错误
    with pytest.raises(TypeError):
        ev(compile_expr("null + 1"), empty)
    # 额外：$not(miss) == undefined
    assert norm(ev(compile_expr("$not(miss)"), empty)) is None
    # 额外：a[0] 对标量也成立
    assert norm(ev(compile_expr("a[0]"), empty)) == 1


@pytest.mark.parametrize("item", DATA["refused"], ids=lambda i: i["expr"])
def test_refused(item):
    with pytest.raises(Unsupported) as ei:
        compile_expr(item["expr"])
    assert ei.value.loc is not None, "拒绝必须带列号"
    assert f"第 {ei.value.loc[1]} 列" in str(ei.value)


def test_pitfall_map_error_names_the_batch_function():
    """坑 c：$map(xs, $string) 必须报 $map，不能被实参里的 $string 抢答。"""
    with pytest.raises(Unsupported) as ei:
        compile_expr("$map(xs, $string)")
    assert "$map" in str(ei.value)
    assert "$string" not in str(ei.value)


# ---------------------------------------------------------------------------
# 可选交叉复核：jsonata-python 只作探针（契约 D4）
# ---------------------------------------------------------------------------
_JSONATA_KNOWN_DEFECT = "Only JSON types"


def test_cross_check_against_official_jsonata():
    jsonata = pytest.importorskip("jsonata")
    checked = skipped = 0
    problems = []
    for case in ALL_CASES:
        if "error" in case["expect"]:
            continue
        try:
            official = jsonata.Jsonata(case["expr"]).evaluate(case["ctx"])
        except Exception as exc:  # noqa: BLE001 - 官方实现的缺陷要逐类登记
            if _JSONATA_KNOWN_DEFECT in str(exc):
                skipped += 1
                continue
            problems.append(f'{case["expr"]}: 官方探针抛 {type(exc).__name__}: {exc}')
            continue
        checked += 1
        expected = case["expect"]["value"]
        if official != expected:
            problems.append(f'{case["expr"]}: 官方 {official!r} != 冻结 {expected!r}')
    assert not problems, "与官方探针不一致：\n" + "\n".join(problems)
    assert checked + skipped == 48, f"交叉复核覆盖 {checked}+{skipped} != 48"
    if skipped:
        print(f"[探针] 跳过 {skipped} 例（jsonata-python 复合对象缺陷）")
