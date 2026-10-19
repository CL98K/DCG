"""Static analyser for 接口实现细节文档.md §4.2 -- the nine hard rules on generated code.

Written by the independent verifier: it decides from the *source text* only, never
from an implementation's self-report.  The analyser is itself validated against a
known-bad fixture -- if it cannot flag known-bad code, its "all clear" on real
products means nothing.

Each check returns (ok: bool, detail: str, observations: list[str]).
"""
from __future__ import annotations

import ast
import re

# maximal row reference chain: r[5]["a"][1] / data[5]['a'] / r["callsign"]
ROW_CHAIN = re.compile(
    r"""(?P<base>\b(?:r|row|data|R|DATA)\b)"""
    r"""(?P<idx>(?:\[\s*(?:\d+|'[^']*'|"[^"]*")\s*\]))"""
    r"""(?P<rest>(?:\[\s*(?:\d+|'[^']*'|"[^"]*")\s*\])*)""",
    re.VERBOSE,
)

# identifiers are split on "_" ; a pure-lowercase component >= 8 chars that is not
# derived from a schema field name is the signature of a random suffix
# (e.g. buffer_bfwyuatnpe / doc_bfwyuatnpe_append).
KNOWN_WORDS = {
    "continue", "function", "isinstance", "setattr", "getattr", "property",
    "optional", "datas", "kwargs", "payload", "buffer", "append", "return",
    "runtime", "fields", "trace", "filter", "single", "generated", "schema",
    "canonical", "undefined", "manifest", "compile", "import", "builtins",
}
# Documented dcg/runtime.py helper names (接口实现细节文档.md §4.4).  Generated code may
# import them by name; they are readable by construction, not random soup.
RUNTIME_WORDS = {
    "add", "concat", "sub", "mul", "div", "mod", "eq", "ne", "lt", "le", "gt",
    "ge", "and", "or", "not", "neg", "upper", "lower", "trim", "str", "num",
    "bool", "abs", "floor", "ceil", "round", "length", "count", "split", "join",
    "substring", "substringbefore", "substringafter", "index", "indexof", "arr",
    "obj", "exists", "typeof", "is_u", "isundefined", "undef", "pad", "contains",
    "replace", "reverse", "sort", "distinct", "base64encode", "base64decode",
    "encodeurlcomponent", "decodeurlcomponent", "formatnumber", "formatbase",
    "number", "boolean", "string", "power", "sqrt", "extend",
}
KNOWN_WORDS |= RUNTIME_WORDS


def _code_lines(src):
    """(lineno, text, indent) for lines that are not blank and not a comment."""
    out = []
    for i, line in enumerate(src.splitlines(), 1):
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        out.append((i, line, len(line) - len(line.lstrip())))
    return out


def _strip_docstrings(src):
    """Source with module/function docstrings blanked out (keeps line count)."""
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return src
    lines = src.splitlines()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) \
                    and isinstance(first.value.value, str):
                for ln in range(first.lineno - 1, (first.end_lineno or first.lineno)):
                    lines[ln] = ""
    return "\n".join(lines)


# --------------------------------------------------------------------------
# statistics
# --------------------------------------------------------------------------
def stats(src, schema=None):
    code = _code_lines(src)
    nodoc = _strip_docstrings(src)
    body = "\n".join(l for l in nodoc.splitlines() if l.strip()
                     and not l.strip().startswith("#"))
    assigns = []
    for i, line, indent in code:
        m = re.match(r"^\s*([A-Za-z_]\w*)\s*=", line)
        if m:
            assigns.append(m.group(1))
    loops = [i for i, l, ind in code if re.match(r"^\s*(for|while)\b", l)]
    appends = len(re.findall(r"(?:\.append\(|\w*_append\()", src))
    return {
        "chars_total": len(src),
        "chars_nodoc": len(nodoc),
        "chars_code": len(body),
        "lines_total": len(src.splitlines()),
        "lines_code": len(code),
        "assign_targets": assigns,
        "n_assign": len(assigns),
        "n_loop": len(loops),
        "n_append": appends,
        "row_chains": _row_chains(src),
    }


def _row_chains(src):
    """{chain_prefix: [lineno, ...]} for every row subscription *prefix*.

    CONTRACT §4.2 rule 1 is about prefixes: `r[5]["a"]` appearing as `r[5]['a'][1]`
    and `r[5]['a'][2]` is the same prefix evaluated twice, and the fix is to hoist
    it to the row head once.  So every prefix of every maximal chain is counted.
    """
    hits = {}
    for i, line in enumerate(_strip_docstrings(src).splitlines(), 1):
        code = line.split("#", 1)[0]
        for m in ROW_CHAIN.finditer(code):
            head = m.group("base") + m.group("idx")
            hits.setdefault(head, []).append(i)
            for sub in re.finditer(r"\[\s*(?:\d+|'[^']*'|\"[^\"]*\")\s*\]", m.group("rest")):
                head += sub.group(0)
                hits.setdefault(head, []).append(i)
    return hits


