# -*- coding: utf-8 -*-
"""v1 describe -> v2 schema 迁移工具（task-4；契约 DCG 接口实现细节文档.md §2、§6）。

v1（``format.py``）与 v2（``dcg``）在**配置形状**上不兼容，这个模块把 v1 的
describe 静态改写成 v2 schema，并且**逐项报告**所有无法自动迁移的项：原因 + 手改建议。
任何被改写语义、被忽略、或无法表达的 v1 键都会出现在报告里，绝不静默丢弃。

用法::

    # 仓库里的 examples/flight.json 是「摊平行字段」版本（--no-wrap-doc）：
    python -m dcg.migrate examples/flight_v1.json -o examples/flight.json \
        --report examples/flight_migration_report.json --check --no-wrap-doc

    # 默认迁移：单组 struct 的文档键映射成结构（out.fields.<组名>，protocol=True 时 type="list" 位置行）
    python -m dcg.migrate examples/flight_v1.json -o flight_struct.json --check

* 输入：v1 describe（dict）。允许被 ``{"describe": {...}}`` / ``{"schema": {...}}``
  包一层；也允许 ``--datas`` 之外的一切 v1 顶层键（``datas``/``data``/``mode``/
  ``struct``/``index``/``_field``/``protocol``/``single``/``filter``/``func``/``debug``）。
* 输出：v2 schema（``name`` / ``input`` / ``output`` / ``out``）+ 迁移报告。
* 报告是**结构化 dict**（``--report FILE`` 落 JSON），也可读的稳定文本打到 stderr；
  退出码：``0`` 全部自动迁移，``3`` 已产出可用 schema 但存在需手改项，``1`` 输入非法。

映射总表（每条都在报告里留痕）：::

    v1                              v2
    protocol=True                   output.shape="dict" + out.fields.<组> =
                                    {"type":"list","fields":{...}}（位置行）
                                    （整篇文档 {"组名": 行列表}，每行是位置 list）
    protocol=False                  output.shape="dict" + out.fields.<组> 具名 object
                                    （整篇文档 {"组名": [{字段: 值}, ...]}）
    single=True                     output.shape="rows"（v1 的 single 是「每行一份文档」
                                    [{"组名": 行}, ...]，不是 v2 output.single 的「只返回
                                    第一行」）
    single=False                    output.single = false（一篇文档包全部行）
    filter="" / None                output.filter = false（v1 无过滤）
    filter="字段名"                  无对应：v2 filter 是布尔，报 manual
    func="pkg.mod.fn"               output.post（v1 的整批后处理 -> v2 post）
    debug=True                      output.trace = true
    datas="$.index"                 input.source = "index"
    index={...}                     无对应（v2 的 shape="index" 语义不同），报 manual
    _field={别名: 路径}               无对应（v2 无别名层）；用在表达式里的别名会被
                                    展开成路径并留痕，未使用的别名报 info
    struct: {"doc": [字段]}           out.fields.doc = {"type":"object","fields":{行字段}}，
                                    文档键 doc 以结构形式保留（--no-wrap-doc 关闭，改摊平）
    struct 多组 / 非单组列表         摊平成同行字段，报 manual（v2 一篇 schema 只产出一个文档）
    type="int"/"str"/...            type="int64"/"str"/...（见 TYPE_MAP）
    value="$.5.a"                   value="$.5.a"（裸字符串在 v2 不是路径）
    value="#.hour.3.2"              expr（反引号内联 Python）`hour[3][2]`
    value="0" / "'abc'"             const
    ext="Python 源码"                expr，整段包进反引号：`` `Python 源码` ``
    value=X 且 ext 引用自身字段名     expr：自引用替换成 X 的表达式文本（报 derived）
    alias / show / const / atom     v2 无对应，报 info/manual（show=false 必须手改）
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import re
import sys

from .schema import parse_value_path

# ---------------------------------------------------------------------------
# v1 形状常量
# ---------------------------------------------------------------------------

#: v1 的 type -> v2 的 type；``""`` 在 v1 是「未声明」，v2 用 ``any``。
TYPE_MAP = {
    "int": "int64", "int64": "int64", "int32": "int32", "long": "int64",
    "float": "float64", "float64": "float64", "double": "float64",
    "str": "str", "string": "str", "text": "str",
    "bool": "bool", "boolean": "bool",
    "list": "list", "array": "list",
    "dict": "object", "object": "object", "map": "object",
    "any": "any", "": "any",
}

#: v1 字段 spec 的合法键（format.py:521/526 读的就是这些）。
SPEC_KEYS = frozenset({"name", "type", "value", "ext", "alias", "show",
                       "const", "atom"})
#: v1 顶层 describe 的键（format.py:452/534-558 读的就是这些）。
TOP_KEYS = frozenset({"datas", "data", "mode", "struct", "index", "_field",
                      "protocol", "single", "filter", "func", "debug",
                      "describe", "schema", "name", "version"})
#: v2 保留的字段名（dcg/schema.py:_check_fname）。
RESERVED_FIELDS = frozenset({"rows", "row", "r", "out", "ap", "_"})
#: Python 内置/关键字：反引号段里用它们是合法 Python，不算「未定义裸名」。
BUILTINS = frozenset("""abs all any ascii bin bool bytearray bytes callable chr
classmethod compile complex delattr dict dir divmod enumerate eval filter float
format frozenset getattr globals hasattr hash help hex id input int isinstance
issubclass iter len list locals map max memoryview min next object oct open ord
pow print property range repr reversed round set setattr slice sorted staticmethod
str sum super tuple type vars zip True False None not and or in is if else
for while lambda return yield from import as with try except finally raise assert
del global nonlocal pass break continue class def async await match case""".split())

_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_FNAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_HASH_REF = re.compile(r"#\.([A-Za-z_][A-Za-z0-9_]*)((?:\.[A-Za-z0-9_]+)*)")
# 注：这里**没有**「$. 链深度」正则 —— 路径长度不限：反引号段里 `$.a.b.c.d` 合法，
# value 侧 `$.data.ID` / `$.data.TASK_TYPE[0]` 也都 validate 通过。
_BT = chr(96)  # backtick


# ---------------------------------------------------------------------------
# 报告
# ---------------------------------------------------------------------------

class Report(object):
    """结构化迁移报告。

    ``kind`` 的含义（决定退出码）：

    * ``mapped``      —— v1 构造在 v2 有直接对应物，逐字迁移。
    * ``derived``     —— 自动迁移，但经过了改写规则（原→新的规则写进 ``reason``）。
    * ``info``        —— 只作说明，不影响产出。
    * ``manual``      —— 已产出 schema，但语义被改写/丢失，**必须人工复核**。
    * ``unsupported`` —— v2 无法表达，已放弃该项，**必须人工处理**。
    """

    KINDS = ("mapped", "derived", "info", "manual", "unsupported")

    def __init__(self, source=None, name=None):
        self.source = source
        self.name = name
        self.items = []

    def add(self, kind, path, v1=None, v2=None, reason="", suggestion=""):
        if kind not in self.KINDS:
            raise ValueError("bad report kind: %r" % (kind,))
        item = {"kind": kind, "path": path, "v1": v1, "v2": v2,
                "reason": reason, "suggestion": suggestion}
        self.items.append(item)
        return item

    def counts(self):
        out = dict((k, 0) for k in self.KINDS)
        for it in self.items:
            out[it["kind"]] += 1
        return out

    @property
    def needs_manual(self):
        return any(it["kind"] in ("manual", "unsupported") for it in self.items)

    def to_dict(self):
        return {"source": self.source, "name": self.name,
                "counts": self.counts(), "needs_manual": self.needs_manual,
                "items": self.items}

    def to_text(self):
        lines = []
        for it in self.items:
            lines.append(("[%s]" % it["kind"]).ljust(14) + it["path"])
            if it["v1"] is not None:
                lines.append(" " * 14 + "v1  : " + str(it["v1"]))
            if it["v2"] is not None:
                lines.append(" " * 14 + "v2  : " + str(it["v2"]))
            if it["reason"]:
                lines.append(" " * 14 + "原因: " + it["reason"])
            if it["suggestion"]:
                lines.append(" " * 14 + "建议: " + it["suggestion"])
        n = self.counts()
        lines.append("")
        lines.append("汇总: " + "  ".join("%s=%d" % (k, n[k]) for k in self.KINDS)
                     + "  -> " + ("需要人工复核" if self.needs_manual else "全部自动"))
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------

def _ident_spans(src):
    """返回 ``[(start, end, name)]``，跳过字符串字面量内部。"""
    spans, i, n, quote = [], 0, len(src), None
    while i < n:
        ch = src[i]
        if quote:
            if ch == "\\":
                i += 2
                continue
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch in ("'", '"'):
            quote = ch
            i += 1
            continue
        if ch.isalpha() or ch == "_":
            j = i
            while j < n and (src[j].isalnum() or src[j] == "_"):
                j += 1
            spans.append((i, j, src[i:j]))
            i = j
            continue
        i += 1
    return spans


def _path_segs(src):
    """``"$.5.a.1"`` -> ``(5, "a", 1)``；非路径返回 None。

    直接复用引擎自己的解析器（dcg/schema.py:55 ``parse_value_path``），这样
    「迁移器判定可迁移」与「validate() 接受」逐字一致：``$.data.TASK_TYPE[0]``
    这类常量下标、以及 ``$.data.ID`` 这类多符号段路径都按引擎的真实行为处理。
    """
    if not isinstance(src, str):
        return None
    s = src.strip()
    if s == "$":
        return ()
    if not s.startswith("$"):
        return None
    try:
        return parse_value_path(s)
    except Exception:
        return None


def _symbolic(segs):
    return sum(1 for s in segs if not isinstance(s, int))


def _literal(text):
    """v1 的 ``ast.literal_eval`` 口径；失败返回 ``(None, 错误串)``。"""
    try:
        return ast.literal_eval(text), None
    except Exception as exc:  # noqa: BLE001 - 报错内容要原样进报告
        return None, "%s: %s" % (type(exc).__name__, exc)


def _json(obj):
    return json.dumps(obj, ensure_ascii=False, sort_keys=True)


# ---------------------------------------------------------------------------
# 表达式改写
# ---------------------------------------------------------------------------

class ExprCtx(object):
    """一次迁移的共享上下文。"""

    def __init__(self, rep):
        self.rep = rep
        self.fields = set()      # 本 schema 已声明的输出字段名
        self.aliases = {}        # _field: 别名 -> v1 路径串
        self.aliases_used = set()
        self.field_order = []    # 声明顺序（用于前向引用检查）


def _expand_hash_refs(expr):
    """v1 ``#.field.i.j`` -> v2 裸名 ``field[i][j]``（契约 §6）。"""
    used = []

    def sub(m):
        name = m.group(1)
        used.append(name)
        idx = "".join("[%s]" % s for s in (m.group(2) or "").split(".") if s)
        return name + idx

    return _HASH_REF.sub(sub, expr), used


