# -*- coding: utf-8 -*-
"""链解析的**唯一权威实现**（契约 §3.7 / Lead 裁决 2026-10-17）。

全项目只有这里认识"链"的语法：``$.`` + 段序列，每段是标识符 ``[A-Za-z_][A-Za-z0-9_]*``
或十进制下标 ``[0-9]+``。JSONata 路径编译、反引号段分析、codegen **都必须**调
:func:`parse_path`，不得各写一套正则或第二套解析。

本模块**只解析**：``$.5.a.1.b`` 会成功返回 ``[5, "a", 1, "b"]``。是否"符号段过多"
（§6.1 深路径不支持）由调用方（路径编译层）按段序列自行判定 —— 否则反引号段的报错
理由会从"深路径不支持"退化成"不是合法链"，掩盖真实问题。
"""
from __future__ import annotations

import re

__all__ = ["parse_path", "parse_chain", "FIELD_REF", "count_symbolic", "chain_text"]

_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_INDEX = re.compile(r"[0-9]+")
_PART = r"(?:[A-Za-z_][A-Za-z0-9_]*|[0-9]+)"
#: 在 Python 源码里**切出**候选 ``$.链`` 文本；合法性一律由 :func:`parse_path` 判断。
#: 放在这里是为了让"链长什么样"只有一个定义处（compiler 直接 import 它）。
FIELD_REF = re.compile(r"\$\.(" + _PART + r"(?:\." + _PART + r")*)")


def parse_chain(chain):
    """**链文本**（不含前导 ``$.``，如 ``"5.a.1"``）-> 段序列；非法返回 ``None``。

    codegen 拿到的通常是链文本，请走这个入口（或用 :func:`dcg.jx.parse_path_chain`
    直接拿 IR —— 它非法即抛 ``Unsupported``，不会静默回退）。
    """
    if not isinstance(chain, str) or not chain:
        return None
    segs = []
    for part in chain.split("."):
        if _INDEX.fullmatch(part):
            segs.append(int(part))
        elif _IDENT.fullmatch(part):
            segs.append(part)
        else:
            return None
    return segs


def parse_path(src):
    """``"$.5.a.1"`` -> ``[5, "a", 1]``；不是 ``$.`` 起头的**合法**链则返回 ``None``。

    合法 = ``$.`` 后是点分段的段序列，每段为标识符或十进制下标；空段、非法字符、
    ``$`` 单字符、``$.`` 后为空一律 ``None``。

    **注意**：本函数只认**带 ``$.`` 前缀**的完整引用。链文本（``"5.a.1"``）要走
    :func:`parse_chain`；若 codegen 把链文本直接喂进来会拿到 ``None``，此时**不得**
    ``else (chain,)`` 静默回退成"单个字段名"（契约 §6.1 要求非法链 build 期报错）。
    """
    if not isinstance(src, str) or not src.startswith("$."):
        return None
    return parse_chain(src[2:])


def count_symbolic(segs) -> int:
    """段序列里**符号段**（``str``）的个数 —— §6.1 的判据落在它上面，不是节点深度。

    ``[5, "a", 1]`` -> 1；``["a", "b"]`` -> 2（≥2 即多层路径，由调用方按需处理）。
    """
    return sum(1 for seg in segs if isinstance(seg, str))


def chain_text(segs) -> str:
    """段序列 -> 链文本（不含前导 ``$.``），与 :attr:`FIELD_REF` 的捕获组同形。"""
    return ".".join(str(seg) for seg in segs)