# --------------------------------------------------------------------------
# the nine rules
# --------------------------------------------------------------------------
def rule1_global_rowhead_unpack(src, schema=None):
    """Every referenced row subscript *prefix* is evaluated exactly once."""
    chains = _row_chains(src)
    dup = {c: ls for c, ls in chains.items() if len(ls) > 1}
    obs = [f"{c} x{len(ls)} @ lines {ls}" for c, ls in sorted(dup.items())]
    return (not dup,
            "所有行下标链前缀各取一次" if not dup else f"{len(dup)} 条链前缀被重复求值",
            obs)


def rule2_no_identity_statements(src, schema=None):
    """No `x = x` and no `x = None` stubs."""
    bad = []
    for i, line, indent in _code_lines(src):
        code = line.split("#", 1)[0].strip()
        m = re.match(r"^([A-Za-z_]\w*)\s*=\s*(.+?)\s*$", code)
        if not m:
            continue
        lhs, rhs = m.group(1), m.group(2)
        if rhs == lhs:
            bad.append(f"line {i}: 恒等赋值 `{code}`")
        elif rhs == "None":
            bad.append(f"line {i}: 空桩 `{code}`")
    return (not bad, "无恒等赋值/空桩" if not bad else f"{len(bad)} 处", bad)


def rule3_no_passthrough_buffer(src, schema=None):
    """规则 ③：管的是**输出构造路径**上的中转，不管输入适配与字段别名。

    台账（Lead 精确定义，2026-10-17 裁决；本判定器与 CONTRACT §4.2 同口径）：
      算   ：`doc_x = buffer_x['doc']`（**局部构造**的容器被再解包）、
             `doc_x_append = doc_x.append`（append 中转别名）、
             `buffer_x = {'doc': []}`（只为再解包而建、自身再无其他用途的容器）。
      不算 ：`_in = _in['index']` / `datas = datas['index']` —— **输入承载形式的适配**
             （输入适配，不在输出路径上）；
             `hour = _a` / `hourA = _a1` / `_a1 = _a[1]` —— **字段别名物化**，是反引号段
             或后续表达式按名引用该字段的必然结果，不是"只用于 return 的中转"。

    判据：只有 RHS 的基名是**本源码里用字面量构造出来的局部容器**（`y = {...}`）时，
    `x = y['k']` 才算输出路径上的中转；基名是行变量/字段链的绑定一律豁免。

    口径修正前 CAP 记 4 处（含 `datas = datas['index']`），修正后 3 处；
    A 版 M0 由 1 处降为 0 处。
    """
    bad = []
    # locals built from a literal container in THIS source: the middle-layer signature
    dict_locals = set()
    for _i, line, _ind in _code_lines(src):
        code = line.split("#", 1)[0].strip()
        m = re.match(r"^([A-Za-z_]\w*)\s*=\s*\{", code)
        if m:
            dict_locals.add(m.group(1))

    for i, line, indent in _code_lines(src):
        code = line.split("#", 1)[0].strip()
        # element extraction from a *locally built container*: `doc_x = buffer_x['doc']`
        m = re.match(r"^([A-Za-z_]\w*)\s*=\s*([A-Za-z_]\w*)\[\s*['\"][^'\"]+['\"]\s*\]$", code)
        if m:
            if m.group(1) == m.group(2):
                continue  # input adaptation: `_in = _in['index']`
            if m.group(2) not in dict_locals:
                continue  # field alias / row access, not an output-path middle layer
            bad.append(f"line {i}: 中转解包 `{code}`")
            continue
        m = re.match(r"^([A-Za-z_]\w*)\s*=\s*([A-Za-z_]\w*)\.append$", code)
        if m:
            bad.append(f"line {i}: append 中转别名 `{code}`")
    # a container literal that only exists to be unpacked again
    for i, line, indent in _code_lines(src):
        code = line.split("#", 1)[0].strip()
        m = re.match(r"^([A-Za-z_]\w*)\s*=\s*\{\s*['\"][^'\"]+['\"]\s*:\s*\[\s*\]\s*\}$", code)
        if m and re.search(r"\b%s\s*\[" % re.escape(m.group(1)), src):
            bad.append(f"line {i}: 只被再次解包的容器 `{code}`")
    return (not bad, "无多余中转" if not bad else f"{len(bad)} 处", bad)


