"""`value` 与 `expr` 读法一致性门禁（DEFECT-015）。

为什么单独一个文件
------------------
接口文档 §4.4（`DCG 接口实现细节文档.md` L628）承诺：

    `value` 与 `expr` 是同一个规则的两种写法，产物**逐字符相同**；
    `type` 声明只影响类型判定与内联，**不改变读法**。

这条承诺是设计契约的一部分，但 2026-10 的一次实测发现它**没被兑现**：
`_col_value` / `_row_value` 里首段按 `optional` 分裸读/安全链，**后续段却无条件
发 `_ix_get`**，于是 `{"value": "$.a.b"}` 明明 required，却比 `{"expr": "$.a.b"}`
多了一层安全链——缺键时前者静默给 undefined，后者抛 `KeyError`。

**用户裁决（m01258）：选 C —— 承认 optional 的语义差异，精确化契约措辞。**

所以本文件守护的契约是**分档的**：

* **required 字段**（未声明 `optional`、无 `default`）：`value` 与 `expr` 产物
  **逐字符相同**，读法完全一致 —— 这是 DEFECT-015 的现场，必须钉死。
* **`optional` 字段**：允许差异，且必须**单向可解释**：
  `value` 侧缺键落 `None`（`default` 则落默认值），`expr` 侧保留 `undefined`
  （`$exists()` 等需要区分「不存在」与「存在且为 null」）。
* `default` 字段属于 `optional` 这一档（`default` 隐含 `optional` 语义）。

测试策略：**不比较字符串**（行循环与列推导式的形态差异无关契约），而是
* **产物档**：required 组合比源码逐字符相同；
* **语义档**：实跑缺键数据，按 `optional`/`default` 三态分别断言 ——
  required 抛异常、optional 得 `None`、default 得默认值。

这样即使将来产物形态变了（比如换成行循环），语义契约仍然被守住。
"""
from __future__ import annotations

import pytest

import dcg

from dcg.runtime import _U

# 每一种「路径 × 修饰」的组合，都用两种写法各生成一遍并实跑。
# 缺键数据统一用 `{'a': {}}`：`$.a` 存在但 `$.a.b` 的 `b` 段缺失。
_CASES = [
    ("$.a", {}),                            # 单段 required
    ("$.a", {"optional": True}),            # 单段 optional
    ("$.a", {"default": 0}),                # 单段 default
    ("$.zz", {}),                           # 单段 required（顶层缺键）
    ("$.a.b", {}),                          # 多段 required  <- DEFECT-015 现场
    ("$.a.b", {"optional": True}),          # 多段 optional
    ("$.a.b", {"default": 0}),              # 多段 default
    ("$.a.b.c", {}),                        # 三段 required <- 逐段裸读
    ("$.a.b.c", {"optional": True}),        # 三段 optional
    # 嵌套整数下标（用户 m01294 追问的场景）：`$.a.5.1` 是 `r['a'][5][1]`
    ("$.a.5.1", {}),                        # required -> 越界抛 IndexError
    ("$.a.5.1", {"optional": True}),        # optional -> 落 None
    ("$.a.5.1", {"default": 0}),            # default  -> 落 0
]

_TYPES = ["int64", "any", "str", "list"]
_SHAPES = ["dict", "rows", "index"]


def _schema(use_value: bool, path: str, extra: dict, ty: str, shape: str) -> dict:
    field = {"type": ty}
    # 有 default 时类型必须能容纳默认值，否则 build 期类型校验会先拦下，
    # 那样测的就不是读法了。读法本身不受 type 影响（见契约 §4.4）。
    if "default" in extra:
        field = {"type": "any"}
    field["value" if use_value else "expr"] = path
    field.update(extra)
    return {
        "name": "t",
        "input": {"source": "$"},
        "output": {"shape": shape},
        "out": {"type": "object", "fields": {
            # 声明 `a`，让 `$.a.b` 里的 `b` 成为真实下标段。
            # **必须 optional**：缺键用例的行里可能根本没有 `a`（如单段路径
            # `$.a` 的 required 用例），这个辅助字段不该先于被测字段抛 KeyError，
            # 否则测到的就是它而不是 `v`。
            "a": {"type": "any", "value": "$.a", "optional": True},
            "v": field,
        }},
    }