def _subst_aliases(expr, ctx, path):
    """把 ``_field`` 别名（裸标识符）替换成它映射的 v1 路径。"""
    out, last, used = [], 0, []
    for start, end, name in _ident_spans(expr):
        if start > 0 and expr[start - 1] in ".#$":
            continue                      # $.a 的 a、属性访问、或已展开的 # 引用
        if name not in ctx.aliases:
            continue
        mapping = ctx.aliases[name]
        used.append(name)
        ctx.aliases_used.add(name)
        out.append(expr[last:start])
        if "," in mapping:
            ctx.rep.add("unsupported", "%s(_field 别名 %s)" % (path, name),
                        v1=mapping,
                        reason="v1 的 _field 允许一个别名映射多路径（逗号分隔），v2 无别名层",
                        suggestion="拆分：为每个路径单独声明一个字段，或直接内联成两个表达式")
            out.append(mapping.split(",")[0].strip())
        else:
            out.append(mapping.strip())
            ctx.rep.add("derived", "%s(_field 别名 %s)" % (path, name),
                        v1=name, v2=mapping.strip(),
                        reason="v2 无 _field 别名层，别名在表达式中就地展开为路径",
                        suggestion="")
        last = end
    out.append(expr[last:])
    return "".join(out), used


def _check_free_names(expr, ctx, path):
    """反引号段里的裸名/``$.`` 链在 v2 是否可解析；问题进报告。"""
    for start, end, name in _ident_spans(expr):
        if start > 0 and expr[start - 1] in ".#$":
            continue
        if end < len(expr) and expr[end] in "'\"" and name in _STR_PREFIX:
            continue                       # f'...' 的 f、r'...' 的 r 是前缀，不是裸名
        if name in BUILTINS or name in ctx.aliases:
            continue
        if name not in ctx.fields:
            ctx.rep.add("manual", "%s(裸名 %s)" % (path, name), v1=name,
                        reason="v2 里裸标识符必须是**本 schema 已声明的字段**；"
                               "这个裸名既不是本 schema 的字段，也不是 _field 别名",
                        suggestion="改写成 $. 路径，或先在 out.fields 里声明对应字段，"
                                   "或做成 Registry/rfunc 函数名")
    # 反引号段里的 $. 链没有深度限制：实测 `$.a.b.c.d` 合法（compile_expr 给出
    # ('py', '$.a.b.c.d', ['a.b.c.d'], ...)，产物是 R['a']['b']['c']['d']），
    # 所以这里不拒绝 ≥2 个符号段的链；只有 f-string 伪路径（v1 syntaxParse 拼路径）
    # 在 v2 没有对应机制。
    if ".f'" in expr or ".f\"" in expr:
        ctx.rep.add("unsupported", "%s(f-string 伪路径)" % path, v1=expr,
                    reason="v1 的 syntaxParse 支持 ``$.data.f'...{len(x)}'`` 这种拼出来的路径；"
                           "v2 没有该机制（且它是逐行走访）",
                    suggestion="把拼路径改成显式字段 / 先用 expr 算出路径所需的字段")


def _chain_text(segs):
    return ".".join(str(s) for s in segs)


# ---------------------------------------------------------------------------
# v1 syntaxParse 的「拼路径」段 -> v2 expr 反引号（``$.链`` 取原始行）
# ---------------------------------------------------------------------------

_PLAIN_SEG = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_STR_PREFIX = frozenset(("f", "r", "b", "u", "fr", "rf", "br", "rb"))
_DIGIT_SEG = re.compile(r"^[0-9]+$")


