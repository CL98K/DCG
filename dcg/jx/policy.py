# -*- coding: utf-8 -*-
"""DCG v2 · jx 政策层。
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
ROW_FUNCS = frozenset(
    {
        # 字符串（20）
        "$string", "$uppercase", "$lowercase", "$trim", "$pad", "$contains",
        "$split", "$join", "$substring", "$substringBefore", "$substringAfter",
        "$replace", "$length", "$base64encode", "$base64decode",
        "$encodeUrlComponent", "$decodeUrlComponent", "$formatNumber", "$formatBase",
        "$indexOf",
        # 数值（10）
        "$number", "$abs", "$floor", "$ceil", "$round", "$sqrt", "$power",
        "$sum", "$min", "$max",
        # 布尔/其他（4）
        "$boolean", "$not", "$exists", "$type",
        # 数组与对象（12）
        "$count", "$append", "$average", "$reverse", "$sort", "$distinct",
        "$merge", "$keys", "$values", "$lookup", "$spread", "$single",
        # JSONata 2.2.2 新增（A 类行内函数，9 个）
        "$match", "$zip", "$clone", "$encodeUrl", "$decodeUrl",
        "$formatInteger", "$parseInteger", "$fromMillis", "$toMillis",
    }
)

# ---------------------------------------------------------------------------
HIGHER_ORDER = frozenset({"$map", "$filter", "$reduce", "$each", "$sift", "$sort"})

#: 需要比较器的 ``$sort`` 之外，其余高阶函数的调用形态。
_HIGHER_ORDER_BASE = frozenset({"$map", "$filter", "$reduce", "$each", "$sift"})

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

#: 结构性永久拒绝，键是给人和测试看的特征名。
STRUCTURAL = {
    "eval": ("$eval(...)", "运行期求值任意表达式 = 运行期解释器入口 + 注入面"),
    "range": ("区间 a..b", "需要序列语义"),
    "regex": ("正则字面量", "要求拒绝"),
    "bind_pos": ("位置绑定 #", "要求拒绝"),
    "bind_join": ("join 绑定 @", "要求拒绝"),
    "sort": ("排序 ^", "要求拒绝"),
    "transform": ("变换操作符 ~> / |...|", "要求拒绝"),
    "reduce": ("Reduce 路径阶段 {k:v}", "要求拒绝"),
    "group": ("分组/聚合 {..}", "要求拒绝"),
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
"""
    n = _norm(name)
    if n in STRUCTURAL:  # 见 docstring：对正常输入不可达，保留原因已在上面写明
        return STRUCTURAL[n][1]
    if n == "$eval":
        return STRUCTURAL["eval"][1]
    return None


def is_higher_order(name: str) -> bool:
    return _norm(name) in HIGHER_ORDER


def is_nondeterministic(name: str) -> bool:
    return _norm(name) in NONDETERMINISTIC
