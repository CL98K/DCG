"""DCG v2 schema 校验（DCG 接口实现细节文档.md §2）。

硬规则：
1. `"a"` 这种**裸字符串永远不是路径**（`$`/`#` 前缀闸门已废弃）。
2. 嵌套**必须**用 `"fields"` 显式声明（list-of-dict 特例已废弃）。
3. 所有非法形状报**带 JSON 路径的 `SchemaError`**，绝不裸 `KeyError`。

`validate()` 返回**规范化深拷贝**（补齐全部默认值），下游（codegen/ir）只认规范化结果。
但这个返回值是**生成器内部形态**，不是可信输入：`input.source` / `value` 是段 tuple、
钩子是 `("expr"|"func", 文本)` 二元组。把它回喂 `validate()` / `generate()` /
`build()` 会命中 `_reject_normalized()` 的契约错误——请始终把**原始 schema**
直接交给 `generate()` / `build()`（它们会自行校验）。
"""

import json
import re

#: 生成模块名前缀
NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
#: FieldSpec 字段名（作为 Python 局部名必须合法，且不得与 helper 前缀撞车）
FNAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

TYPES = ("int64", "int32", "float64", "str", "bool", "list", "object", "any")
SHAPES = ("list", "dict", "index", "rows")

VALUE_KEYS = ("value", "const", "expr")
FIELD_KEYS = frozenset({
    "type", "optional", "default", "value", "const", "expr", "fields",
    # `aggregate=True`：`shape:"dict"` 下这个 object 字段是**列容器**——它的每个
    # 子键各聚合成一列（`{"A":[...],"B":{...}}`）。不加时 object 字段是一列
    # 具名行（`[{...},{...}]`）。两者结构上完全同形，只能靠声明者说清楚，
    # 不能靠结构猜。
    "aggregate",
})
TOP_KEYS = frozenset({"name", "input", "output", "out"})
OUTPUT_KEYS = frozenset({"shape", "before", "after", "trace"})

#: `output.before` / `output.after` 允许的**函数表达式前缀**。
#: 两者都是「对**整体数据**求值」的钩子：以 `=` 开头表示 JSONata 表达式，
#: 以 `@` 开头表示外部函数 `@pkg.mod.fn`（运行期按名解析后调用）。
HOOK_EXPR = "="
HOOK_FUNC = "@"

#: 数值型下标等
_SEG_NAMED = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)(.*)$")
_SEG_BRACKET = re.compile(r"^\[(.*?)\](.*)$")
_SEG_NUM = re.compile(r"^(\d+)(.*)$")