def _split_v1_path(value):
    """v1 ``format.py:256-273`` 的 ``syntaxParse._split``。

    先按 ``.`` 切，再把从 ``f'`` 开头的段一直拼到以 ``}'`` 结尾的段 —— 这是 v1
    支持「运行期拼路径」的全部机制（``$.data.f'AIRPORTIATA_{len(IATA)}'``）。
    """
    parts = value.split(".")
    out, i = [], 0
    while i < len(parts):
        part = parts[i]
        if part.startswith("f'"):
            while not part.endswith("}'") and i + 1 < len(parts):
                i += 1
                part += "." + parts[i]
        out.append(part)
        i += 1
    return out


def _code_mask(src):
    """``mask[i]`` 为真表示 ``src[i]`` 处在 Python **代码**位置。

    字符串字面量内部为假；f-string 的 ``{...}`` 里为真（那里是代码）。v1 拼路径段里
    别名出现在 f-string 花括号内是常态，所以别名展开必须用这个掩码。
    """
    n = len(src)
    mask = [False] * n
    stack = [["code", 0, "top"]]
    i = 0
    while i < n:
        ctx = stack[-1]
        ch = src[i]
        if ctx[0] == "code":
            prev = src[i - 1] if i else ""
            if (ch == "f" and i + 1 < n and src[i + 1] in "'\""
                    and not (prev.isalnum() or prev == "_")):
                stack.append(["fstr", src[i + 1], 0])
                i += 2
                continue
            if ch in "'\"":
                stack.append(["str", ch, 0])
                i += 1
                continue
            mask[i] = True
            if ctx[2] == "f":
                if ch == "{":
                    if i + 1 < n and src[i + 1] == "{":
                        i += 2
                        continue
                    ctx[1] += 1
                elif ch == "}":
                    if ctx[1] == 0:
                        stack.pop()
                        i += 1
                        continue
                    ctx[1] -= 1
            i += 1
            continue
        if ctx[0] == "fstr":
            if ch == "\\":
                i += 2
                continue
            if ch == ctx[1]:
                stack.pop()
                i += 1
                continue
            if ch == "{":
                if i + 1 < n and src[i + 1] == "{":
                    i += 2
                    continue
                stack.append(["code", 0, "f"])
                i += 1
                continue
            if ch == "}":
                i += 2 if (i + 1 < n and src[i + 1] == "}") else 1
                continue
            i += 1
            continue
        # 普通字符串字面量
        if ch == "\\":
            i += 2
            continue
        if ch == ctx[1]:
            stack.pop()
        i += 1
    return mask


def _subst_aliases_py(src, ctx, path):
    """展开 ``_field`` 别名：字符串字面量外、以及 f-string 花括号内都展开。"""
    mask = _code_mask(src)
    out, used, last, i, n = [], [], 0, 0, len(src)
    while i < n:
        ch = src[i]
        if ch.isalpha() or ch == "_":
            j = i
            while j < n and (src[j].isalnum() or src[j] == "_"):
                j += 1
            name = src[i:j]
            prev = src[i - 1] if i else ""
            if mask[i] and name in ctx.aliases and prev not in ".#$":
                mapping = ctx.aliases[name].strip()
                out.append(src[last:i])
                used.append(name)
                ctx.aliases_used.add(name)
                if "," in mapping:
                    ctx.rep.add("unsupported", "%s(_field 别名 %s)" % (path, name),
                                v1=mapping,
                                reason="v1 的 _field 允许一个别名映射多路径（逗号分隔），"
                                       "v2 无别名层",
                                suggestion="拆分：为每个路径单独声明一个字段，"
                                           "或直接内联成两个表达式")
                    out.append(mapping.split(",")[0].strip())
                else:
                    ctx.rep.add("derived", "%s(_field 别名 %s)" % (path, name),
                                v1=name, v2=mapping,
                                reason="v2 无 _field 别名层；拼路径段里的别名就地展开为路径",
                                suggestion="")
                    out.append(mapping)
                last = j
            i = j
            continue
        i += 1
    out.append(src[last:])
    return "".join(out), used


def _dynamic_path_expr(value, ctx, path):
    """v1 ``$.data.f'...{len(alias)}'`` -> v2 ``expr`` ``$.data[f'...']``。

    v1 ``syntaxParse`` 把 ``f'...'`` / ``[...]`` 段变成 **Python 索引表达式**；v2 的
    反引号段里 ``$.链`` 就是原始行取值（且 f-string 花括号内也生效），所以逐段照搬即可，
    不需要写 Python->JSONata 转换器。不可迁移时返回 ``None``（并尽量留报告）。
    """
    parts = _split_v1_path(value)
    if not parts or parts[0] != "$" or len(parts) == 1:
        return None
    segs = parts[1:]
    if not any(not (_PLAIN_SEG.match(s) or _DIGIT_SEG.match(s)) for s in segs):
        return None
    if not (_PLAIN_SEG.match(segs[0]) or _DIGIT_SEG.match(segs[0])):
        ctx.rep.add("unsupported", path, v1=value,
                    reason="v1 的拼路径以 f-string/方括号段**开头**；v2 反引号段只把 ``$.链``"
                           "当行取值，首段必须是普通段（如 ``$.a[f'...']``）",
                    suggestion="先声明一个 object 字段承接首段，再在它上面做动态索引")
        return None
    head = segs[0]
    text = "$." + head if _PLAIN_SEG.match(head) else "$.%d" % int(head)
    for seg in segs[1:]:
        if _PLAIN_SEG.match(seg):
            text += "[%r]" % seg
            continue
        if _DIGIT_SEG.match(seg):
            text += "[%d]" % int(seg)
            continue
        if seg.startswith("f'"):
            sub, _used = _subst_aliases_py(seg, ctx, path)
            text += "[" + sub + "]"
            continue
        if "[" in seg:
            base, rest = seg[:seg.index("[")], seg[seg.index("["):]
            if base:
                if _PLAIN_SEG.match(base):
                    text += "[%r]" % base
                elif _DIGIT_SEG.match(base):
                    text += "[%d]" % int(base)
                else:
                    ctx.rep.add("unsupported", path, v1=value,
                                reason="v1 路径段 %r 既不是普通段也不是方括号索引，"
                                       "v2 无法逐字承接" % seg,
                                suggestion="把这一段改写成显式字段，或拆成两条路径")
                    return None
            while rest:
                end = rest.find("]")
                if end < 0:
                    ctx.rep.add("unsupported", path, v1=value,
                                reason="v1 路径段 %r 的方括号没有闭合" % seg,
                                suggestion="检查 v1 describe")
                    return None
                inner = rest[1:end].strip()
                rest = rest[end + 1:]
                if inner.isdigit():
                    text += "[%s]" % inner
                elif ":" in inner:
                    ctx.rep.add("unsupported", path, v1=value,
                                reason="v1 的切片下标（%r）在 v2 反引号段里没有对应写法" % seg,
                                suggestion="把切片结果先声明成一个字段，再用 JSONata 函数改写")
                    return None
                else:
                    sub, _used = _subst_aliases_py(inner, ctx, path)
                    text += "[" + sub + "]"
            continue
        ctx.rep.add("unsupported", path, v1=value,
                    reason="v1 路径段 %r 既不是普通段，也不是 f-string/方括号索引" % seg,
                    suggestion="改写成显式字段，或拆成多条路径")
        return None
    _check_free_names(text, ctx, path)
    ctx.rep.add("derived", path, v1=value, v2=_BT + text + _BT,
                reason="v1 的 syntaxParse 把 f-string/方括号段当 Python 索引表达式；"
                       "v2 用 expr 的反引号内联 Python 逐字承接（反引号里的 $.链 = "
                       "原始行取值，f-string 花括号内也生效）",
                suggestion="")
    return _BT + text + _BT