def _generate(use_value: bool, path: str, extra: dict, ty: str, shape: str) -> str:
    return dcg.generate(_schema(use_value, path, extra, ty, shape))


def _run(use_value: bool, path: str, extra: dict, ty: str, shape: str, rows) -> object:
    ns: dict = {}
    src = _generate(use_value, path, extra, ty, shape)
    exec(compile(src, "<t>", "exec"), ns)  # noqa: S102
    return ns["run"](rows)


# --------------------------------------------------------------- 产物档
@pytest.mark.parametrize("path,extra", [(p, e) for p, e in _CASES if not e])
@pytest.mark.parametrize("ty", _TYPES)
@pytest.mark.parametrize("shape", _SHAPES)
def test_required_value_and_expr_are_byte_identical(path, extra, ty, shape):
    """required 字段：`value` 与 `expr` 产物必须逐字符相同（契约 §4.4）。

    DEFECT-015 的现场就在这里：`value` 多段路径被错误地裹进 `_ix_get`，
    而 `expr` 侧是裸链 `r['a']['b']`。
    """
    by_value = _generate(True, path, extra, ty, shape)
    by_expr = _generate(False, path, extra, ty, shape)
    assert by_value == by_expr, (
        "required 字段的 `value` 与 `expr` 产物必须逐字符相同（契约 §4.4）\n"
        f"  path={path!r} type={ty} shape={shape}\n"
        "--- value ---\n" + by_value +
        "\n--- expr  ---\n" + by_expr
    )


# --------------------------------------------------------------- 语义档
def _index_col(product: dict, field: str) -> object:
    """`shape:"index"` 的产物是 `{行号: [列值...]}`；取第一个元素指定字段那一格。"""
    first = product[min(product)]
    names = ["a", "v"]          # `_schema()` 里字段声明序
    return first[names.index(field)]


def _expected(extra: dict, row: dict, path: str) -> object:
    """按「朴素 Python」算出期望值，不复用 codegen 任何逻辑。"""
    if extra.get("optional") or "default" in extra:
        # optional/default：缺键不得抛异常
        try:
            cur = row
            for seg in path.lstrip("$.").split("."):
                cur = cur[seg] if isinstance(cur, dict) else cur[int(seg)]
            return cur
        except (KeyError, IndexError, TypeError):
            return extra.get("default", None)
    # required：缺键必须抛 Python 原生异常
    cur = row
    for seg in path.lstrip("$.").split("."):
        cur = cur[seg] if isinstance(cur, dict) else cur[int(seg)]
    return cur


@pytest.mark.parametrize("use_value", [True, False], ids=["value", "expr"])
@pytest.mark.parametrize("path,extra", _CASES)
def test_missing_key_semantics(use_value, path, extra):
    """缺键/越界语义按 optional/default 三态分开，三态之间不能串味。

    * required：必须抛 Python 原生异常 —— `$.a.b` 缺键抛 `KeyError`，
      `$.a.5.1` 越界抛 `IndexError`。无 undefined 出口，缺键是真实数据错误。
    * optional：**先读成 undefined，再由规则 7 的守卫落成 `None`** —— `value`
      与 `expr` 两种写法、四种输出形状都是这个答案，且**都不抛**。
      （历史：这里曾按「expr 侧保留 undefined 哨兵」断过一段时间，理由是想让
      `$exists()` 能区分「缺值」与「为 null」。但接口文档 §4.2 规则 7 规定
      optional 字段「恰好一次 undefined 判断」，且 `body()` 对**所有** optional
      字段都发 `None if t is _U else t`；只有列级快路径不加守卫时才会漏出哨兵，
      那是 DEFECT-012/017 复现，不是承诺。要观察 undefined 本身请回表达式层
      （`$exists(<expr>)`，见 `tests/test_sentinel_integrity.py`）。）
    * default：必须得默认值，且**不需要显式写 `optional`**（`default` 隐含
      optional 语义 —— `opt = bool(optional) or "default" in spec`）。
    """
    # 数据要让**被测路径的第一段就缺失**，否则测的就不是缺键语义。
    # `$.a` / `$.zz` 这类单段路径：整个行里没有该键。
    # `$.a.b` / `$.a.b.c`：`a` 存在但为空，第二段缺失。
    # `$.a.5.1`：`a` 是空 list，第 5 个元素越界。
    if ".5." in path:
        row = {"a": []}
    elif len(path.lstrip("$.").split(".")) == 1:
        row = {"other": 1}          # 单段：行里根本没有这个键
    else:
        row = {"a": {}}
    required = not extra

    if required:
        with pytest.raises((KeyError, IndexError)):
            _run(use_value, path, extra, "int64", "index", [row])
        return

    got = _index_col(_run(use_value, path, extra, "any", "index", [row]), "v")
    want = _expected(extra, row, path)
    if "default" in extra:
        # `expr` + `default` 与 `value` + `default` 必须同值（DEFECT-016 已修：
        # 无行容器快路径的 `_col_value`/`_row_value` 两处 `expr` 分支此前漏了
        # `_df(...)` 兜底，导致 default 只在 `value` 写法下生效）。
        assert got == want, f"default 应落默认值 {want!r}，实际 {got!r}"
    else:
        # optional 无 default：undefined 由规则 7 的守卫落成 `None`。`value` 与
        # `expr` 两种写法、四种输出形状都必须是这一个答案 —— 只要有一种形状漏出
        # 哨兵（`got is _U`），就是「同一 schema 换个 shape 语义就变」。
        assert got is None, (
            f"{'value' if use_value else 'expr'} + optional 应落 None，实际 {got!r}")


