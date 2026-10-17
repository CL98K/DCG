# -*- coding: utf-8 -*-
"""DCG v2 · jx 政策层（CONTRACT.md §3.4 / §3.5）。

这里只有三样东西：

* :class:`Unsupported` —— 公开异常（契约 §3.1），带 ``(feature, loc, msg)``；
  ``str(e)`` 形如 ``"第 12 列: $sum 不可逐行编译（需整批数据求值）"``。
* 逐行可编译函数白名单 ``ROW_FUNCS``（契约 §3.4）。
* 必须整批/非确定/结构性拒绝的黑名单 ``BATCH_ONLY``（契约 §3.5）。

**绝不静默降级**：任何不在白名单里的调用、任何黑名单里的构造，都必须抛
:class:`Unsupported`。

白/黑名单的**重叠**是契约本身的设计：``$sum $max $min $sort $reverse $distinct``
同时在 §3.4（标注"仅限字面量数组"）和 §3.5 里。处置见
:data:`LITERAL_ARRAY_ONLY` —— 只接受字面量数组实参，其余一律按 §3.5 拒绝。
"""
from __future__ import annotations

__all__ = [
    "Unsupported",
    "ROW_FUNCS",
    "BATCH_ONLY",
    "NONDETERMINISTIC",
    "LITERAL_ARRAY_ONLY",
    "BARE_CALL_ALIASES",
    "is_row_func",
    "rejection_reason",
]


class Unsupported(Exception):
    """不能逐行编译的结构 / 函数。

    契约 §3.1::

        class Unsupported(Exception):
            def __init__(self, feature: str, loc, msg: str): ...
            # str(e) 形如: "第 12 列: $sum 不可逐行编译（需整批数据求值）"
    """

    def __init__(self, feature: str, loc=None, msg: str | None = None):
        super().__init__(feature, loc, msg)
        self.feature = feature
        self.loc = loc
        self.msg = msg

    @property
    def col(self):
        return None if self.loc is None else self.loc[1]

    def __str__(self) -> str:
        if self.loc is None:
            where = ""
        else:
            line, col = self.loc
            # 契约 §3.1 的样例是单行表达式，保持"第 N 列"；多行才补行号。
            where = f"第 {col} 列: " if line == 1 else f"第 {line} 行第 {col} 列: "
        tail = "不可逐行编译" if self.msg is None else f"不可逐行编译（{self.msg}）"
        return f"{where}{self.feature} {tail}"


# ---------------------------------------------------------------------------
# 契约 §3.4 —— 逐行可编译函数白名单
# ---------------------------------------------------------------------------
ROW_FUNCS = frozenset(
    {
        # 字符串
        "$string", "$uppercase", "$lowercase", "$trim", "$pad", "$contains",
        "$split", "$join", "$substring", "$substringBefore", "$substringAfter",
        "$replace", "$length", "$base64encode", "$base64decode",
        "$encodeUrlComponent", "$decodeUrlComponent", "$formatNumber", "$formatBase",
        # 数值（$sum/$min/$max 另受 LITERAL_ARRAY_ONLY 约束）
        "$number", "$abs", "$floor", "$ceil", "$round", "$sqrt", "$power",
        "$sum", "$min", "$max",
        # 布尔/其他
        "$boolean", "$not", "$exists", "$type",
        # 数组（$append/$reverse/$sort/$distinct 另受 LITERAL_ARRAY_ONLY 约束）
        "$count", "$append", "$reverse", "$sort", "$distinct",
    }
)

# ---------------------------------------------------------------------------
# 契约 §3.5 —— 必须拒绝
# ---------------------------------------------------------------------------
#: 需要整批数据求值。
BATCH_ONLY = frozenset(
    {
        "$sum", "$max", "$min", "$average", "$sort", "$reverse", "$distinct",
        "$shuffle", "$reduce", "$each", "$sift", "$keys", "$values", "$lookup",
        "$merge", "$spread", "$map", "$filter", "$single", "$position",
    }
)

#: 非确定（同一行不同时刻结果不同）。
NONDETERMINISTIC = frozenset({"$now", "$millis", "$random", "$eval"})

#: 同时在 §3.4 白名单与 §3.5 黑名单里的函数：只有**实参全是字面量数组**时才可逐行编译。
#: （``$count`` 不在此列 —— §3.4 的注释也提到它，但冻结基准
#: `bench/frozen/conformance.json` 的 core 例 `$count(xs)` 要求它对运行期数组成立。）
LITERAL_ARRAY_ONLY = frozenset(
    {"$sum", "$max", "$min", "$append", "$reverse", "$sort", "$distinct"}
)

#: 裸名调用别名 —— 真实 schema 的 `ext` 里写的是 ``str($.x)`` 而不是
#: ``$string($.x)``。裸名**只**在这张表里才
#: 允许被调用；表外的裸名（如字段名 `a(...)`）仍然按"只有 $ 函数可以被调用"拒绝。
BARE_CALL_ALIASES = {
    "str": "$string",
    "int": "$number",
    "float": "$number",
    "bool": "$boolean",
    "len": "$length",
    "abs": "$abs",
}

#: 结构性拒绝（契约 §3.5 "其他结构"），键是给人和测试看的特征名。
STRUCTURAL = {
    "range": ("区间 a..b", "需要序列语义"),
    "regex": ("正则字面量", "契约 §3.5 要求拒绝"),
    "lambda": ("function(){} 高阶字面量", "需整体求值"),
    "bind_pos": ("位置绑定 #", "契约 §3.5 要求拒绝"),
    "bind_join": ("join 绑定 @", "契约 §3.5 要求拒绝"),
    "sort": ("排序 ^", "契约 §3.5 要求拒绝"),
    "transform": ("变换操作符 ~> / |...|", "契约 §3.5 要求拒绝"),
    "reduce": ("Reduce 路径阶段 {k:v}", "契约 §3.5 要求拒绝"),
    "group": ("分组/聚合 {..}", "契约 §3.5 要求拒绝"),
    "path": ("深路径 a.b", "v2 不支持 JSONata 逐行走访（契约 §6.1），请用嵌套 fields"),
}


def _norm(name: str) -> str:
    """统一成带 ``$`` 的写法（``"sum"`` 与 ``"$sum"`` 等价）。"""
    if not name:
        return name
    return name if name.startswith("$") else "$" + name


def is_row_func(name: str) -> bool:
    """是否在 §3.4 白名单里（不含 LITERAL_ARRAY_ONLY 的实参形状检查）。"""
    return _norm(name) in ROW_FUNCS


def rejection_reason(name: str) -> str | None:
    """函数名若必须整体拒绝，返回原因；否则返回 ``None``。

    只做**函数身份**判定，不看实参 —— 这正是原型踩过的坑 (b)：
    必须先判归属再 emit 实参，否则 ``$map(xs, $string)`` 会报出误导性的
    "$string 需要 ctx 绑定" 而不是 "$map 不可逐行编译"。
    """
    n = _norm(name)
    if n in NONDETERMINISTIC:
        return "非确定求值"
    if n in BATCH_ONLY and n not in LITERAL_ARRAY_ONLY:
        return "需整批数据求值"
    return None