def _expr_from_python(src, ctx, path, origin):
    """v1 的一段 Python 源码 -> v2 ``expr``（反引号内联 Python，契约 §4.3/D5）。"""
    rewritten = src
    hash_used = []
    if "#." in rewritten:
        rewritten, hash_used = _expand_hash_refs(rewritten)
        if hash_used:
            ctx.rep.add("derived", path, v1=src, v2=rewritten,
                        reason="v1 ``#.field`` 变量引用 -> v2 裸字段名 + 常量下标"
                               "（#.hour.3.2 -> hour[3][2]）",
                        suggestion="")
    rewritten, alias_used = _subst_aliases(rewritten, ctx, path)
    _check_free_names(rewritten, ctx, path)
    if _BT in rewritten:
        ctx.rep.add("unsupported", path, v1=src,
                    reason="表达式里本来就有反引号，v2 的反引号内联段无法再包一层",
                    suggestion="把这段逻辑改写成 Registry/rfunc 函数后按名调用")
        return None
    if not rewritten.strip():
        ctx.rep.add("unsupported", path, v1=_json(src),
                    reason="%s 改写后为空" % origin,
                    suggestion="检查 v1 describe 的这个字段")
        return None
    if origin == "ext" and not (hash_used or alias_used):
        ctx.rep.add("derived", path, v1=src, v2=_BT + rewritten + _BT,
                    reason="v1 ext 是一段 Python 源码；v2 用 ``expr`` 的反引号内联 Python 承接"
                           "（契约 D5：ext 已删除，能力走反引号）",
                    suggestion="")
    return _BT + rewritten + _BT


# ---------------------------------------------------------------------------
# 字段 spec -> v2 FieldSpec
# ---------------------------------------------------------------------------

def _v1_type(spec, path, rep):
    raw = spec.get("type", "")
    if raw not in TYPE_MAP:
        rep.add("manual", path + "(type)", v1=raw,
                reason="v1 出现未知 type，已按 v2 的 \"any\" 迁移",
                suggestion='显式改成 v2 类型之一: int64/int32/float64/str/bool/list/object/any')
        return "any"
    out = TYPE_MAP[raw]
    if out != raw:
        rep.add("derived", path + "(type)", v1=raw, v2=out,
                reason="v1 type 名 -> v2 type 名", suggestion="")
    return out


def _field_spec(spec, path, ctx):
    """一个 v1 字段 spec -> v2 FieldSpec（dict）。"""
    rep = ctx.rep
    for key in sorted(set(spec) - SPEC_KEYS):
        rep.add("manual", path + "(未知键 %s)" % key, v1=_json(spec[key]),
                reason="v1 字段 spec 里出现本迁移器不认识的键，已忽略",
                suggestion="确认这个键在 v1 里没有被 format.py 读取（format.py:521/526）")

    if spec.get("alias"):
        rep.add("info", path + "(alias)", v1=_json(spec["alias"]),
                reason="v2 没有 alias：out.fields 的键名就是输出名",
                suggestion="若要保留旧名，请把字段名直接改成 alias")
    if "show" in spec:
        if spec["show"] is False:
            rep.add("manual", path + "(show=false)", v1="show=False",
                    reason="v1 的 show=false 表示「算出但不输出」；v2 **刻意没有**这种机制"
                           "（schema 即输出契约，契约 §2/§6）",
                    suggestion="三选一：(a) 把该表达式内联到引用它的字段里（首选，例6 的 "
                               "_K_QV_1 是死计算可直接删）；(b) 接受它作为正式输出字段；"
                               "(c) 拆成两次 build，中间产物单独构建")
        else:
            rep.add("info", path + "(show=true)", v1="show=True",
                    reason="v2 字段默认即输出，无需 show", suggestion="")
    for key in ("const", "atom"):
        if key in spec:
            rep.add("info", path + "(%s)" % key, v1=_json(spec[key]),
                    reason="v1 的 const/atom 是**取值开关**（format.py:355-360），"
                           "不是数据常量；v2 无对应物，value 已按字面量迁移",
                    suggestion="")

    v2type = _v1_type(spec, path, rep)
    value = spec.get("value", "")
    ext = spec.get("ext")
    if isinstance(value, str):
        value = value.strip()

    if ext:
        if value:
            # v1 语义：字段先按 value 求值并以字段名绑定，然后 ext 在**绑定之后**的
            # 作用域里求值（可以自引用该字段名，如 key: value="$.1", ext="-(key)"）。
            # v2 的 value/const/expr 三键互斥，所以把 ext 里的自引用就地替换成
            # value 的表达式文本，整段仍然进 expr 的反引号内联 Python。
            vtext = _value_text_for_sub(value, ctx, path)
            fname = spec.get("name") or ""
            if vtext is not None and fname and _references_ident(ext, fname):
                rewritten = _replace_ident(ext, fname, vtext)
                rep.add("derived", path + "(value+ext)",
                        v1="value=%s ; ext=%s" % (_json(value), ext), v2=rewritten,
                        reason="v1 的字段先按 value 求值并以字段名绑定，再执行 ext（ext 可自引用"
                               "字段名）；v2 的 value/const/expr 三键互斥，所以把自引用替换成"
                               "value 表达式，整段仍作为 expr 的反引号内联 Python",
                        suggestion="")
                ext = rewritten
            else:
                rep.add("info", path + "(value+ext)", v1=_json(value),
                        reason="v1 字段同时给了 value 和 ext，但 ext 没有引用该字段名；"
                               "v1 的 value 结果会被 ext 的赋值覆盖，v2 只需保留 ext 对应的 expr",
                        suggestion="")
        expr = _expr_from_python(ext, ctx, path + "(ext)", "ext")
        if expr is None:
            return None
        return {"type": v2type, "expr": expr}

    if not value:
        rep.add("unsupported", path, v1="value=''（且无 ext）",
                reason="v1 里 value 为空且没有 ext，等于没有取值来源",
                suggestion='给出 "value"/"const"/"expr" 之一，或从 out.fields 里删掉该字段')
        return None

    if value.startswith("#"):
        expr = _expr_from_python(value, ctx, path + "(value=#)", "value#")
        if expr is None:
            return None
        return {"type": v2type, "expr": expr}

    # v1 的 "$[" 与 "$." 等价（format.py:286-287）
    rewritten = value
    if value.startswith("$["):
        rewritten = "$." + value[2:]
        rep.add("derived", path, v1=value, v2=rewritten,
                reason='v1 的 "$[" 与 "$." 等价（format.py:286-287）', suggestion="")

    segs = _path_segs(rewritten)
    if segs is not None:
        v2path = "$" if not segs else "$." + _chain_text(segs)
        rep.add("mapped", path, v1=value, v2='value "%s"' % v2path,
                reason="v1 $/# 前缀闸门删除后，v2 的 value 必须是 $ 开头的路径",
                suggestion="")
        if _symbolic(segs) >= 2:
            rep.add("info", path + "(多层路径)", v1=value, v2='value "%s"' % v2path,
                    reason="路径长度不限（expr / 反引号 / value 三侧一致）；"
                           "代价是行必须是 dict/对象，list 行会在运行期 TypeError",
                    suggestion="行是 list 时改用嵌套 fields；行是 dict 时可直接用")
        return {"type": v2type, "value": v2path}

    if value.startswith("$"):
        dyn_expr = _dynamic_path_expr(rewritten, ctx, path)
        if dyn_expr is not None:
            return {"type": v2type, "expr": dyn_expr}
        if ".f'" in value or ".f\"" in value:
            rep.add("unsupported", path, v1=value,
                    reason="v1 的 syntaxParse 把 ``$.a.f'...'`` 变成运行期拼出来的路径；"
                           "v2 的 value 只能是编译期常量链，拼路径只能走 expr 的反引号内联 Python",
                    suggestion="手改成 expr，例如 `` `$.data[f'K_{len($.data.K)}']` ``"
                               "（反引号内是任意 Python，$.链 取原始行）")
            return None
        try:
            parse_value_path(value)
            why = "解析器未给出原因"
        except Exception as e:
            why = str(e)
        rep.add("unsupported", path, v1=value,
                reason="v2 的 value 解析器（dcg/schema.py:55 parse_value_path）拒绝了它：%s" % why,
                suggestion='改成 $.a / $.5.a.1 / $.a[0] 形式的常量路径，或改用 expr')
        return None

    literal, err = _literal(value)
    if err is not None:
        rep.add("unsupported", path, v1=value,
                reason="v1 把非 $/# 开头的 value 交给 ast.literal_eval（format.py:282），"
                       "这里同样失败：%s" % err,
                suggestion='写成 JSON 字面量（如 0、"abc"、[1,2]）或改用 expr')
        return None
    rep.add("mapped", path, v1=value, v2="const " + _json(literal),
            reason="v1 的 $/# 前缀闸门：非 $/# 开头 = 字面量常量 -> v2 const",
            suggestion="")
    return {"type": v2type, "const": literal}