def rule4_output_shape_fixed(src, schema=None):
    """Output shape fixed at compile time; no runtime .items()/chain materialisation."""
    bad = []
    for pat, why in ((r"\bitertools\b", "出现 itertools"),
                     (r"\bchain\s*\(", "出现 itertools.chain"),
                     (r"\.items\s*\(\s*\)", "运行期 .items() 物化"),
                     (r"tuple\s*\(\s*\w+\.items", "tuple(doc.items()) 物化"),
                     (r"\bOrderedDict\b", "OrderedDict 物化")):
        if re.search(pat, src):
            bad.append(why)
    shape = None
    if schema:
        shape = (schema.get("output") or {}).get("shape", "list")
    if shape == "list":
        if not re.search(r"^\s*\w+\s*=\s*\[\s*\]", src, re.M) and \
           not re.search(r"\[\s*None\s*\]\s*\*\s*", src):
            bad.append("shape=list 但未见 list 输出构造")
        if not re.search(r"^\s*return\s+\w+", src, re.M):
            bad.append("shape=list 但未见 return")
    return (not bad, "输出形状编译期固定" if not bad else "; ".join(bad), bad)


def rule5_no_rowlevel_try(src, schema=None):
    """No try/except inside the row loop."""
    trys = [(i, ind) for i, l, ind in _code_lines(src)
            if re.match(r"^\s*try\s*:", l)]
    fors = [ind for i, l, ind in _code_lines(src)
            if re.match(r"^\s*for\b", l)]
    bad = []
    if fors:
        find = min(fors)
        bad = [f"line {i}: 循环内 try/except" for i, ind in trys if ind > find]
    return (not bad, "行级 try/except 不在循环内" if not bad else f"{len(bad)} 处", bad)


def rule6_no_isinstance_dispatch(src, schema=None):
    """Type constraint is a compile-time branch: no isinstance chain, no lambda table."""
    bad = []
    if re.search(r"\bisinstance\s*\(", src):
        bad.append("出现 isinstance 分派")
    if re.search(r"\{\s*['\"][^'\"]+['\"]\s*:\s*lambda\b", src):
        bad.append("出现 dict-of-lambda 分派表")
    if re.search(r"\btype\s*\(\s*\w+\s*\)\s*(?:==|is)\b", src):
        bad.append("出现运行期 type() 判断")
    return (not bad, "无 isinstance 分派链" if not bad else "; ".join(bad), bad)


UNDEF_GUARD = re.compile(r"\bis\s+not\s+(?:_U|UNDEF)\b|\bis\s+_U\b|\b_U\s+is\s+not\b")


def _field_names(node):
    """Top-level field names of an `out` / nested node (contract §2)."""
    names = set()
    if isinstance(node, dict) and isinstance(node.get("fields"), dict):
        for fname, spec in node["fields"].items():
            names.add(fname)
    return names


def _field_level_guards(src, schema):
    """Map undefined guards to the *schema field* they guard.

    verifier-dev 修正（2026-10-17）：初版把整个模块里的 `is _U` 计数与 optional
    字段数相比 —— 会被算术 helper 内部的 undefined 预检（`if a is _U or b is _U`）
    喂饱，从而在"optional 字段根本没生成"时给出假 PASS（实测 D9 就是这样漏过去的）。
    现在只看**字段赋值行**（及其紧邻上一行），即真正的字段级守卫。
    """
    out = ((schema or {}).get("out") or {})
    names = _field_names(out)
    lines = src.splitlines()
    guarded, unguarded = set(), set()
    for i, ln in enumerate(lines):
        m = re.match(r"\s*([A-Za-z_]\w*)\s*=[^=]", ln)
        if not m or m.group(1) not in names:
            continue
        fname = m.group(1)
        if UNDEF_GUARD.search(ln) or (i and UNDEF_GUARD.search(lines[i - 1])):
            guarded.add(fname)
        else:
            unguarded.add(fname)
    return names, guarded, unguarded


