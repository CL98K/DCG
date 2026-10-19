"""DCG v2 schema 校验（DCG 接口实现细节文档.md §2）。

硬规则：
1. `"a"` 这种**裸字符串永远不是路径**（`$`/`#` 前缀闸门已废弃）。
2. 嵌套**必须**用 `"fields"` 显式声明（list-of-dict 特例已废弃）。
3. 所有非法形状报**带 JSON 路径的 `SchemaError`**，绝不裸 `KeyError`。

`validate()` 返回**规范化深拷贝**（补齐全部默认值），下游（codegen/ir）只认规范化结果。
"""

import re

#: 生成模块名前缀
NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
#: FieldSpec 字段名（作为 Python 局部名必须合法，且不得与 helper 前缀撞车）
FNAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

TYPES = ("int64", "int32", "float64", "str", "bool", "list", "object", "any")
SHAPES = ("list", "dict", "index", "rows")

VALUE_KEYS = ("value", "const", "expr")
FIELD_KEYS = frozenset({
    "type", "optional", "value", "const", "expr", "fields", "items",
})
TOP_KEYS = frozenset({"name", "input", "output", "out"})
OUTPUT_KEYS = frozenset({"shape", "single", "filter", "post", "trace"})

#: 数值型下标等
_SEG_NAMED = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)(.*)$")
_SEG_BRACKET = re.compile(r"^\[(.*?)\](.*)$")
_SEG_NUM = re.compile(r"^(\d+)(.*)$")


class SchemaError(ValueError):
    """带 JSON 路径的 schema 错误。"""

    def __init__(self, path, msg):
        self.path = path
        self.msg = msg
        super().__init__(f"{path}: {msg}")


def _err(path, msg):
    raise SchemaError(path, msg)


def _kid(path, key):
    return f"{path}.{key}" if path else key


def _idx(path, i):
    return f"{path}[{i}]"


def parse_value_path(src, path=""):
    """`"$.a.b"` / `"$.5.a.1"` / `"$.a[0]"` -> `("a","b")` / `(5,"a",1)`。

    裸字符串（如 `"a"`）在 v2 **不是路径**，直接报错。

    **幂等**（P0-2 裁决）：已经规范化的链 tuple/list（段为 str/int）原样接受，
    所以 `validate(validate(s))` 必定成功，`build()` 也可以把规范化结果直接
    交给生成器而不必二次 `validate`。错误一定带 `path`，绝不出现空路径。
    """
    if isinstance(src, (tuple, list)):
        segs_out = []
        for seg in src:
            if isinstance(seg, bool) or not isinstance(seg, (str, int)):
                _err(path, f"value 路径下标非法: {seg!r}")
            segs_out.append(seg)
        return tuple(segs_out)
    if not isinstance(src, str):
        _err(path, f"value 必须是字符串（或已规范化的链），得到 {type(src).__name__}")
    s = src.strip()
    if s == "$":
        return ()
    if not s.startswith("$."):
        _err(path, f'value 必须是 "$" 开头的路径（如 "$.a.b"），得到 {src!r}；'
                   f'v2 里裸字符串不是路径')
    s = s[2:]
    segs = []
    while s:
        if s.startswith("."):
            s = s[1:]
            continue
        m = _SEG_BRACKET.match(s)
        if m:
            body, s = m.group(1), m.group(2)
            body = body.strip()
            if len(body) >= 2 and body[0] == body[-1] and body[0] in "'\"":
                segs.append(body[1:-1])
            elif re.fullmatch(r"\d+", body):
                segs.append(int(body))
            else:
                _err(path, f"value 路径下标非法: [{body}]")
            continue
        m = _SEG_NUM.match(s)
        if m:
            segs.append(int(m.group(1)))
            s = m.group(2)
            continue
        m = _SEG_NAMED.match(s)
        if m:
            segs.append(m.group(1))
            s = m.group(2)
            continue
        _err(path, f"value 路径非法: {src!r}（剩余 {s!r}）")
    return tuple(segs)


def _check_type(spec, path):
    if "type" not in spec:
        _err(path, '缺少必填键 "type"')
    t = spec["type"]
    if t not in TYPES:
        _err(_kid(path, "type"),
             "type 非法: " + repr(t) + f"（可选: {', '.join(TYPES)}）")
    return t


def _norm_field(name, spec, path):
    if not isinstance(spec, dict):
        _err(path, f"字段定义必须是 object，得到 {type(spec).__name__}")
    unknown = set(spec) - FIELD_KEYS
    if unknown:
        extra = ", ".join(sorted(unknown))
        hint = ""
        if {"name", "alias", "ext"} & unknown:
            hint = "（疑似旧版字段形状；请用 python -m dcg.migrate 迁移）"
        _err(path, f"未知键: {extra}{hint}")
    t = _check_type(spec, path)
    optional = spec.get("optional", False)
    if not isinstance(optional, bool):
        _err(_kid(path, "optional"), "optional 必须是 bool")

    has_nested = "fields" in spec
    has_items = "items" in spec
    given = [k for k in VALUE_KEYS if k in spec]
    if has_nested and t != "object":
        _err(_kid(path, "fields"), '只有 type="object" 才能用 "fields"')
    if has_items and t != "list":
        _err(_kid(path, "items"), '只有 type="list" 才能用 "items"')
    if has_nested and given:
        _err(path, '"fields" 与 value/const/expr 互斥')
    if has_items and given:
        _err(path, '"items" 与 value/const/expr 互斥')
    if has_nested:
        if not spec["fields"]:
            _err(_kid(path, "fields"), "fields 不能为空 object")
        fields = {}
        for fname, fspec in spec["fields"].items():
            _check_fname(fname, _kid(path, "fields"))
            fields[fname] = _norm_field(
                fname, fspec, _kid(_kid(path, "fields"), fname))
        out = {"type": t, "optional": optional, "fields": fields}
        if "items" in spec:
            out["items"] = _norm_field("<>", spec["items"], _kid(path, "items"))
        return out
    if has_items:
        return {"type": t, "optional": optional,
                "items": _norm_field("<>", spec["items"], _kid(path, "items"))}
    if len(given) != 1:
        if not given:
            _err(path, 'value/const/expr 必须恰好提供一个（一个都没给）')
        _err(path, f'value/const/expr 必须恰好提供一个（给了 {len(given)} 个: '
                  f'{"、".join(given)}）')
    which = given[0]
    out = {"type": t, "optional": optional}
    if which == "value":
        out["value"] = parse_value_path(spec["value"], _kid(path, "value"))
    elif which == "const":
        out["const"] = spec["const"]
    else:
        raw = spec["expr"]
        if not isinstance(raw, str):
            _err(_kid(path, "expr"), "expr 必须是字符串")
        out["expr"] = raw
    return out


