# -*- coding: utf-8 -*-
"""`examples/jx/*.jx` 语料解析 + 真实执行层（供执行测试与 `JSONata 语法.md` 生成器共用）。

- `examples/jx/*.jx` 是语料唯一真理源：每个 `=== <标题>` 记录是一条例样；
- 本模块只负责解析与"真跑一遍"观测（`compile_expr` -> `dcg.generate` -> `exec` -> `run`）；
- 执行测试（`tests/test_jx_examples.py`）与文档生成器（`tests/gen_jsonata_manual.py`）都基于它，
  保证文档里的结果与测试里的结果来自同一套执行逻辑。
"""
from __future__ import annotations

import ast
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import dcg  # noqa: E402
from dcg import SchemaError  # noqa: E402
from dcg.jx import Unsupported, compile_expr  # noqa: E402

SAMPLES_DIR = ROOT / "examples" / "jx"


# ---------------------------------------------------------------------------
# 样例文件解析
# ---------------------------------------------------------------------------
def sample_files():
    return sorted(SAMPLES_DIR.glob("*.jx"))


def parse_file(path):
    meta = {"file": path.name, "title": path.stem, "desc": []}
    cases = []
    cur = None
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.rstrip()
        if line.startswith("=== "):
            cur = {"title": line[4:].strip(), "expr": None, "row": {}, "expect": None,
                   "type": None, "note": None, "state": None}
            cases.append(cur)
            continue
        if line.startswith("#"):
            body = line[1:].strip()
            if body.startswith("file:"):
                meta["file"] = body.split(":", 1)[1].strip()
            elif body.startswith("title:"):
                meta["title"] = body.split(":", 1)[1].strip()
            elif body.startswith("desc:"):
                meta["desc"].append(body.split(":", 1)[1].strip())
            continue
        if not line.strip() or cur is None:
            continue
        key, _, val = line.partition(":")
        key, val = key.strip(), val.strip()
        if key == "expr":
            cur["expr"] = val
        elif key == "row":
            cur["row"] = ast.literal_eval(val)
        elif key == "note":
            cur["note"] = val
        elif key in ("expect", "type", "state"):
            cur[key] = val
        else:
            raise AssertionError(f"{path.name}: 未知键 {key!r}")
    for case in cases:
        if not case["state"]:
            case["state"] = "gap" if case["expect"] == "codegen-reject" else "ok"
    return meta, cases


def all_samples():
    out = []
    for path in sample_files():
        meta, cases = parse_file(path)
        for i, case in enumerate(cases, 1):
            out.append((path, meta, i, case))
    return out


ALL = all_samples()


def _cid(item):
    path, _meta, i, case = item
    return f"{path.stem}:{i}:{case['title']}"


# ---------------------------------------------------------------------------
# 真实执行
# ---------------------------------------------------------------------------
class Obs:
    __slots__ = ("kind", "value", "exc")

    def __init__(self, kind, value=None, exc=None):
        self.kind, self.value, self.exc = kind, value, exc


def build(expr, row, out_type="any"):
    """按 DCG 真实管线建 schema -> generate -> exec，返回 (run, 列号, 源码)。"""
    fields = {}
    for k in row:
        if isinstance(k, str) and k.isidentifier() and k.isascii():
            fields[k] = {"type": "any", "value": "$." + k}
    fields["v"] = {"type": out_type, "expr": expr}
    schema = {"name": "ex", "output": {"shape": "list"},
              "out": {"type": "object", "fields": fields}}
    src = dcg.generate(schema)
    ns = {}
    exec(compile(src, "<gen/examples>", "exec"), ns)  # noqa: S102 - 被测产物就是代码
    return ns, list(fields).index("v"), src


def observe(case):
    expr = case["expr"]
    expect = case["expect"] or ""
    if expect == "reject":
        try:
            compile_expr(expr)
        except Unsupported as exc:
            return Obs("unsupported", exc=exc)
        return Obs("not-rejected")
    if expect == "codegen-reject":
        compile_expr(expr)                      # jx 必须接受（这是"gap 而非语法错"的前提）
        try:
            build(expr, case["row"])
        except Unsupported as exc:
            return Obs("codegen-unsupported", exc=exc)
        except SchemaError as exc:
            # 生成器层的两类拒绝：Unsupported（能力缺口）与 SchemaError（语义不成立，
            # 如 `$x` 变量在逐行模式下没有作用域）。两者都只在 build 期抛出，
            # 对语料而言都是"编译期拒绝"，见契约 §7.3。
            return Obs("codegen-schema-error", exc=exc)
        return Obs("not-rejected")
    ns, ix, _src = build(expr, case["row"])
    try:
        out = ns["run"]([case["row"]])
    except Exception as exc:  # noqa: BLE001 - defect 例要求观测到真实异常
        return Obs("raise", exc=exc)
    res = out[0][ix]
    sentinel = ns.get("_U")
    if sentinel is not None and res is sentinel:
        return Obs("undefined", value=res)
    return Obs("value", value=res)
