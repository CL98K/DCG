# -*- coding: utf-8 -*-
"""链解析的**唯一权威实现**。
"""
from __future__ import annotations

import re

__all__ = ["parse_path", "parse_chain", "FIELD_REF", "count_symbolic", "chain_text"]

_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_INDEX = re.compile(r"[0-9]+")
_PART = r"(?:[A-Za-z_][A-Za-z0-9_]*|[0-9]+)"
#: 在 Python 源码里**切出**候选 ``$.链`` 文本；合法性一律由 :func:`parse_path` 判断。
FIELD_REF = re.compile(r"\$\.(" + _PART + r"(?:\." + _PART + r")*)")


def parse_chain(chain):
    """**链文本**（不含前导 ``$.``，如 ``"5.a.1"``）-> 段序列；非法返回 ``None``。
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
"""
    if not isinstance(src, str) or not src.startswith("$."):
        return None
    return parse_chain(src[2:])


def count_symbolic(segs) -> int:
    """段序列里**符号段**（``str``）的个数 —— 的判据落在它上面，不是节点深度。
"""
    return sum(1 for seg in segs if isinstance(seg, str))


def chain_text(segs) -> str:
    """段序列 -> 链文本（不含前导 ``$.``），与 :attr:`FIELD_REF` 的捕获组同形。"""
    return ".".join(str(seg) for seg in segs)