def _is_spec(node):
    return isinstance(node, dict) and "name" in node


def _references_ident(expr, name):
    """``expr`` 里是否把 ``name`` 当裸标识符引用（跳过字符串、属性/路径段）。"""
    for start, _end, word in _ident_spans(expr):
        if word != name:
            continue
        if start > 0 and expr[start - 1] in ".#$":
            continue
        return True
    return False


def _replace_ident(expr, name, replacement):
    """把 ``expr`` 里的裸标识符 ``name`` 就地替换成 ``replacement`` 文本。

    跳过字符串字面量、以及 ``.name``/``#name``/``$name`` 这种非裸名出现。
    """
    out, last = [], 0
    for start, end, word in _ident_spans(expr):
        if word != name:
            continue
        if start > 0 and expr[start - 1] in ".#$":
            continue
        out.append(expr[last:start])
        out.append(replacement)
        last = end
    out.append(expr[last:])
    return "".join(out)


def _value_text_for_sub(value, ctx, path):
    """v1 ``value`` 的表达式文本（用于 value+ext 的自引用替换）。"""
    text = value.strip() if isinstance(value, str) else ""
    if not text:
        return None
    if text.startswith("#"):
        rewritten, _used = _expand_hash_refs(text)
        return rewritten
    if text.startswith("$["):
        return "$." + text[2:]
    if text.startswith("$"):
        return text
    literal, err = _literal(text)
    if err is not None:
        return None
    return _json(literal)


def _add_field(fields, name, spec, path, ctx):
    if name in fields:
        # v1 的 mapping 是全局且「后写覆盖先写」：同名声明里最后一次的取值生效；生成码
        # 在 append 之前完成全部赋值，所以两个输出位置都等于最后一次的取值。
        # v2 的 out.fields 不允许重名：先出现的位置改写成最后一次的取值，后出现的位置
        # 用一个唯一名（name_2）保留位置。
        fields[name] = spec
        new = name
        i = 2
        while new in fields or new in RESERVED_FIELDS:
            new = "%s_%d" % (name, i)
            i += 1
        fields[new] = spec
        ctx.field_order.append(new)
        ctx.fields.add(new)
        ctx.rep.add("manual", path, v1=name, v2="%s + %s" % (name, new),
                    reason="v1 的 struct 允许同名重复声明：mapping 后写覆盖先写，生成码里"
                           "同名变量在 append 前最后一次赋值生效，所以两个输出位置都等于"
                           "最后一次的取值；v2 的 out.fields 不允许重名，这里保留两个位置并"
                           "把先出现的位置改写成最后一次的取值",
                    suggestion="核对两条同名声明是否确实同值；若不需要其中一个位置，"
                               "删掉对应字段并重新核对 list 形状下的位置语义")
        return
    fields[name] = spec
    ctx.field_order.append(name)
    ctx.fields.add(name)


def _predeclare_fields(node, ctx):
    """把本层即将产出的字段名先登记进 ``ctx.fields``。

    v2 按依赖拓扑序求值，字段可以引用**后面**声明的字段；而 ``_fields_from``
    是顺序遍历、边迁移边登记名字的。不预登记的话，前向引用会被
    ``_check_free_names`` 误报成「裸名不是已声明字段」。这里只登记名字
    （不建 spec、不改字段顺序）。
    """
    def _name(value):
        if isinstance(value, str) and _FNAME.match(value):
            ctx.fields.add(value)

    if isinstance(node, list):
        for item in node:
            if _is_spec(item):
                _name(item.get("name"))
            elif isinstance(item, dict):
                for key in item:
                    _name(key)
    elif isinstance(node, dict) and not _is_spec(node):
        for key, val in node.items():
            if isinstance(val, list):
                # v1 的 dlist 组摊平进本层，名字也属于本层。
                _predeclare_fields(val, ctx)
            elif isinstance(val, dict) and _is_spec(val):
                _name(val.get("name") or key)
            else:
                _name(key)