@pytest.mark.parametrize("use_value", [True, False], ids=["value", "expr"])
@pytest.mark.parametrize("optional,default", [(True, None), (False, 0)])
def test_default_implies_optional(use_value, optional, default):
    """`default` 隐含 optional 语义：不写 `optional: True` 也走安全链。

    用户 m01294 追问的场景：`{"value": "$.a.5.1", "default": 0}`（**没有 optional**）
    在越界时必须落 `0`，而不是抛 `IndexError`。

    **value 与 expr 两侧同值**（DEFECT-016 已于用户 m01422 裁决后修复：`default`
    是字段级落值，与 `value`/`expr` 哪种写法无关 —— 接口文档 §4.4 第 7 条写的就是
    「设了 default 的字段编译期常量替换 `_df(<expr>,<default>)`」，未限定写法）。
    """
    row = {"a": []}
    field = {"type": "any", "value" if use_value else "expr": "$.a.5.1", "default": default}
    if optional:
        field["optional"] = True
    schema = {"name": "t", "input": {"source": "$"}, "output": {"shape": "dict"},
              "out": {"type": "object", "fields": {"v": field}}}
    ns: dict = {}
    exec(compile(dcg.generate(schema), "<t>", "exec"), ns)  # noqa: S102
    got = ns["run"]([row])["v"][0]
    assert got == default, f"越界应落默认值 {default!r}，实际 {got!r}"


def test_multisegment_required_emits_no_safe_chain():
    """DEFECT-015 的精确回归：required 多段路径的产物里不能出现 `_ix_get`。

    这条不看 value/expr 是否相等（上面已测），而是直接钉住「required 不出 helper」
    ——即使将来两种写法一起错成同一个样子，这条也会红。
    """
    src = _generate(True, "$.a.b.c", {}, "int64", "dict")
    line = [ln.strip() for ln in src.splitlines() if ln.strip().startswith("return")][0]
    assert "_ix_get" not in line, (
        "required 多段路径不应生成 `_ix_get` 安全链（契约：未声明 optional 一律裸链）\n"
        f"{line}"
    )
    assert "r['a']['b']['c']" in line, f"应为逐段裸读，实际 {line}"


