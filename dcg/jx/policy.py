# -*- coding: utf-8 -*-
"""DCG v2 · jx 政策层（接口实现细节文档.md §3.5 / §3.9）。

这里只有三样东西：

* :class:`Unsupported` —— 公开异常（接口实现细节文档 §3.1），带 ``(feature, loc, msg)``；
  ``str(e)`` 形如 ``"第 12 列: $eval(...) 不可逐行编译（运行期求值任意表达式 = …）"``。
* 四张能力表：``ROW_FUNCS``（A 类行内函数）/ ``HIGHER_ORDER``（B 类高阶函数）/
  ``NONDETERMINISTIC``（C 类非确定函数）/ ``STRUCTURAL``（永久拒绝的语法构造）。
* 归属判定函数 :func:`rejection_reason` / :func:`is_row_func`。

**绝不静默降级**：不在任何一张表里的调用一律 ``Unsupported``；永久拒绝的构造
（``$eval``、区间、正则、变换/分组等整批语义）一律 ``Unsupported``。
"""
from __future__ import annotations

__all__ = [
    "Unsupported",
    "ROW_FUNCS",
    "HIGHER_ORDER",
    "NONDETERMINISTIC",
    "STRUCTURAL",
    "BARE_CALL_ALIASES",
    "is_row_func",
    "rejection_reason",
]


class Unsupported(Exception):
    """不能逐行编译的结构 / 函数。

    接口实现细节文档 §3.1::

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
            # 单行表达式保持"第 N 列"；多行才补行号。
            where = f"第 {col} 列: " if line == 1 else f"第 {line} 行第 {col} 列: "
        tail = "不可逐行编译" if self.msg is None else f"不可逐行编译（{self.msg}）"
        return f"{where}{self.feature} {tail}"


# ---------------------------------------------------------------------------
# A 类｜行内函数（45 个，直接发射运行期 helper）
# ---------------------------------------------------------------------------
ROW_FUNCS = frozenset(
    {
        # 字符串（19）
        "$string", "$uppercase", "$lowercase", "$trim", "$pad", "$contains",
        "$split", "$join", "$substring", "$substringBefore", "$substringAfter",
        "$replace", "$length", "$base64encode", "$base64decode",
        "$encodeUrlComponent", "$decodeUrlComponent", "$formatNumber", "$formatBase",
        # 数值（10）
        "$number", "$abs", "$floor", "$ceil", "$round", "$sqrt", "$power",
        "$sum", "$min", "$max",
        # 布尔/其他（4）
        "$boolean", "$not", "$exists", "$type",
        # 数组与对象（12）
        "$count", "$append", "$average", "$reverse", "$sort", "$distinct",
        "$merge", "$keys", "$values", "$lookup", "$spread", "$single",
    }
)

# ---------------------------------------------------------------------------
# B 类｜高阶函数（需要函数字面量）
# ---------------------------------------------------------------------------
HIGHER_ORDER = frozenset({"$map", "$filter", "$reduce", "$each", "$sift", "$sort"})

#: 需要比较器的 ``$sort`` 之外，其余高阶函数的调用形态。
_HIGHER_ORDER_BASE = frozenset({"$map", "$filter", "$reduce", "$each", "$sift"})

# ---------------------------------------------------------------------------
# C 类｜非确定函数（结果不可复现，产物仍可复现）
# ---------------------------------------------------------------------------
NONDETERMINISTIC = frozenset({"$now", "$millis", "$random", "$shuffle"})

#: 裸名调用别名 —— schema 里写 ``str($.x)`` 时归一化为 ``$string``。
BARE_CALL_ALIASES = {
    "str": "$string",
    "int": "$number",
    "float": "$number",
    "bool": "$boolean",
    "len": "$length",
    "abs": "$abs",
}

#: 结构性永久拒绝（接口实现细节文档 §3.9），键是给人和测试看的特征名。
#: 函数身份类拒绝（``$eval``）也放这里，保证「先判归属、再 emit 实参」。
STRUCTURAL = {
    "eval": ("$eval(...)", "运行期求值任意表达式 = 运行期解释器入口 + 注入面"),
    "range": ("区间 a..b", "需要序列语义"),
    "regex": ("正则字面量", "契约 §3.5 要求拒绝"),
    "bind_pos": ("位置绑定 #", "契约 §3.5 要求拒绝"),
    "bind_join": ("join 绑定 @", "契约 §3.5 要求拒绝"),
    "sort": ("排序 ^", "契约 §3.5 要求拒绝"),
    "transform": ("变换操作符 ~> / |...|", "契约 §3.5 要求拒绝"),
    "reduce": ("Reduce 路径阶段 {k:v}", "契约 §3.5 要求拒绝"),
    "group": ("分组/聚合 {..}", "契约 §3.5 要求拒绝"),
    "path": ("不支持的路径形态", "路径只支持 $ 起头、由标识符段与常量下标段组成（长度不限）"),
}


def _norm(name: str) -> str:
    """统一成带 ``$`` 的写法（``"sum"`` 与 ``"$sum"`` 等价）。"""
    if not name:
        return name
    return name if name.startswith("$") else "$" + name


def is_row_func(name: str) -> bool:
    """是否在 A 类白名单里（不看实参形状 —— A 类没有实参限制）。"""
    return _norm(name) in ROW_FUNCS


def rejection_reason(name: str) -> str | None:
    """函数名若必须永久拒绝，返回原因；否则返回 ``None``。

    只做**函数身份**判定，不看实参 —— 这正是原型踩过的坑：
    必须先判归属再 emit 实参，否则 ``$map(xs, $string)`` 会报出误导性的
    "$string 需要 ctx 绑定" 而不是 "$map 需要函数字面量"。
    """
    n = _norm(name)
    if n in STRUCTURAL:
        return STRUCTURAL[n][1]
    if n == "$eval":
        return STRUCTURAL["eval"][1]
    return None


def is_higher_order(name: str) -> bool:
    return _norm(name) in HIGHER_ORDER


def is_nondeterministic(name: str) -> bool:
    return _norm(name) in NONDETERMINISTIC
