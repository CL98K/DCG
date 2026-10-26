"""required 与安全读共享同一路径前缀时的读法隔离（DEFECT-018 / 020 / 021）。

契约（`DCG 接口实现细节文档.md` §4.4、`README.md` §2.3/§2.8）：

* **required** 字段（未声明 `optional`、无 `default`）**一律裸链**
  —— 缺键是真实数据错误，抛 `KeyError`；
* **optional**/`default` 字段走安全链 —— 缺键得 `undefined`（`value` 侧落
  `None`，`default` 落默认值）。

这两条单独看都对，**交叉时**会互相污染：同一个路径前缀（例如 `$.k`）若既被
required 字段读、又被 optional 字段（或 `py{}` 段、反引号段这类安全消费端）读，
`codegen` 的前缀提升会把它绑定成**一个**局部名，于是两侧共用同一种取法：

* 按安全口径绑定（`_k=_ix_get(R,'k')`）→ required 那格缺键**不再抛 KeyError**，
  而是把 `undefined` 静默放进输出；
* 按裸读口径绑定（`_k=R['k']`）→ optional 那格缺键**抛 KeyError**。

两种都错，所以实现上选了第三条路：**冲突前缀提升成裸读局部名**（DEFECT-022）——
一个局部名只能服务一种缺键语义，但「两种引用并存」不等于「不能提升」：绑成
`_k=R['k']` 后 required 侧语义不变（缺键照抛 `KeyError`），optional 侧从 `_k`
起步派生安全链（`_k.get('k',_U)`），缺键仍是 `undefined`。反过来把局部名绑成
**安全**口径才是必须禁止的：那会把 required 的 `KeyError` 静默吞成哨兵。

口径判定分两层：

1. **声明口径**（DEFECT-021，`_Gen._eff_safe`）：一条路径若在 `out.fields` 里
   只被声明成一种读法，就按声明走（`flight_id` 的 `$.fid` 处处裸读，
   `$exists($.a)` 的 `$.a` 处处安全读）；
2. **引用点口径**（DEFECT-020）：同一条路径被声明成两种读法时（本文件的
   `hard` required / `soft` optional 同读 `$.k`），每个引用点按**自己**当时的
   `self.safe` 走；`plan()` 只给「整棵子树口径一致」的前缀发局部名。

本文件是这条机制的回归门禁：此前它在 `tests/` 下**零覆盖**。

**注意用例刻意用具体类型 `int64` 而不是 `any`**：`type:"any"` 在契约里本身就
是安全读（`any` 值可能是 undefined，裸读会把 undefined 出口掐掉），两格都
`any` 时 `$.k` 只有一种读法、没有冲突可测（`_decl == {('k',): {True}}`）。
"""
from __future__ import annotations

import re

import pytest

import dcg

#: `$.k` 同时被 required 字段（hard）与 optional 字段（soft）引用，两格都是具体
#: 类型 —— 这样才有「required 裸读 vs optional 安全读」的冲突可测。
_SCHEMA = {
    "name": "t",
    "input": {"source": "$"},
    "output": {"shape": "list"},
    "out": {"type": "object", "fields": {
        "hard": {"type": "int64", "value": "$.k"},
        "soft": {"type": "int64", "value": "$.k", "optional": True},
    }},
}


def _src() -> str:
    return dcg.generate(_SCHEMA)


def _run(rows):
    ns: dict = {}
    exec(compile(_src(), "<t>", "exec"), ns)  # noqa: S102
    return ns["run"](rows)


# ------------------------------------------------------------------ 产物档
def test_conflicting_prefix_hoists_as_bare():
    """冲突前缀提升成局部名时，**必须绑成裸读**（DEFECT-022）。

    一个局部名只能服务一种缺键语义，但「两种引用并存」不等于「不能提升」：
    只要绑成裸读 `_k=R['k']`，required 侧的语义就不变（缺键照抛 `KeyError`）；
    optional 侧从 `_k` 起步派生安全链（`.get(k,_U)` / `_ix_get`），缺键仍是
    `undefined`。反过来把局部名绑成安全口径才是**必须禁止**的 —— 那会把
    required 的 `KeyError` 静默吞成哨兵。
    """
    src = _src()
    assert re.search(r"^\s*_k\s*=\s*R\['k'\]\s*$", src, re.M), (
        "required 与 optional 共享的前缀 `('k',)` 若被提升，必须绑成裸读；"
        "绑成 `_ix_get(R,'k')` 会把 required 的缺键语义静默改掉\n" + src
    )
    assert "_k=_ix_get(R,'k')" not in src, src