def test_any_and_concrete_type_emit_identical_read():
    """契约锁定：`type` **不参与**路径读值的守卫判定，唯一判据是 `optional`。

    背景（验收报告 D-SHAPE-01）：历史上曾有一版「`type: "any"` 一律 `_ix_get`」的实现，
    后来被回滚——现行契约是 `any` 与 `int64` 等在未声明 `optional` 时**产物完全同形**，
    缺键一律 `KeyError`；要 `undefined` 就必须显式写 `optional: true`。

    这条测试直接钉住「同形」，任何一侧被单独改动都会立刻变红。
    """
    def v_segment(type_name, optional=False):
        """取产物里 `'v'` 那一段（辅助字段 `a` 自带守卫，不能拿来判 `v`）。"""
        extra = {"optional": True} if optional else {}
        src = _generate(True, "$.zz", extra, type_name, "dict")
        line = [ln.strip() for ln in src.splitlines() if ln.strip().startswith("return")][0]
        marker = "'v':"
        assert marker in line, f"产物里找不到 v 字段: {line}"
        return line.split(marker, 1)[1]

    required = {t: v_segment(t) for t in ("any", "int64", "str", "list")}
    first = required["any"]
    for t, seg in required.items():
        assert seg == first, (
            f"required 下 type={t!r} 的读值片段与 type='any' 不同——"
            f"契约要求 type 不影响读值形态（只有 optional 影响）\n"
            f"any: {first}\n{t}: {seg}"
        )
    assert "r['zz']" in first, f"required 应为裸链，实际 {first}"
    assert "_ix_get" not in first and ".get(" not in first, (
        f"required 的 v 片段不应生成任何守卫，实际 {first}"
    )

    # optional 下：any 与具体类型同样必须同形（都走守卫）
    optional = {t: v_segment(t, optional=True) for t in ("any", "int64")}
    assert optional["any"] == optional["int64"], (
        "optional 下 type='any' 与 'int64' 的读值片段不同\n"
        f"any: {optional['any']}\nint64: {optional['int64']}"
    )
    assert optional["any"] != first, "optional 必须与 required 分叉（前者带守卫）"


# ------------------------------------------------- optional 在快路径下也落 None
#: 无行容器快路径（`shape` dict/rows + 单字段）由 `_col_value` / `_row_value`
#: 发射，不走行循环里 `body()` 发的 `x=None if t is _U else t`。
#: 那两处曾漏了这条守卫，于是 optional 字段把 `undefined` 哨兵漏进产物里
#: （`_ix_get(r,'v')` 而非 `None if (_v:=...) is _U else _v`）—— DEFECT-017。
_FAST_PATH_SHAPES = ["rows", "dict"]


def _single_field_schema(use_value: bool, path: str, extra: dict, shape: str) -> dict:
    """单字段 schema：这是 `_columns_from_source()` / `_flat_row()` 的适用条件。"""
    field = {"type": "any", "value" if use_value else "expr": path}
    field.update(extra)
    return {
        "name": "t",
        "input": {"source": "$.rows"},
        "output": {"shape": shape},
        "out": {"type": "object", "fields": {"v": field}},
    }


@pytest.mark.parametrize("use_value", [True, False], ids=["value", "expr"])
@pytest.mark.parametrize("shape", _FAST_PATH_SHAPES)
@pytest.mark.parametrize("path", ["$.v", "$.p.q"])
def test_optional_falls_to_none_on_fast_path(use_value, shape, path):
    """DEFECT-017 精确回归：快路径下 optional 缺键必须落 `None`，不得漏出 `undefined`。

    这是 `shape:"dict"` 与 `shape:"rows"` 都受影响的**跨形状语义**问题：
    同一份 schema 只要同时满足「单字段 + 无行容器」，守卫就丢了。
    """
    schema = _single_field_schema(use_value, path, {"optional": True}, shape)
    src = dcg.generate(schema)
    line = [ln.strip() for ln in src.splitlines() if ln.strip().startswith("return")][0]
    assert "None if (_v:=" in line, (
        f"optional 的快路径产物必须带 None 守卫（DEFECT-017）\n{line}"
    )
    ns: dict = {}
    exec(compile(src, "<t>", "exec"), ns)  # noqa: S102
    got = ns["run"]({"rows": [{"other": 1}]})
    leaf = got["v"][0] if shape == "dict" else got[0]["v"]
    assert leaf is None, f"optional 缺键应落 None，实际 {leaf!r}"
    assert leaf is not _U


def test_optional_const_does_not_crash_single_field_rows():
    """`const` + `optional` + 单字段 rows：修前 `_row_value` 对 const 直接
    `self._expr_node(spec)` 拿到 None 再 `emit(None)`，build 期就崩
    `TypeError: 'NoneType' object is not subscriptable`。"""
    schema = _single_field_schema(True, "$.v", {"const": 7, "optional": True}, "rows")
    schema["out"]["fields"]["v"].pop("value")
    ns: dict = {}
    exec(compile(dcg.generate(schema), "<t>", "exec"), ns)  # noqa: S102
    assert ns["run"]({"rows": [{}]}) == [{"v": 7}]