#: 外部函数的限定名：`pkg.mod.fn`（至少一个点，每段都是合法标识符）
_QUALNAME_RE = re.compile(
    r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)+$")


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
    """把路径字符串解析成段元组：`("a", 0, "b")`。

    语法（`$` 之后**点号与方括号等价**，可以任意混用、可以留冗余点号）：

    | 写法 | 结果 | 说明 |
    |---|---|---|
    | `$` | `()` | 空路径 = 整份输入 |
    | `$.a` / `$a` | `("a",)` | 标识符段 |
    | `$.a.0.b` | `("a", 0, "b")` | **纯数字段 = 整数下标**，等同 `$.a[0].b` |
    | `$[0].a` / `$[0][1]` | `(0, "a")` / `(0, 1)` | `$` 后可直接跟方括号 |
    | `$.a[0]` | `("a", 0)` | 点号与方括号混用 |
    | `$["x y"]` / `$['x y']` | `("x y",)` | 引号段 = 字符串键，可含点号/空格 |

    裸字符串（如 `"a"`）在 v2 **不是路径**，直接报错。

    **内部形态**：已经规范化的链 tuple/list（段为 str/int）原样接受——生成器
    内部（codegen/ir、`build()` 的规范化通道）依赖这一点。注意 `validate()` 的
    入口会先拒收整个规范化 schema（`_reject_normalized()`），所以「先 validate
    再回喂」不是公开用法：请把原始 schema 直接交给 `generate()` / `build()`。
    错误一定带 `path`，绝不出现空路径。
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
    if not s.startswith("$"):
        _err(path, f'value 必须是 "$" 开头的路径（如 "$.a.b"），得到 {src!r}；'
                   f'v2 里裸字符串不是路径')
    s = s[1:]
    segs = []
    while s:
        if s[0] == ".":                 # 冗余/分隔点号
            s = s[1:]
            continue
        if s[0] == "[":                 # 方括号段
            m = _SEG_BRACKET.match(s)
            if not m:
                _err(path, f"value 路径非法: {src!r}（方括号未闭合: {s!r}）")
            body, s = m.group(1), m.group(2)
            body = body.strip()
            if len(body) >= 2 and body[0] == body[-1] and body[0] in "'\"":
                segs.append(body[1:-1])            # 引号段：字符串键
            elif re.fullmatch(r"\d+", body):
                segs.append(int(body))             # 纯数字：整数下标
            else:
                _err(path, f"value 路径下标非法: [{body}]")
            continue
        m = _SEG_NUM.match(s)
        if m:                           # 纯数字段 = 整数下标
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
        # 与其它 `_err` 保持同一句式「必须是 <期望>，得到 <实际>」：
        # 枚举类的「期望」就是可选集合本身，所以括号里列出全部合法值。
        _err(_kid(path, "type"),
             f"必须是 {'/'.join(TYPES)} 之一，得到 {t!r}")
    return t


def _check_default(v, t, path):
    """default 必须是 JSON 常量且与声明 type 相容（None 表示 JSON null）。"""
    if t != "any":
        if t in ("int64", "int32"):
            ok = isinstance(v, int) and not isinstance(v, bool)
        elif t == "float64":
            ok = isinstance(v, (int, float)) and not isinstance(v, bool)
        elif t == "str":
            ok = isinstance(v, str)
        elif t == "bool":
            ok = isinstance(v, bool)
        elif t == "list":
            ok = isinstance(v, list)
        else:  # object
            ok = isinstance(v, dict)
        if not ok:
            _err(_kid(path, "default"),
                 f"default 与 type={t!r} 不匹配: {v!r}")
    try:
        json.dumps(v, allow_nan=False)
    except (TypeError, ValueError) as exc:
        _err(_kid(path, "default"), f"default 必须是 JSON 值: {exc}")


def _norm_field(name, spec, path):
    if not isinstance(spec, dict):
        _err(path, f"字段定义必须是 object，得到 {type(spec).__name__}")
    unknown = set(spec) - FIELD_KEYS
    if unknown:
        _err(path, "未知键: " + ", ".join(sorted(unknown)))
    t = _check_type(spec, path)
    optional = spec.get("optional", False)
    if not isinstance(optional, bool):
        _err(_kid(path, "optional"), "optional 必须是 bool")

    has_nested = "fields" in spec

    given = [k for k in VALUE_KEYS if k in spec]
    has_default = "default" in spec
    if has_default:
        if has_nested:
            _err(_kid(path, "default"),
                 "结构字段（fields）不能设 default：其值不会是 undefined")
        _check_default(spec["default"], t, path)
    if has_nested and t not in ("object", "list"):
        _err(_kid(path, "fields"), '只有 type 为 "object" 或 "list" 才能用 "fields"')
    if has_nested and given:
        _err(path, '"fields" 与 value/const/expr 互斥')
    aggregate = spec.get("aggregate", False)
    if not isinstance(aggregate, bool):
        _err(_kid(path, "aggregate"), "aggregate 必须是 bool")
    if aggregate and not has_nested:
        _err(_kid(path, "aggregate"),
             'aggregate 只能用在带 "fields" 的 object 字段上')
    if aggregate and t != "object":
        _err(_kid(path, "aggregate"),
             'aggregate 只能用在 type 为 "object" 的字段上')
    if has_nested:
        if not spec["fields"]:
            _err(_kid(path, "fields"), "fields 不能为空 object")
        fields = {}
        for fname, fspec in spec["fields"].items():
            _check_fname(fname, _kid(path, "fields"))
            fields[fname] = _norm_field(
                fname, fspec, _kid(_kid(path, "fields"), fname))
        out = {"type": t, "optional": optional, "fields": fields}
        if aggregate:
            out["aggregate"] = True
        return out
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
    if has_default:
        out["default"] = spec["default"]
    return out


def _check_fname(fname, path):
    if not isinstance(fname, str) or not FNAME_RE.match(fname):
        _err(path, f"字段名必须是合法标识符，得到 {fname!r}")
    if fname in ("rows", "row", "r", "out", "ap", "_"):
        _err(path, f"字段名 {fname!r} 与生成码内建名冲突，请改名")


def _norm_hook(out_cfg, key):
    """规范化 `output.before` / `output.after` —— 两者都是**整体数据钩子**。

    统一成 `("expr", 文本)` / `("func", "pkg.mod.fn")`：
    * `"="` 开头的字符串 —— JSONata 表达式（显式标记）；
    * `"@"` 开头的字符串 —— 外部函数 `@pkg.mod.fn`；
    * 其它非空字符串 —— 当 JSONata 表达式（最常见的省略写法）；
    * 布尔 / 数字 / null —— 一律拒绝：钩子是「输入数据 → 新数据」的转换，
      没有一个能表达这个意思的布尔语义。
    """
    raw = out_cfg.get(key, None)
    if raw is None:
        return None
    if isinstance(raw, bool):
        _err(_kid("output", key),
             f"{key} 不接受布尔值：它是「整体数据 → 新数据」的转换钩子，"
             f'请写 JSONata 表达式（如 "= $map($, function($r){{ $r }})"）'
             f'或外部函数（"@pkg.mod.fn"）')
    if not isinstance(raw, str):
        _err(_kid("output", key),
             f'必须是表达式字符串或 "@pkg.mod.fn"，得到 {type(raw).__name__}')
    text = raw.strip()
    if not text:
        _err(_kid("output", key), "不能是空字符串")
    if text.startswith(HOOK_FUNC):
        qual = text[len(HOOK_FUNC):].strip()
        if not _QUALNAME_RE.match(qual):
            _err(_kid("output", key),
                 f'外部函数必须是 "@pkg.mod.fn" 形式，得到 {raw!r}')
        return ("func", qual)
    if text.startswith(HOOK_EXPR):
        text = text[len(HOOK_EXPR):].strip()
        if not text:
            _err(_kid("output", key), f'"=" 之后缺少表达式，得到 {raw!r}')
    return ("expr", text)


def _normalized_value_path(spec, path):
    """返回字段里已是规范化段 tuple 的 `value` 的路径；没有则 None。"""
    if not isinstance(spec, dict):
        return None
    if isinstance(spec.get("value"), tuple):
        return _kid(path, "value")
    subs = spec.get("fields")
    if isinstance(subs, dict):
        for name, sub in subs.items():
            hit = _normalized_value_path(sub, _kid(_kid(path, "fields"), name))
            if hit is not None:
                return hit
    return None


def _reject_normalized(schema):
    """拒收 `validate()` 自己返回的规范化内部形态（第九轮裁决 B）。

    规范形里有三类**原始 schema 不可能合法出现**的 tuple：
    * `input.source` —— 段 tuple（原始写法必须是 `"$.a.b"` 字符串）；
    * `output.before` / `output.after` —— `("expr"|"func", 文本)` 二元组；
    * 任意层 `out.fields[*].value` —— 段 tuple。

    这三类都是生成器内部表示，不是可信输入。早失败比让后续 `_norm_hook`
    报「得到 tuple」清楚得多；也避免「`validate()` 的输出能不能再回喂」这种
    两可约定（本函数把口径钉死为：不能）。
    """
    if not isinstance(schema, dict):
        return
    _hint = ("收到 dcg.validate() 返回的规范化内部形态（{shape}），不是可信输入："
             "请把**原始 schema** 直接传给 generate()/build()（它们会自行校验），"
             "不要先 validate() 再把结果回喂")
    out = schema.get("out")
    if isinstance(out, dict):
        fields = out.get("fields")
        if isinstance(fields, dict):
            for name, spec in fields.items():
                hit = _normalized_value_path(spec, _kid("out.fields", name))
                if hit is not None:
                    _err(hit, _hint.format(shape="value 段 tuple"))
    out_cfg = schema.get("output")
    if isinstance(out_cfg, dict):
        for key in ("before", "after"):
            if isinstance(out_cfg.get(key), tuple):
                _err(_kid("output", key), _hint.format(shape="钩子二元组"))
    inp = schema.get("input")
    if isinstance(inp, dict) and isinstance(inp.get("source"), tuple):
        _err("input.source", _hint.format(shape="段 tuple"))


def validate(schema):
    """校验并规范化 schema；非法形状抛 `SchemaError`（带 JSON 路径）。"""
    if not isinstance(schema, dict):
        _err("", f"schema 必须是 object，得到 {type(schema).__name__}")
    _reject_normalized(schema)
    unknown = set(schema) - TOP_KEYS
    if unknown:
        _err("", "未知顶层键: " + ", ".join(sorted(unknown)))

    name = schema.get("name")
    if not isinstance(name, str):
        _err("name", "缺少必填键或类型错误")
    if not NAME_RE.match(name):
        _err("name", f"必须是 [A-Za-z_][A-Za-z0-9_]*，得到 {name!r}")

    inp = schema.get("input", {})
    if not isinstance(inp, dict):
        _err("input", "必须是 object")
    uin = set(inp) - {"source"}
    if uin:
        _err("input", "未知键: " + ", ".join(sorted(uin)))
    raw_src = inp.get("source", None)
    if raw_src is None:
        inp = {"source": ()}
    else:
        # `input.source` 是**行集合在输入数据里的路径**，语法与字段 `value`
        # 完全一致（`$.a.b` / `$`）。缺省 `$` = 整份输入就是行集合。
        inp = {"source": parse_value_path(raw_src, "input.source")}

    out_cfg = schema.get("output", None)
    if out_cfg is None:
        _err("output", "缺少必填键")
    if not isinstance(out_cfg, dict):
        _err("output", "必须是 object")
    for legacy, repl in (("filter", "before"), ("post", "after")):
        if legacy in out_cfg:
            _err(_kid("output", legacy),
                 f'"{legacy}" 不存在：逐行钩子已被「整体数据 → 新数据」的'
                 f'"{repl}" 取代（"= <表达式>" 或 "@pkg.mod.fn"）')
    uo = set(out_cfg) - OUTPUT_KEYS
    if uo:
        _err("output", "未知键: " + ", ".join(sorted(uo)))
    shape = out_cfg.get("shape", "list")
    if shape not in SHAPES:
        _err("output.shape", f"必须是 {SHAPES} 之一，得到 {shape!r}")
    trace = out_cfg.get("trace", False)
    if not isinstance(trace, bool):
        _err(_kid("output", "trace"), "必须是 bool")
    # `before` / `after` 都是**整体数据钩子**（§4.1）：
    # * `before` 收到的是经 `input.source` 定位后的全量数据，返回值替换它当新输入；
    # * `after` 收到的是按 shape 组装完成的全量结果，返回值作为最终返回值。
    before = _norm_hook(out_cfg, "before")
    after = _norm_hook(out_cfg, "after")

    out = schema.get("out")
    if out is None:
        _err("out", "缺少必填键")
    if not isinstance(out, dict):
        _err("out", f"必须是 object，得到 {type(out).__name__}")
    out_fields = out.get("fields")
    if out_fields is None:
        if "type" in out and out.get("type") != "object":
            _err("out.type", 'out 必须是 type="object"')
        _err("out.fields", "缺少必填键")
    if not isinstance(out_fields, dict):
        _err("out.fields", f"必须是 object，得到 {type(out_fields).__name__}")
    if not out_fields:
        _err("out.fields", "至少需要一个字段")
    fields = {}
    for fname, fspec in out_fields.items():
        _check_fname(fname, "out.fields")
        fields[fname] = _norm_field(fname, fspec, _kid("out.fields", fname))

    return {
        "name": name,
        "input": inp,
        "output": {
            "shape": shape,
            "before": before,
            "after": after,
            "trace": trace,
        },
        "out": {"type": "object", "fields": fields},
    }


# ---------------------------------------------------------------- 便捷访问
def value_source(spec):
    """规范化 FieldSpec -> 取值二元组 `(kind, payload)`；kind in value/const/expr/fields。"""
    if "fields" in spec:
        return ("fields", spec["fields"])
    if "value" in spec:
        return ("value", spec["value"])
    if "const" in spec:
        return ("const", spec["const"])
    return ("expr", spec["expr"])


def is_optional(spec):
    return bool(spec.get("optional"))


__all__ = ["SchemaError", "validate", "parse_value_path", "value_source",
           "is_optional", "TYPES", "SHAPES",
    "NAME_RE", "FNAME_RE"]