def _check_fname(fname, path):
    if not isinstance(fname, str) or not FNAME_RE.match(fname):
        _err(path, f"字段名必须是合法标识符，得到 {fname!r}")
    if fname in ("rows", "row", "r", "out", "ap", "_"):
        _err(path, f"字段名 {fname!r} 与生成码内建名冲突，请改名")


def validate(schema):
    """校验并规范化 schema；非法形状抛 `SchemaError`（带 JSON 路径）。"""
    if not isinstance(schema, dict):
        _err("", f"schema 必须是 object，得到 {type(schema).__name__}")
    unknown = set(schema) - TOP_KEYS
    if unknown:
        extra = ", ".join(sorted(unknown))
        hint = ""
        if {"struct", "describe", "datas", "_field", "protocol", "single",
            "filter", "func", "mode"} & unknown:
            hint = "（疑似旧版 describe 形状；请用 python -m dcg.migrate 迁移）"
        _err("", f"未知顶层键: {extra}{hint}")

    name = schema.get("name")
    if not isinstance(name, str):
        _err("name", "缺少必填键或类型错误")
    if not NAME_RE.match(name):
        _err("name", f"必须是 [A-Za-z_][A-Za-z0-9_]*，得到 {name!r}")

    inp = schema.get("input", {"source": "rows"})
    if not isinstance(inp, dict):
        _err("input", "必须是 object")
    source = inp.get("source", "rows")
    if source not in ("rows", "index"):
        _err("input.source", f'必须是 "rows" 或 "index"，得到 {source!r}')

    out_cfg = schema.get("output", None)
    if out_cfg is None:
        _err("output", "缺少必填键")
    if not isinstance(out_cfg, dict):
        _err("output", "必须是 object")
    uo = set(out_cfg) - OUTPUT_KEYS
    if uo:
        _err("output", "未知键: " + ", ".join(sorted(uo)))
    shape = out_cfg.get("shape", "list")
    if shape not in SHAPES:
        _err("output.shape", f"必须是 {SHAPES} 之一，得到 {shape!r}")
    for k in ("single", "filter", "trace"):
        v = out_cfg.get(k, False)
        if not isinstance(v, bool):
            _err(_kid("output", k), "必须是 bool")
    post = out_cfg.get("post", None)
    if post is not None:
        if not isinstance(post, str) or post.count(".") < 1:
            _err("output.post", f'必须是 "pkg.mod.fn" 形式的字符串，得到 {post!r}')

    out = schema.get("out")
    if out is None:
        _err("out", "缺少必填键")
    if not isinstance(out, dict):
        _err("out", f"必须是 object，得到 {type(out).__name__}")
    out_fields = out.get("fields")
    if out_fields is None:
        if "type" in out and out.get("type") != "object":
            _err("out.type", 'out 必须是 type="object"')
        if "list" in out or "struct" in out or "doc" in out:
            _err("out", 'out 必须是 {"type":"object","fields":{...}}；'
                        '旧版的 list-of-dict 请用 python -m dcg.migrate')
        _err("out.fields", "缺少必填键")
    if not isinstance(out_fields, dict):
        _err("out.fields", f"必须是 object，得到 {type(out_fields).__name__}")
    if not out_fields:
        _err("out.fields", "至少需要一个字段")
    fields = {}
    for fname, fspec in out_fields.items():
        _check_fname(fname, "out.fields")
        fields[fname] = _norm_field(fname, fspec, _kid("out.fields", fname))

    single = bool(out_cfg.get("single", False))
    if single and shape in ("dict", "index"):
        _err("output.single",
             f"shape={shape!r} 下 single=true 无意义："
             f"该形状返回的是整列/整表，不是单行（§4.1）")
    return {
        "name": name,
        "input": {"source": source},
        "output": {
            "shape": shape,
            "single": single,
            "filter": bool(out_cfg.get("filter", True)),
            "post": post,
            "trace": bool(out_cfg.get("trace", False)),
        },
        "out": {"type": "object", "fields": fields},
    }


# ---------------------------------------------------------------- 便捷访问
def value_source(spec):
    """规范化 FieldSpec -> 取值三元组 `(kind, payload)`；kind in value/const/expr/fields/items。"""
    if "fields" in spec:
        return ("fields", spec["fields"])
    if "items" in spec:
        return ("items", spec["items"])
    if "value" in spec:
        return ("value", spec["value"])
    if "const" in spec:
        return ("const", spec["const"])
    return ("expr", spec["expr"])


def is_optional(spec):
    return bool(spec.get("optional"))


__all__ = ["SchemaError", "validate", "parse_value_path", "value_source",
           "is_optional", "TYPES", "SHAPES", "NAME_RE", "FNAME_RE"]