def rule7_optional_guard_only(src, schema=None):
    """Only `optional: true` fields get an undefined guard (CONTRACT §4.2 rule 7)."""
    if not schema:
        n_guard = len(UNDEF_GUARD.findall(src))
        return (True, f"守卫 {n_guard} 处（未给 schema，只做计数）", [])

    names, guarded, unguarded = _field_level_guards(src, schema)
    opt_names = {n for n in names if _field_is_optional(schema.get("out"), n)}
    obs = []

    # optional 字段必须**既被产出、又被守卫**
    missing_opt = sorted(opt_names - guarded - unguarded)
    if missing_opt:
        obs.append(f"optional 字段根本没产出（静默丢弃，最严重）: {missing_opt}")
    unguarded_opt = sorted(opt_names & unguarded)
    if unguarded_opt:
        obs.append(f"optional 字段缺少 undefined 守卫: {unguarded_opt}")
    wrongly = sorted(guarded - opt_names)
    if wrongly:
        obs.append(f"非 optional 字段被加了守卫: {wrongly}")

    ok = not obs
    detail = (f"字段级守卫 {sorted(guarded)}，无守卫字段 {sorted(unguarded)}，"
              f"schema optional 字段 {sorted(opt_names)}")
    return ok, detail, obs


def _field_is_optional(out, fname):
    fields = (out or {}).get("fields") or {}
    spec = fields.get(fname)
    return bool(isinstance(spec, dict) and spec.get("optional"))


def _count_optional(node):
    if not isinstance(node, dict):
        return 0
    n = 0
    fields = node.get("fields") or {}
    for spec in fields.values():
        if not isinstance(spec, dict):
            continue
        if spec.get("optional"):
            n += 1
        if isinstance(spec.get("fields"), dict):
            n += _count_optional(spec)
    return n


def _identifiers(src):
    """Identifier names bound in the source (AST, so docstrings/comments are not
    mistaken for names).  Returns None when the source does not parse."""
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return None
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.arg):
            names.add(node.arg)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for a in node.names:
                names.add(a.asname or a.name)
    return names


def rule8_deterministic_names(src, schema=None):
    """Readable, schema-derived names; no random hex soup."""
    bad = []
    if re.search(r"\brandom\b", src):
        bad.append("源码出现 random")
    fields = set()
    if schema:
        fields = set((schema.get("out") or {}).get("fields") or {})
    field_blob = " ".join(fields).lower()
    names = _identifiers(src)
    if names is None:
        names = set(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", src))
    suspects = set()
    for ident in names:
        for part in ident.split("_"):
            if len(part) >= 8 and part.islower() and part not in KNOWN_WORDS \
                    and part not in field_blob:
                suspects.add(part)
    if suspects:
        bad.append("疑似随机变量名: " + ", ".join(sorted(suspects)))
    return (not bad, "变量名确定性可读" if not bad else "; ".join(bad), bad)


def rule9_cython_same_semantics(src_py, src_cy=None, schema=None):
    """cython=True adds cdef annotations without changing semantics."""
    if src_cy is None:
        return False, "未提供 cython=True 产物", []
    bad = []
    if "cdef" not in src_cy:
        bad.append("cython 产物无 cdef 声明")
    return (not bad, "cython 模式有类型标注" if not bad else "; ".join(bad), bad)


CHECKS = [
    ("1 全局行首解包（同一字段下标只取一次）", rule1_global_rowhead_unpack),
    ("2 零恒等语句/空桩", rule2_no_identity_statements),
    ("3 零只用于 return 的中转", rule3_no_passthrough_buffer),
    ("4 输出形状编译期固定", rule4_output_shape_fixed),
    ("5 行级 try/except 移出循环", rule5_no_rowlevel_try),
    ("6 类型约束编译期分支（无 isinstance 链）", rule6_no_isinstance_dispatch),
    ("7 optional 才生成守卫", rule7_optional_guard_only),
    ("8 变量名确定性（无 random）", rule8_deterministic_names),
    ("9 Cython 模式同源类型标注", rule9_cython_same_semantics),
]


def run_all(src, schema=None, src_cy=None):
    """-> [(rule, ok, detail, observations)]"""
    out = []
    for name, fn in CHECKS:
        if fn is rule9_cython_same_semantics:
            ok, detail, obs = fn(src, src_cy, schema)
        else:
            ok, detail, obs = fn(src, schema)
        out.append((name, ok, detail, obs))
    return out


def format_report(src, schema=None, src_cy=None, title="generated source"):
    lines = [f"=== 规则判定: {title} ({len(src)} chars) ==="]
    for name, ok, detail, obs in run_all(src, schema, src_cy):
        lines.append(f"[{'PASS' if ok else 'FAIL'}] {name}: {detail}")
        for o in obs:
            lines.append(f"        - {o}")
    return "\n".join(lines)


def render(src):
    """Pretty source dump with line numbers, for evidence files."""
    return "\n".join(f"{i:4d}| {l}" for i, l in enumerate(src.splitlines(), 1))