def _fields_from(node, path, ctx, into=None):
    """v1 struct 节点 -> v2 out.fields（有序 dict）。

    ``into`` 非 None 时直接往这个 dict 里加字段（用于把 v1 的 dlist 组摊平进
    上一层），这样字段的顺序、重名检测和 ctx.field_order 都只有一次登记。
    """
    rep = ctx.rep
    fields = {} if into is None else into
    _predeclare_fields(node, ctx)
    if isinstance(node, list):
        for i, item in enumerate(node):
            sub = "%s[%d]" % (path, i)
            if _is_spec(item):
                name = item.get("name")
                if not isinstance(name, str) or not _FNAME.match(name):
                    rep.add("unsupported", sub, v1=_json(name),
                            reason="v1 字段名不是合法 Python 标识符，v2 字段名必须是标识符",
                            suggestion="改成 [A-Za-z_][A-Za-z0-9_]* 的名字")
                    continue
                if name in RESERVED_FIELDS:
                    rep.add("unsupported", sub, v1=name,
                            reason="v2 保留这些名字给生成码内建名（rows/row/r/out/ap/_）",
                            suggestion="换个字段名")
                    continue
                spec = _field_spec(item, sub, ctx)
                if spec is not None:
                    _add_field(fields, name, spec, sub, ctx)
            elif isinstance(item, dict):
                for key, val in item.items():
                    nested = _fields_from(val, "%s.%s" % (sub, key), ctx)
                    if nested:
                        _add_field(fields, key, {"type": "object", "fields": nested},
                                   "%s.%s" % (sub, key), ctx)
                        rep.add("derived", "%s.%s" % (sub, key), v1="嵌套 struct",
                                v2='type="object" + fields',
                                reason="v1 靠「list 里出现 dict 就当嵌套」（format.py:509-510）；"
                                       "v2 必须显式 fields（契约 §2 硬规则 2）",
                                suggestion="")
            else:
                rep.add("unsupported", sub, v1=repr(item),
                        reason="v1 struct 列表的元素既不是字段 spec 也不是嵌套组",
                        suggestion="检查 describe")
        return fields

    if isinstance(node, dict):
        if _is_spec(node):
            name = node.get("name")
            spec = _field_spec(node, path, ctx)
            if spec is not None and isinstance(name, str) and _FNAME.match(name):
                _add_field(fields, name, spec, path, ctx)
            return fields
        for key, val in node.items():
            sub = "%s.%s" % (path, key)
            if isinstance(val, list):
                # v1 的 dlist（format.py:524-527）就是「这一行的字段」，无论 struct 里
                # 有几个 list 组，都摊平进当前这一层，字段顺序即输出位置。
                _fields_from(val, sub, ctx, into=fields)
                rep.add("derived", sub, v1='{"%s": [字段...]}' % key,
                        v2="out.fields 行内展开（本层）",
                        reason="v1 的 dlist（format.py:524-527）就是「这一行的字段」；"
                               "v2 把这些字段直接摊平进这一层的 out.fields",
                        suggestion="")
            elif isinstance(val, dict) and _is_spec(val):
                name = val.get("name") or key
                spec = _field_spec(val, sub, ctx)
                if spec is not None:
                    _add_field(fields, name, spec, sub, ctx)
            elif isinstance(val, dict):
                nested = _fields_from(val, sub, ctx)
                if nested:
                    _add_field(fields, key, {"type": "object", "fields": nested}, sub, ctx)
                rep.add("derived", sub, v1="嵌套 struct",
                        v2='type="object" + fields',
                        reason="v1 的「dict 里再出现 dict 就当嵌套」（format.py:511-518）；"
                               "v2 必须显式 fields",
                        suggestion="")
            else:
                rep.add("unsupported", sub, v1=repr(val),
                        reason="v1 struct 的值既不是字段 spec 也不是 dict/list",
                        suggestion="检查 describe")
        return fields

    rep.add("unsupported", path, v1=type(node).__name__,
            reason="v1 struct 必须是 dict 或 list",
            suggestion="检查 describe")
    return fields


# ---------------------------------------------------------------------------
# 顶层迁移
# ---------------------------------------------------------------------------

def _unwrap(raw):
    """允许 {"describe": {...}} / {"schema": {...}} 包一层。"""
    if isinstance(raw, dict):
        for key in ("describe", "schema"):
            inner = raw.get(key)
            if isinstance(inner, dict) and "struct" in inner:
                return inner
    return raw


def _v1_single_reject(describe):
    """v1 的 single=True 在含变量引用/ext 时直接抛 ``SingleModeError``。

    对应 V1/format.py:536-546 的 ``isVar``/``isExt`` 判定：字段里有非空的
    ``ext``，或者 ``value`` 不是以 ``$`` 开头的路径（说明是变量/字段名引用），
    就属于 v1 原样跑不起来的那一类。
    """
    def walk(node):
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "ext" and value:
                    return True
                if walk(value):
                    return True
            return False
        if isinstance(node, list):
            for item in node:
                if not isinstance(item, dict):
                    continue
                value = item.get("value")
                if isinstance(value, str) and value.strip() and not value.strip().startswith("$"):
                    return True
                if walk(item):
                    return True
            return False
        return False

    return walk(describe.get("struct"))


