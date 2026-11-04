# -*- coding: utf-8 -*-
"""DCG v2 · 用户函数注册表。
"""
from __future__ import annotations

import builtins
import re

from .policy import Unsupported

__all__ = ["Registry", "default_registry"]

#: ``__qualname__`` 必须是「点分隔的标识符序列」——产物按 qualname 做
_IMPORTABLE_QUALNAME = re.compile(r"^[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*$")


class Registry:
    """``$name -> callable`` 的确定性查表。"""

    __slots__ = ("_fns",)

    def __init__(self):
        self._fns: dict[str, object] = {}

    # -- 写入 -------------------------------------------------------------
    def fn(self, name: str, func) -> None:
        """注册 ``$name``。``name`` 必须形如 ``"$myfn"``。
"""
        if not isinstance(name, str) or not name.startswith("$") or len(name) < 2:
            raise ValueError(f'Registry.fn: 名字必须形如 "$myfn"，收到 {name!r}')
        if not builtins.callable(func):
            raise TypeError(f"Registry.fn({name!r}): 第二个参数必须可调用（非可调用对象）")
        self._fns[name] = func

    def unregister(self, name: str) -> None:
        self._fns.pop(name, None)

    # -- 读取 -------------------------------------------------------------
    def get(self, name: str):
        """取回可调用对象；未注册返回 ``None``。"""
        return self._fns.get(name)

    def qualname(self, name: str) -> str:
        """生成 codegen 用的 ``"pkg.mod.fn"`` / ``"pkg.mod.Class.method"``。
"""
        f = self._fns[name]
        mod = getattr(f, "__module__", None) or "__main__"
        qn = getattr(f, "__qualname__", None) or getattr(f, "__name__", None)
        if not isinstance(mod, str) or not mod:
            raise Unsupported(
                f"$name 注册函数", None,
                f"Registry 里 {name} 所在的模块名不可用（{mod!r}），"
                f"产物无法按 qualname 导入")
        if not isinstance(qn, str) or not _IMPORTABLE_QUALNAME.match(qn):
            raise Unsupported(
                f"{name}(...)",
                None,
                f"Registry 注册的函数 {name} 的 qualname 是 {qn!r}，"
                f"不是模块级可导入的名字（lambda、闭包、局部函数、推导式都无法"
                f"被产物按 qualname 导入）；请改成模块顶层的 def 函数再注册"
                f"")
        return f"{mod}.{qn}"

    def names(self):
        return tuple(self._fns)

    def __contains__(self, name) -> bool:
        return name in self._fns

    def __getitem__(self, name):
        return self._fns[name]

    def __len__(self) -> int:
        return len(self._fns)

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"Registry({', '.join(sorted(self._fns))})"


#: 给"进程级注册"留的便利单例。**compile_expr 不会隐式使用它**（纯函数要求）。
default_registry = Registry()
