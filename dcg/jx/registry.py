# -*- coding: utf-8 -*-
"""DCG v2 · 用户函数注册表（DCG 接口实现细节文档.md §3.1）。

``Registry.fn("$myfn", callable)`` 之后，表达式里的 ``$myfn(...)`` 编译成
``("rfunc", "pkg.mod.fn", [args...], loc)``（契约 §1），由 codegen 按 qualname
直接 import 或查表调用。

**不是全局状态**：``compile_expr`` 默认不看任何注册表，只有显式传入 ``registry=``
时才会把未在白名单里的 ``$name`` 解析成 ``rfunc``。这样 ``compile_expr`` 保持
契约 §3.1 的纯函数性质（同输入同输出）。
"""
from __future__ import annotations

import builtins
import re

from .policy import Unsupported

__all__ = ["Registry", "default_registry"]

#: ``__qualname__`` 必须是「点分隔的标识符序列」——产物按 qualname 做
#: ``getattr`` 导入，``<lambda>`` / ``<locals>`` 这类片段导入不到。
_IMPORTABLE_QUALNAME = re.compile(r"^[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*$")


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
        """生成 codegen 用的 ``"pkg.mod.fn"``。

        **必须是模块级可导入的属性路径**：产物里的 ``_x(q, *a)`` 是这样解析的::

            s = q.rfind("."); f = _xr[q] = getattr(__import__(q[:s]), q[s+1:])

        也就是 q 拆成「模块名」+「模块内最后一段属性名」，所以
        ``<lambda>``、``<locals>``、``<listcomp>`` 这类 ``__qualname__``
        片段会让产物在运行期 ``getattr`` 失败（且是 NameError/AttributeError，
        不是 build 期错误）。契约 §4.1 要求 build 期全量报错，因此这里直接拒绝。

        规则：``__qualname__`` 只能是「用点分隔的标识符序列」（如
        ``Class.method``、``_mod.fn``），且首段就是 ``fn`` 本身时也允许；
        含 ``<``/``>``（lambda、嵌套函数、推导式）或非标识符片段一律抛
        ``Unsupported``，消息里给出可操作的修法。
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
                f"（DCG 接口实现细节文档 §3.1）")
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