def test_prefix_is_not_safe_bound():
    """冲突前缀**绝不能**按安全口径绑定（否则 required 缺键不再抛 `KeyError`）。"""
    src = _src()
    assert "_ix_get(R,'k')" not in src, (
        "required 与 optional 共享的前缀被按安全口径绑定，"
        "required 的缺键语义会被静默改掉\n" + src
    )
    assert not re.search(r"\.get\(['\"]k['\"],_U\)\s*$", src, re.M), (
        "冲突前缀被按 `.get` 快链绑定，required 侧缺键不再抛 KeyError\n" + src
    )


def test_required_side_stays_bare():
    """required 那格必须读到裸读语义（契约：缺键抛 `KeyError`）。

    允许两种形态：直接 `R['k']`，或从**裸读**局部名 `_k` 派生（`_k['k']` 不会
    出现，但 `_k` 本身是 `R['k']` 的绑定）。判据是「required 侧不经过安全链」。
    """
    src = _src()
    assert "R['k']" in src, src


def test_optional_side_stays_safe():
    """optional 那格必须仍是安全链（契约：缺键给 `undefined`）。

    安全链的**具体形态不钉死**：`type` 可判定时 codegen 走 `.get(k,_U)` 快链
    （接口 §4.4「主判据的唯一例外」，比 `_ix_get` 快约 2.3 倍），`type:"any"`
    时走 `_ix_get`；两者语义等价。钉死形态会把性能优化误判成回归。

    **必须用「optional 单独出现」的 schema 来测**：本模块的 `_SCHEMA` 里
    `hard` 是 required 的同一路径，缺键时整行在 required 那格就先抛 `KeyError`
    了，optional 那格根本轮不到求值 —— 那是正确行为（见
    `test_missing_key_still_raises_keyerror`），不是安全链失效。
    """
    schema = {
        "name": "t3",
        "input": {"source": "$"},
        "output": {"shape": "list"},
        "out": {"type": "object", "fields": {
            "soft": {"type": "int64", "value": "$.k", "optional": True},
        }},
    }
    src = dcg.generate(schema)
    assert re.search(r"\.get\(['\"]k['\"],_U\)|_ix_get\([^)]*['\"]k['\"]", src), src
    assert not re.search(r"R\['k'\]", src), (
        "optional 那格被发成了裸下标\n" + src)
    ns: dict = {}
    exec(compile(src, "<t3>", "exec"), ns)  # noqa: S102
    assert ns["run"]([{}]) == [[None]]


# ------------------------------------------------------------------ 语义档
def test_missing_key_still_raises_keyerror():
    """缺键时 required 侧必须抛 `KeyError` —— 这正是 DEFECT-018 修掉的静默错误。"""
    with pytest.raises(KeyError):
        _run([{}])


def test_present_key_keeps_both_sides():
    """键存在时两侧都取到值，且顺序按声明序。"""
    assert _run([{"k": 7}]) == [[7, 7]]


def test_safe_side_alone_does_not_leak_undefined():
    """optional 侧在缺键时落 `None`，不把哨兵漏进输出。"""
    # 只取 soft 一格：缺键 -> None（`value` 侧口径）。
    schema = {
        "name": "t2",
        "input": {"source": "$"},
        "output": {"shape": "list"},
        "out": {"type": "object", "fields": {
            "soft": {"type": "any", "value": "$.k", "optional": True},
        }},
    }
    ns: dict = {}
    exec(compile(dcg.generate(schema), "<t2>", "exec"), ns)  # noqa: S102
    assert ns["run"]([{}]) == [[None]]