def migrate_describe(describe, name=None, source=None, wrap_doc=None):
    """v1 describe -> ``(v2 schema, Report)``。纯函数，不读写文件。

    当没有任何字段能迁移时返回 ``(None, report)``：报告里保留全部逐项原因，
    调用方可以照常打印/写盘。``describe`` 不是 object 时抛 ``ValueError``。
    """
    rep = Report(source=source)
    if not isinstance(describe, dict):
        raise ValueError("v1 describe 必须是 object，得到 %s" % type(describe).__name__)

    ctx = ExprCtx(rep)
    aliases = describe.get("_field") or {}
    if not isinstance(aliases, dict):
        rep.add("manual", "_field", v1=_json(aliases),
                reason="v1 的 _field 必须是 {别名: 路径} 映射，已忽略",
                suggestion="检查 describe")
        aliases = {}
    ctx.aliases = dict((k, v) for k, v in aliases.items() if isinstance(v, str))

    # ---- name
    if not name:
        name = describe.get("name")
    if not name:
        name = "migrated"
    if not _FNAME.match(name):
        rep.add("manual", "name", v1=_json(name), v2="migrated",
                reason="v2 的 name 必须是 [A-Za-z_][A-Za-z0-9_]*",
                suggestion="用 --name 指定合法名字")
        name = "migrated"

    # ---- protocol / single 先记下来：v2 的形状取决于 struct 是否有文档外层键
    protocol = describe.get("protocol", True)
    single_v1 = bool(describe.get("single", False))

    # ---- filter
    v1_filter = describe.get("filter", "")
    if not v1_filter:
        filter_out = False
        rep.add("derived", "output.filter", v1="filter=%r" % (v1_filter,),
                v2="filter=false",
                reason="v1 的 filter 为空 = 不过滤；v2 的 filter 是布尔"
                       "（默认 true = 丢弃 falsy 行），语义不同名同义",
                suggestion="")
    else:
        filter_out = False
        rep.add("manual", "output.filter", v1="filter=%r" % (v1_filter,),
                v2="filter=false（未迁移）",
                reason="v1 的 filter 是**字段名**（按该字段真值过滤行，如 format.py:558 的 "
                       "expParse）；v2 的 filter 只是布尔（丢弃 falsy 的行）",
                suggestion="要保留按字段过滤的语义，请在 v2 里显式表达："
                           "(a) 加一个该字段的 expr 字段并在 out.fields 里保留它，"
                           "把过滤逻辑写进 expr（如条件返回）；(b) 或改用 output.post "
                           "指向外部函数；(c) 若确实不需要过滤，就接受 filter=false")

    # ---- func -> post / debug -> trace
    post = None
    func = describe.get("func")
    if func:
        if isinstance(func, str) and func.count(".") >= 1:
            post = func
            rep.add("mapped", "output.post", v1="func=%r" % (func,), v2="post=%r" % (func,),
                    reason="v1 的整批后处理函数 -> v2 output.post", suggestion="")
        else:
            rep.add("manual", "output.post", v1="func=%r" % (func,), v2="post=null",
                    reason="v2 的 post 必须是 \"pkg.mod.fn\" 形式的字符串（schema.py:228-231）；"
                           "v1 的 func 可以是一段源码",
                    suggestion="把这批后处理逻辑放进一个可导入模块，再把 func 写成 "
                               "\"你的模块.函数\"")
    trace = bool(describe.get("debug", False))
    if trace:
        rep.add("mapped", "output.trace", v1="debug=True", v2="trace=true",
                reason="v1 debug 开关 -> v2 output.trace（DCG 整体设计方案与架构文档.md §7）", suggestion="")

    # ---- datas -> input.source
    datas = describe.get("datas", describe.get("data"))
    src = "$"
    if isinstance(datas, str) and datas.strip():
        # v1 的 datas 就是「行集合在哪」的路径；v2 的 input.source 用的是
        # 同一套路径语法，所以直接照搬即可（含多级 `$.a.b`）。
        segs = _path_segs(datas)
        if segs is not None and segs:
            parts = []
            for s in segs:
                if isinstance(s, str):
                    parts.append("." + s
                                 if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", s)
                                 else f"[{s!r}]")
                else:
                    parts.append(f"[{s}]")
            src = "$" + "".join(parts)
            rep.add("mapped", "input.source", v1="datas=%r" % (datas,),
                    v2="source=%r" % (src,),
                    reason="v1 datas 是行集合在输入里的路径 -> v2 input.source "
                           "用同一套路径语法（产物 run 会按它先取出子表）",
                    suggestion="")
        else:
            rep.add("manual", "input.source", v1="datas=%r" % (datas,),
                    v2='source="$"',
                    reason="v1 datas 写法无法解析成路径",
                    suggestion="把行集合的位置写成 v2 路径，如 source=\"$.a.b\"；"
                               "输入本来就是行列表时用 source=\"$\"（即默认）")
    elif datas is not None:
        rep.add("info", "input.source", v1="datas=%r" % (datas,),
                v2='source="$"',
                reason="v1 datas 为空 = 直接吃行列表，与 v2 默认一致", suggestion="")

    # ---- index
    if describe.get("index"):
        rep.add("manual", "index", v1=_json(describe["index"]), v2=None,
                reason="v2 **没有** index 键（schema.py 的 TOP_KEYS 会直接报未知键）；"
                       "v2 的 output.shape=\"index\" 是另一种东西：{行号: 行} 的整表输出，"
                       "与 v1 的 index 分组规则不同",
                suggestion="v1 的 index={...} 分组/合并语义在 v2 无对应物；"
                           "若只是要按行号取行，用 shape=\"index\"；否则需要外部预处理")

    # ---- mode
    mode = describe.get("mode")
    if mode:
        rep.add("info", "mode", v1=_json(mode), v2="（无 mode 概念）",
                reason="v1 的 mode 选择 dcg.py 的六套 Template；v2 的产物形状由 "
                       "output.shape/single/filter 编译期决定", suggestion="")

    # ---- struct -> out.fields
    struct = describe.get("struct")
    wrap_group = None      # v1 的输出文档键（struct 只有一个列表组时）
    row_fields = {}
    if struct is None:
        rep.add("unsupported", "out.fields", v1="（没有 struct）",
                reason="v1 describe 里没有 struct，迁移器无法知道要产出哪些字段",
                suggestion='补上 "struct"，或手工写 {"out": {"type": "object", "fields": {...}}}')
    elif isinstance(struct, dict) and len(struct) == 1:
        only_key = next(iter(struct))
        if _FNAME.match(only_key) and isinstance(struct[only_key], list):
            # v1 的 struct 顶层键就是输出文档的键，这正是 v2 的文档级表达：
            # out.fields.<键> = {type:"list", fields:{行字段}}（位置行）
            wrap_group = only_key
            row_fields = _fields_from(struct[only_key], "struct.%s" % only_key, ctx)
        else:
            row_fields = _fields_from(struct, "struct", ctx)
    else:
        row_fields = _fields_from(struct, "struct", ctx)
        if isinstance(struct, dict) and len(struct) > 1:
            rep.add("manual", "struct",
                    v1="多组 struct（%d 个顶层键: %s）" % (len(struct), ", ".join(sorted(struct))),
                    v2='list 组摊平进本层；非 spec 的 dict 嵌套为 type="object"',
                    reason="v1 的 struct 展开规则是隐式的（format.py:501-527）：dlist 组的字段"
                           "被平铺进同一张字段表，组内与组外字段共享同一个命名空间；"
                           "本例有多个顶层组，组边界只能靠人工判断",
                    suggestion="逐组核对：确认哪些组本该平铺成同行字段、哪些本该是嵌套 object；"
                               "核对字段顺序（list 形状下位置即语义）后删掉这条提示。"
                               "多段文档要逐字节还原，v2 的正确做法是「每组一个产物 + 一层组装"
                               "函数」——一个 schema 只产出一篇文档")

    # ---- output.shape / output.single
    #      v1 的输出永远是 {文档键: 行列表}。v2 的 shape="dict" 恰是同构表达：
    #      out 的每个文档键按行聚合成列表；single=True（每行一份文档）用 shape="rows"。
    structural = wrap_group is not None and wrap_doc is not False
    if structural:
        shape = "rows" if single_v1 else "dict"
        rep.add("mapped", "output.shape",
                v1="protocol=%r, single=%r" % (protocol, single_v1),
                v2='shape="%s"' % shape,
                reason='v1 的输出是 {"%s": 行列表}：v2 的 shape="dict" 把 out 的每个文档键'
                       "按行聚合成列表，正好同构；v1 的 single=True 是「每行一份文档」，"
                       'v2 用 shape="rows" 表达' % (wrap_group,),
                suggestion="")
        rep.add("derived", "out.fields.%s" % wrap_group,
                v1="struct 顶层键 %r（v1 的输出文档键）" % (wrap_group,),
                v2=('{"type":"list","fields":{...}}' if protocol
                    else '{"type":"object","fields":{...}}'),
                reason="v1 的 struct 顶层键就是输出文档的键；v2 用 out.fields[%r] 表达该文档键。"
                       % (wrap_group,)
                       + ("protocol=True 时行是位置 list，用 type=\"list\" + fields 按声明顺序输出、"
                          "undefined 落成 None 保位；"
                          if protocol else
                          "protocol=False 时行是 {字段: 值} 对象，用具名 object 即可；"),
                suggestion="")
        rep.add("derived", "output.single",
                v1="single=%r" % (single_v1,),
                v2="single=false（整篇还是逐行由 shape 决定）",
                reason="v1 的 single 不是「只取第一行」：single=True 是每行一份文档"
                       "（[{组名: 行}, ...]），single=False 是一篇文档包全部行；"
                       "v2 分别对应 shape=\"rows\" 与 shape=\"dict\"，output.single 恒为 false",
                suggestion="")
        if single_v1 and _v1_single_reject(describe):
            rep.add("info", "output.single", v1="single=True + 变量引用/ext",
                    v2='shape="rows"',
                    reason="v1 在 single=True 且字段含变量引用或 ext 时会抛 SingleModeError"
                           "（V1/format.py:536-546），该 describe 原样跑不起来；迁移结果按"
                           "「每行一份文档」的意图生成",
                    suggestion="")
        if post:
            rep.add("derived", "output.post",
                    v1="func=%r（作用在整篇文档上）" % (post,),
                    v2="post=%r" % (post,),
                    reason="v1 的 func 与 v2 的 post 都是整批后处理，入参都是整篇文档；"
                           "若 func 内部按行表示（位置 list / 具名 dict）做了假设，需人工核对",
                    suggestion="核对 post 的入参：现在是 {\"%s\": 行列表}，与 v1 一致"
                               % (wrap_group,))
    else:
        shape = "list" if protocol else "rows"
        rep.add("mapped", "output.shape", v1="protocol=%r" % (protocol,),
                v2='shape="%s"' % shape,
                reason="本 describe 没有可还原的文档外层键（struct 不是「单组列表」），"
                       "shape 只表达行本身",
                suggestion="" if protocol else
                           "v2 的 shape=\"rows\" 就是逐行 {字段名: 值} dict，与 v1 同构")
        rep.add("derived", "output.single", v1="single=%r" % (single_v1,),
                v2="single=false",
                reason="v1 的 single=False 是「一篇文档包全部行」；single=True 是逐行文档，"
                       "但本 describe 没有可还原的文档键，只能按整篇输出",
                suggestion="")
        if wrap_group is not None and wrap_doc is False:
            rep.add("manual", "output.post",
                    v1="文档键 %r" % (wrap_group,), v2="post=null（文档键丢失）",
                    reason="--no-wrap-doc：显式要求不还原文档外层键，列表组被摊平成行字段",
                    suggestion="去掉 --no-wrap-doc，让迁移器生成 out.fields[%r] 的文档结构"
                               % (wrap_group,))
        elif single_v1:
            rep.add("manual", "output.single", v1="single=True",
                    v2="single=false（逐行文档未还原）",
                    reason="v1 的 single=True 把整篇文档拆成每行一份并逐份包 struct 的顶层键；"
                           "本 describe 的 struct 不是「单个列表组」，无法自动生成逐行文档",
                    suggestion="若需要，请用 output.post 手写："
                               "wrap(rows) = [{组名: 行} for 行 in rows]")

    # ---- _field 汇总（未使用的别名）
    for alias, mapping in sorted(ctx.aliases.items()):
        if alias not in ctx.aliases_used:
            rep.add("info", "_field.%s" % alias, v1=_json(mapping), v2=None,
                    reason="v2 没有 _field 别名层（schema 即输出契约）；该别名在本 describe "
                           "的表达式里没有被引用",
                    suggestion="丢弃即可；若它代表「算出但不输出」的中间量，"
                               "请参考 show=false 的三条手改路径")

    if not row_fields:
        rep.name = name
        rep.add("unsupported", "out.fields", v1="（没有任何可迁移字段）", v2=None,
                reason="所有字段都被上面的 unsupported/manual 原因丢弃了；v2 的 out.fields "
                       "不允许为空 object（schema.py）",
                suggestion="按上面每条建议先手工修 describe，再重跑迁移；或直接手写 v2 schema")
        return None, rep

    if structural:
        doc_spec = {"type": "list" if protocol else "object", "fields": row_fields}
        out_fields = {wrap_group: doc_spec}
    else:
        out_fields = row_fields

    schema = {"name": name, "input": {"source": src},
              "output": {"shape": shape, "single": False, "filter": filter_out,
                         "post": post, "trace": trace},
              "out": {"type": "object", "fields": out_fields}}
    rep.name = name
    for key in sorted(set(describe) - TOP_KEYS):
        rep.add("manual", key, v1=_json(describe[key]),
                reason="v1 describe 里出现本迁移器不认识的顶层键，已忽略",
                suggestion="确认它在 v1 里没有被 format.py 读取（format.py:532-558）")
    return schema, rep


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def load_describe(path):
    with open(path, "r", encoding="utf-8") as fh:
        raw = json.load(fh)
    return _unwrap(raw)


