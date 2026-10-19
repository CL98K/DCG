# -*- coding: utf-8 -*-
"""DCG v2 · 用户函数注册表（接口实现细节文档.md §3.1）。

``Registry.fn("$myfn", callable)`` 之后，表达式里的 ``$myfn(...)`` 编译成
``("rfunc", "pkg.mod.fn", [args...], loc)``（契约 §1），由 codegen 按 qualname
直接 import 或查表调用。

**不是全局状态**：``compile_expr`` 默认不看任何注册表，只有显式传入 ``registry=``
时才会把未在白名单里的 ``$name`` 解析成 ``rfunc``。这样 ``compile_expr`` 保持
契约 §3.1 的纯函数性质（同输入同输出）。
"""
from __future__ import annotations

import builtins

__all__ = ["Registry", "default_registry"]


class Registry:
    """``$name -> callable`` 的确定性查表。"""

    __slots__ = ("_fns",)

    def __init__(self):
        self._fns: dict[str, object] = {}

    # -- 写入 -------------------------------------------------------------
    def fn(self, name: str, callable) -> None:
        """注册 ``$name``。``name`` 必须形如 ``"$myfn"``。

        形参名 ``callable`` 是契约 §3.1 的签名，会遮蔽同名内置函数 ——
        所以这里必须走 :func:`builtins.callable`，否则 ``callable(callable)``
        会把**被注册的函数**当校验器调用（注册 lambdas 时直接炸）。
        """
        if not isinstance(name, str) or not name.startswith("$") or len(name) < 2:
            raise ValueError(f'Registry.fn: 名字必须形如 "$myfn"，收到 {name!r}')
        if not builtins.callable(callable):
            raise TypeError(f"Registry.fn({name!r}): 第二个参数必须可调用")
        self._fns[name] = callable

    def unregister(self, name: str) -> None:
        self._fns.pop(name, None)

    # -- 读取 -------------------------------------------------------------
    def get(self, name: str):
        """取回可调用对象；未注册返回 ``None``。"""
        return self._fns.get(name)

    def qualname(self, name: str) -> str:
        """生成 codegen 用的 ``"pkg.mod.fn"``。"""
        f = self._fns[name]
        mod = getattr(f, "__module__", None) or "__main__"
        qn = getattr(f, "__qualname__", None) or getattr(f, "__name__", repr(f))
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