def _check(schema, build_dir, overwrite):
    """用公开面 validate/generate/build 证明产物可用（绝不碰实现细节）。"""
    import dcg  # 延迟导入：只有 --check/--build-dir 才需要引擎

    norm = dcg.validate(schema)
    src = dcg.generate(norm)
    print("[check] dcg.validate() OK（规范化后的字段数=%d）"
          % len(norm["out"]["fields"]), file=sys.stderr)
    print("[check] dcg.generate() OK（产物 %d 字符）" % len(src), file=sys.stderr)
    if build_dir:
        manifest = dcg.build(norm, build_dir, name=norm["name"], overwrite=overwrite)
        print("[check] dcg.build() OK -> %s" % _json(manifest), file=sys.stderr)
        return manifest
    return None


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="python -m dcg.migrate",
        description="把 v1 format.py 的 describe 迁移成 v2 schema（并逐项报告必须手改的地方）")
    ap.add_argument("input", help="v1 describe 的 JSON 文件")
    ap.add_argument("-o", "--out", default=None,
                    help="v2 schema 输出路径（默认 <输入名>.v2.json；\"-\" 表示 stdout）")
    ap.add_argument("--report", default=None, help="结构化迁移报告的 JSON 输出路径")
    ap.add_argument("--name", default=None, help="v2 schema 的 name（默认取文件名）")
    ap.add_argument("--indent", type=int, default=2, help="JSON 缩进（默认 2）")
    ap.add_argument("--check", action="store_true",
                    help="迁移后用 dcg.validate() + dcg.generate() 自检")
    ap.add_argument("--build-dir", default=None,
                    help="额外用 dcg.build() 真的落盘产物（隐含 --check）")
    ap.add_argument("--overwrite", action="store_true", help="build 时覆盖已有产物")
    ap.add_argument("--quiet", action="store_true", help="不打印文本报告（只写 --report）")
    ap.add_argument("--no-wrap-doc", dest="no_wrap_doc", action="store_true",
                    help="不还原 v1 的文档外层键（默认把 struct 的单组列表映射成 "
                         "out.fields.<组名>，输出仍是 {\"<组名>\": 行列表}）")
    args = ap.parse_args(argv)

    source = os.path.abspath(args.input)
    try:
        describe = load_describe(args.input)
    except (OSError, ValueError) as exc:
        print("[migrate] 无法读取/解析 v1 describe: %s" % exc, file=sys.stderr)
        return 1

    stem = os.path.splitext(os.path.basename(args.input))[0]
    name = args.name or describe.get("name") or stem
    if name.endswith("_v1"):
        name = name[:-3]
    try:
        schema, rep = migrate_describe(describe, name=name, source=source,
                                       wrap_doc=False if args.no_wrap_doc else None)
    except ValueError as exc:
        print("[migrate] %s" % exc, file=sys.stderr)
        return 1

    out_path = args.out
    if out_path is None:
        out_path = os.path.join(os.path.dirname(source) or ".", stem + ".v2.json")

    # 没有 schema 也要先把报告落盘/打印出来，否则用户看不到逐项原因。
    if args.report:
        payload = rep.to_dict()
        with open(args.report, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        print("[migrate] 迁移报告 -> %s" % args.report, file=sys.stderr)
    if not args.quiet:
        print(rep.to_text(), file=sys.stderr)
    if schema is None:
        counts = rep.counts()
        print("[migrate] 没有产出 v2 schema：out.fields 为空（unsupported=%d manual=%d）；"
              "原因见上面的逐项报告" % (counts.get("unsupported", 0), counts.get("manual", 0)),
              file=sys.stderr)
        return 1

    text = json.dumps(schema, ensure_ascii=False, indent=args.indent) + "\n"
    if out_path == "-":
        sys.stdout.write(text)
    else:
        with open(out_path, "w", encoding="utf-8") as fh:
            fh.write(text)
        print("[migrate] v2 schema -> %s（%d 字节）" % (out_path, len(text.encode("utf-8"))),
              file=sys.stderr)

    if args.check or args.build_dir:
        try:
            _check(schema, args.build_dir, args.overwrite)
        except Exception as exc:  # noqa: BLE001 - 自检失败要原样暴露
            print("[check] FAIL %s: %s" % (type(exc).__name__, exc), file=sys.stderr)
            return 1

    return 3 if rep.needs_manual else 0


if __name__ == "__main__":
    sys.exit(main())
