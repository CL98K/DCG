# -*- coding: utf-8 -*-
"""DCG Schema Builder 的后端桥。

对 `tools/app.py` 只暴露三个函数：

    dcg_version() -> str
    infer(text: str) -> dict
    run(schema: dict, text: str) -> dict

契约见同目录 `API.md`。本模块只依赖 Python 标准库 + 仓库内的 `dcg` 包，
不写任何文件、不起线程、不联网。所有异常（`MemoryError` 除外）都转成
``{"ok": False, "stage": ..., "error": ...}``，绝不穿透给 HTTP 层。
"""
from __future__ import annotations

import json
import math
import os
import re
import sys
import time

# ---------------------------------------------------------------------------
# 加载同仓库的 dcg 包：tools/ 与 dcg/ 是兄弟目录，仓库根 = tools/ 的上一级。
# ---------------------------------------------------------------------------
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import dcg  # noqa: E402  （必须在 sys.path 调整之后）

__all__ = ["dcg_version", "infer", "run"]

#: run() 最多回传的输出行数（契约冻结值）
MAX_OUT_ROWS = 200
#: 每个字段/节点最多保留的样例数
MAX_SAMPLES = 3

_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_INDEX_LABEL_RE = re.compile(r"^\[(\d+)\]$")
#: `dcg.schema._check_fname` 里禁止的字段名；suggest 要主动避开，否则用户直接采用会校验失败
_RESERVED_FIELD_NAMES = frozenset({"rows", "row", "r", "out", "ap", "_"})


# ===========================================================================
# 小工具
# ===========================================================================
class _WarnBag:
    """有序、去重的警告集合。"""

    __slots__ = ("_items", "_seen")

    def __init__(self):
        self._items = []
        self._seen = set()

    def add(self, message):
        if message not in self._seen:
            self._seen.add(message)
            self._items.append(message)

    def extend(self, messages):
        for message in messages:
            self.add(message)

    def as_list(self):
        return list(self._items)

    def __bool__(self):
        return bool(self._items)


def _safe_scalar(value):
    """把单个标量转成可 JSON 序列化的值（NaN/Inf -> 字符串）。"""
    if isinstance(value, float) and not math.isfinite(value):
        if math.isnan(value):
            return "NaN"
        return "Infinity" if value > 0 else "-Infinity"
    return value


def _json_safe(value, warnings, warn_nonstr_keys=True):
    """递归降级成 ``json.dumps(..., allow_nan=False)`` 能编码的值。

    非有限浮点、非 JSON 类型、非字符串键、孤立代理字符都在这里转成字符串，
    并在 ``warnings`` 里留一条提示 —— 调用方拿到的一定是 JSON 可编码结果。
    ``warn_nonstr_keys=False`` 用于 ``shape="index"``：行号键本就该转字符串。
    """
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        if math.isfinite(value):
            return value
        warnings.add("结果含 NaN/Infinity，已转成字符串表示")
        return "NaN" if math.isnan(value) else ("Infinity" if value > 0 else "-Infinity")
    if isinstance(value, str):
        try:
            value.encode("utf-8")
        except UnicodeEncodeError:
            warnings.add("结果含非法 Unicode 字符，已替换为 U+FFFD")
            return value.encode("utf-8", "replace").decode("utf-8")
        return value
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            if isinstance(key, str):
                safe_key = key
                try:
                    safe_key.encode("utf-8")
                except UnicodeEncodeError:
                    safe_key = key.encode("utf-8", "replace").decode("utf-8")
                    warnings.add("结果含非法 Unicode 字符，已替换为 U+FFFD")
            else:
                safe_key = _key_text(key)
                if warn_nonstr_keys:
                    warnings.add("结果含非字符串键，已转成字符串")
            out[safe_key] = _json_safe(item, warnings, warn_nonstr_keys)
        return out
    if isinstance(value, (list, tuple)):
        return [_json_safe(item, warnings, warn_nonstr_keys) for item in value]
    warnings.add("结果含非 JSON 类型（%s），已转成字符串表示" % type(value).__name__)
    try:
        return str(value)
    except Exception:  # noqa: BLE001 —— __str__ 自己炸了就用 repr
        try:
            return repr(value)
        except Exception:  # noqa: BLE001
            return "<unprintable %s>" % type(value).__name__


def _key_text(key):
    try:
        return str(key)
    except Exception:  # noqa: BLE001
        return "<unprintable key>"


def _scalar_type(value):
    if value is None:
        return "any"
    if isinstance(value, bool):      # bool 是 int 的子类，必须先判
        return "bool"
    if isinstance(value, int):
        return "int64"
    if isinstance(value, float):
        return "float64"
    if isinstance(value, str):
        return "str"
    return "any"


def _merge_scalar_types(types):
    """跨行合并标量类型：全 int -> int64；含 float -> float64；冲突 -> any。"""
    kinds = set(types)
    if not kinds:
        return "any"
    if len(kinds) == 1:
        return next(iter(kinds))
    if kinds <= {"int64", "float64"}:
        return "float64"
    return "any"


def _path_of(segs):
    """段元组 -> DCG 取值路径；行根（空）用 ``$``。"""
    if not segs:
        return "$"
    return "$." + ".".join(str(seg) for seg in segs)


def _is_representable_key(key):
    """键能否用 DCG 点号路径表达（``$`` 后接标识符段）。

    DCG 的点号路径只认 ``[A-Za-z_][A-Za-z0-9_]*`` 作对象键；纯数字会被当成
    数组下标，含 ``.`` / ``-`` / 中文等的键都无法表达。
    """
    return isinstance(key, str) and bool(_IDENT_RE.match(key))


def _suggest_name(label, used):
    """把节点标签变成合法、不重复的建议字段名。"""
    match = _INDEX_LABEL_RE.match(label or "")
    if match:
        base = "col_" + match.group(1)
    elif label == "row":
        base = "value"          # 标量行的整行字段
    else:
        base = re.sub(r"[^A-Za-z0-9_]", "_", label or "")
        if not base:
            base = "col_%d" % len(used)
        elif not base[0].isalpha():
            base = "col_" + base
    if base in _RESERVED_FIELD_NAMES:
        base = base + "_2"
    if base not in used:
        used.add(base)
        return base
    n = 2
    while True:
        candidate = "%s_%d" % (base, n)
        if candidate not in used:
            used.add(candidate)
            return candidate
        n += 1


# ===========================================================================
# JSON 解析
# ===========================================================================
class _BadConstant(ValueError):
    """`JSON` 文本里出现 Python 扩展常量 NaN / Infinity。"""

    def __init__(self, name):
        self.name = name
        super().__init__("非法 JSON 常量 %s" % name)


def _reject_constant(name):
    raise _BadConstant(name)


def _line_col(text, index):
    index = max(0, min(index, len(text)))
    line = text.count("\n", 0, index) + 1
    last_nl = text.rfind("\n", 0, index)
    col = index - last_nl
    return line, col


def _parse_json_text(text):
    """返回 ``(data, error)``；两者恰有一个为 None。"""
    if not isinstance(text, str):
        return None, "text 必须是字符串，得到 %s" % type(text).__name__
    text = text.lstrip("\ufeff")
    try:
        return json.loads(text, parse_constant=_reject_constant), None
    except json.JSONDecodeError as exc:
        return None, "第 %d 行第 %d 列: %s" % (exc.lineno, exc.colno, exc.msg)
    except _BadConstant as exc:
        line, col = _line_col(text, text.find(exc.name))
        return None, "第 %d 行第 %d 列: %s" % (line, col, exc)
    except (TypeError, ValueError) as exc:
        return None, "第 1 行第 1 列: %s" % exc


# ===========================================================================
# 结构推断
# ===========================================================================
class _State:
    """同一个路径上跨行累积的观测值。"""

    __slots__ = (
        "label", "segs", "children",
        "saw_object", "saw_scalar", "scalar_types", "scalar_samples",
        "saw_list", "saw_plain_list", "saw_container_list",
        "list_len_max", "list_samples",
    )

    def __init__(self, label, segs):
        self.label = label
        self.segs = segs
        self.children = {}          # seg -> _State（保持首次出现顺序）
        self.saw_object = False
        self.saw_scalar = False
        self.scalar_types = []
        self.scalar_samples = []
        self.saw_list = False
        self.saw_plain_list = False
        self.saw_container_list = False
        self.list_len_max = 0
        self.list_samples = []

    def child(self, seg, label, segs):
        state = self.children.get(seg)
        if state is None:
            state = _State(label, segs)
            self.children[seg] = state
        return state


def _add_sample(bucket, value):
    if len(bucket) < MAX_SAMPLES:
        bucket.append(_safe_scalar(value))


def _observe(state, value, warnings):
    """把一次观测（一行里某个路径的值）并入 ``state``。"""
    if isinstance(value, dict):
        state.saw_object = True
        for key, item in value.items():
            if not _is_representable_key(key):
                if isinstance(key, str) and "." in key:
                    warnings.add("键 %s 含 '.'，DCG 路径无法表达，已跳过" % key)
                else:
                    warnings.add("键 %r 不是合法标识符，DCG 路径无法表达，已跳过" % (key,))
                continue
            _observe(state.child(key, key, state.segs + (key,)), item, warnings)
        return

    if isinstance(value, list):
        state.saw_list = True
        state.list_len_max = max(state.list_len_max, len(value))
        if any(isinstance(item, (dict, list)) for item in value):
            state.saw_container_list = True
            for index, item in enumerate(value):
                _observe(state.child(index, "[%d]" % index, state.segs + (index,)),
                         item, warnings)
        else:
            state.saw_plain_list = True
            for item in value:
                _add_sample(state.list_samples, item)
        return

    state.saw_scalar = True
    state.scalar_types.append(_scalar_type(value))
    _add_sample(state.scalar_samples, value)


def _observe_row(root_state, row, warnings):
    """rows / wrapper 模式下，把一行拆成「相对行根」的字段观测。"""
    if isinstance(row, dict):
        for key, item in row.items():
            if not _is_representable_key(key):
                if isinstance(key, str) and "." in key:
                    warnings.add("键 %s 含 '.'，DCG 路径无法表达，已跳过" % key)
                else:
                    warnings.add("键 %r 不是合法标识符，DCG 路径无法表达，已跳过" % (key,))
                continue
            _observe(root_state.child(key, key, (key,)), item, warnings)
        return
    if isinstance(row, list):
        for index, item in enumerate(row):
            _observe(root_state.child(index, "[%d]" % index, (index,)),
                     item, warnings)
        return
    # 标量行：整行就是一个字段，路径 "$"
    _observe(root_state.child("$", "row", ()), row, warnings)


def _leaf_kind(state):
    """判定节点的展示类型。"""
    has_scalar = state.saw_scalar
    has_list = state.saw_list
    has_object = state.saw_object
    if has_object and not has_scalar and not has_list:
        return "object"
    if has_list and not has_scalar and not has_object:
        return "list_container" if state.saw_container_list else "list_plain"
    if has_scalar and not has_list and not has_object:
        return "scalar"
    return "mixed"


def _make_node(state, node_type, count, sample, children, path=None):
    return {
        "label": state.label,
        "path": _path_of(state.segs) if path is None else path,
        "type": node_type,
        "count": count,
        "sample": sample,
        "children": children,
    }


def _finalize(state, warnings, fields, used_names, as_field):
    """把累积状态变成 API.md 里的 node；``as_field`` 时顺带登记 fields 条目。"""
    kind = _leaf_kind(state)

    if kind == "object":
        children = [
            _finalize(child, warnings, fields, used_names, True)
            for child in state.children.values()
        ]
        node = _make_node(state, "object", len(children), None, children)
        if as_field:
            warnings.add("对象字段 %s 暂不支持直接拖拽，可用表达式取子字段"
                         % _field_display_label(state))
        return node

    if kind == "list_container":
        children = [
            _finalize(child, warnings, fields, used_names, True)
            for child in state.children.values()
        ]
        node = _make_node(state, "list", state.list_len_max, None, children)
        if as_field:
            warnings.add("数组字段 %s 含对象/嵌套元素，暂不支持直接拖拽，"
                         "可用表达式取子字段" % _field_display_label(state))
        return node

    if kind == "list_plain":
        samples = [_safe_scalar(item) for item in state.list_samples]
        node = _make_node(state, "list", state.list_len_max, samples, None)
        if as_field:
            _register_field(state, "list", samples, fields, used_names)
        return node

    if kind == "scalar":
        node_type = _merge_scalar_types(state.scalar_types)
        sample = _first_non_none(state.scalar_samples)
        node = _make_node(state, node_type, None, sample, None)
        if as_field:
            _register_field(state, node_type, state.scalar_samples, fields, used_names)
        return node

    # mixed：同一路径上 object/list/scalar 冲突 -> any，且不再下钻（路径不保证存在）
    if state.saw_object or state.saw_container_list:
        warnings.add("字段 %s 类型冲突，已按 any 处理" % _field_display_label(state))
    sample = _first_non_none(state.scalar_samples)
    node = _make_node(state, "any", None, sample, None)
    if as_field:
        _register_field(state, "any", state.scalar_samples, fields, used_names)
    return node


def _first_non_none(samples):
    for sample in samples:
        if sample is not None:
            return sample
    return samples[0] if samples else None


def _field_display_label(state):
    if state.label and state.label != "row":
        return state.label
    return _path_of(state.segs)


def _register_field(state, field_type, samples, fields, used_names):
    fields.append({
        "path": _path_of(state.segs),
        "label": state.label,
        "suggest": _suggest_name(state.label, used_names),
        "type": field_type,
        "samples": list(samples)[:MAX_SAMPLES],
        "depth": len(state.segs),
    })


def _schema_name_for_wrapper(key):
    if isinstance(key, str) and _IDENT_RE.match(key):
        return key
    base = re.sub(r"[^A-Za-z0-9_]", "_", key if isinstance(key, str) else "rows")
    base = base.strip("_") or "rows"
    if not base[0].isalpha():
        base = "s_" + base
    return base


def _infer_impl(text):
    data, error = _parse_json_text(text)
    if error is not None:
        return {"ok": False, "stage": "json", "error": error}

    warnings = _WarnBag()
    fields = []
    used_names = set(_RESERVED_FIELD_NAMES)

    if isinstance(data, list):
        mode = "rows"
        rows = data
        source = None
        schema_name = "rows"
        empty_warning = "样本为空数组，无法推断字段" if not rows else None
    elif isinstance(data, dict):
        list_keys = [key for key, value in data.items() if isinstance(value, list)]
        if len(list_keys) == 1:
            mode = "wrapper"
            wrapper_key = list_keys[0]
            rows = data[wrapper_key]
            source = "$." + str(wrapper_key)
            schema_name = _schema_name_for_wrapper(wrapper_key)
            empty_warning = "样本为空数组，无法推断字段" if not rows else None
            if not _is_representable_key(wrapper_key):
                warnings.add("包装键 %r 不是合法标识符，自动生成的 input.source "
                             "可能不可用，请手动调整" % (wrapper_key,))
            for key in data:
                if key != wrapper_key:
                    warnings.add("顶层键 %r 不是行集合，已忽略（仅 %s 作为行）"
                                 % (key, source))
        else:
            mode = "single"
            rows = [data]
            source = None
            schema_name = "rows"
            empty_warning = "样本是空对象，无法推断字段" if not data else None
    else:
        mode = "single"
        rows = [data]
        source = None
        schema_name = "rows"
        empty_warning = None

    if mode in ("rows", "wrapper"):
        row_state = _State("row", ())
        for row in rows:
            _observe_row(row_state, row, warnings)
        children = [
            _finalize(child, warnings, fields, used_names, True)
            for child in row_state.children.values()
        ]
        tree = _make_node(row_state, "rows", len(rows), None, children, path="")
        tree["label"] = "rows[%d]" % len(rows)
    elif isinstance(data, dict):
        row_state = _State("row", ())
        _observe(row_state, data, warnings)
        children = [
            _finalize(child, warnings, fields, used_names, True)
            for child in row_state.children.values()
        ]
        tree = _make_node(row_state, "object", len(children), None, children, path="")
    else:
        # 顶层本身就是标量
        row_state = _State("row", ())
        scalar_state = row_state.child("$", "row", ())
        _observe(scalar_state, data, warnings)
        tree = _finalize(scalar_state, warnings, fields, used_names, True)

    if empty_warning:
        warnings.add(empty_warning)

    # 样例里的 NaN/Inf、孤立代理字符等也降级成字符串，保证整个响应可 JSON 编码
    samples_note = _WarnBag()
    tree = _json_safe(tree, samples_note)
    fields = _json_safe(fields, samples_note)
    warnings.extend(samples_note.as_list())

    return {
        "ok": True,
        "mode": mode,
        "source": source,
        "row_count": len(rows),
        "schema_name": schema_name,
        "tree": tree,
        "fields": fields,
        "warnings": warnings.as_list(),
    }


def infer(text):
    """原始 JSON 文本 -> 结构树 + 扁平可拖拽字段（见 API.md）。"""
    try:
        return _infer_impl(text)
    except MemoryError:
        raise
    except Exception as exc:  # noqa: BLE001 —— 兜底，绝不穿透
        return {"ok": False, "stage": "infer",
                "error": "%s: %s" % (type(exc).__name__, exc)}


# ===========================================================================
# 编译并运行
# ===========================================================================
def _manifest_for(schema, code):
    """尽量取 manifest 键；取不到的键直接省略（前端容错显示）。"""
    manifest = {}
    try:
        name = schema.get("name") if isinstance(schema, dict) else None
        if isinstance(name, str) and name:
            manifest["name"] = name
    except Exception:  # noqa: BLE001
        pass
    manifest["lines"] = len(code.splitlines())
    manifest["chars"] = len(code)

    try:
        from dcg import codegen as _codegen

        norm = dcg.validate(schema)
        manifest["schema_sha1"] = _codegen.schema_sha1(norm)
        manifest["generator_version"] = _codegen._generator_version()
    except Exception:  # noqa: BLE001 —— 取不到就省略
        pass
    return manifest


def _truncate_columns(value):
    """列容器递归截断：list 列保留前 ``MAX_OUT_ROWS`` 个值。

    返回 ``(out, 最大列长, 是否任一列被截断)``；``shape="dict"`` 的顶层与
    ``aggregate`` 产生的嵌套列都走这里。
    """
    if isinstance(value, list):
        return value[:MAX_OUT_ROWS], len(value), len(value) > MAX_OUT_ROWS
    if isinstance(value, dict):
        out = {}
        count = 0
        truncated = False
        for key, item in value.items():
            if isinstance(item, (list, dict)):
                sub, sub_count, sub_trunc = _truncate_columns(item)
            else:
                sub, sub_count, sub_trunc = item, 0, False
            out[key] = sub
            count = max(count, sub_count)
            truncated = truncated or sub_trunc
        return out, count, truncated
    return value, 0, False


def _index_sort_key(key):
    """``shape="index"`` 的行号键排序：数值优先升序，非数值排后面。"""
    try:
        return (0, float(key))
    except (TypeError, ValueError):
        return (1, _key_text(key))


def _shape_out(shape, value):
    """按 schema 的 ``output.shape`` 整形结果 -> ``(out, out_count, truncated)``。

    shape 与真实结果不符时退回兜底（整体当 1 行），绝不抛异常。
    """
    if shape in ("list", "rows"):
        if isinstance(value, list):
            return (value[:MAX_OUT_ROWS], len(value), len(value) > MAX_OUT_ROWS)
    elif shape == "dict":
        if isinstance(value, dict):
            return _truncate_columns(value)
    elif shape == "index":
        if isinstance(value, dict):
            ordered = sorted(value.items(), key=lambda kv: _index_sort_key(kv[0]))
            out = dict(ordered[:MAX_OUT_ROWS])
            return out, len(value), len(value) > MAX_OUT_ROWS

    # 兜底：single 模式返回单个对象、shape 与结果不符等。
    if value is None:
        return [], 0, False
    return [value], 1, False


def _run_impl(schema, text):
    started = time.perf_counter()

    data, error = _parse_json_text(text)
    if error is not None:
        return {"ok": False, "stage": "json", "error": error, "code": None}

    try:
        norm = dcg.validate(schema)
    except MemoryError:
        raise
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "stage": "schema",
                "error": "%s" % (exc,), "code": None}

    try:
        code = dcg.generate(schema)
    except MemoryError:
        raise
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "stage": "generate",
                "error": "%s" % (exc,), "code": None}

    try:
        namespace = {}
        exec(compile(code, "<dcg-generated>", "exec"), namespace)  # noqa: S102
        entry = namespace.get("run")
        if not callable(entry):
            raise RuntimeError("生成代码没有定义 run()")
        # 有 input.source：传整个 wrapper 对象（生成代码第一行自行解包）；
        # 否则传「行列表」——single 模式补一层 [row]。
        if norm["input"]["source"]:
            payload = data
        elif isinstance(data, list):
            payload = data
        else:
            payload = [data]
        result = entry(payload)
    except MemoryError:
        raise
    except Exception as exc:  # noqa: BLE001 —— stage=run 尽量带 code
        return {"ok": False, "stage": "run",
                "error": "%s: %s" % (type(exc).__name__, exc), "code": code}

    elapsed_ms = (time.perf_counter() - started) * 1000.0

    warnings = _WarnBag()
    try:
        shape = norm["output"]["shape"]
    except Exception:  # noqa: BLE001 —— 理论上 validate 已保证存在
        shape = None
    # index 的行号键在 JSON 里本来就会变成字符串，不必告警。
    safe_result = _json_safe(result, warnings,
                             warn_nonstr_keys=(shape != "index"))
    out, out_count, truncated = _shape_out(shape, safe_result)

    return {
        "ok": True,
        "out": out,
        "out_count": out_count,
        "truncated": truncated,
        "code": code,
        "elapsed_ms": round(elapsed_ms, 3),
        "manifest": _manifest_for(schema, code),
        "warnings": warnings.as_list(),
    }


def run(schema, text):
    """JSON 文本 + schema -> 编译、执行、回传输出（见 API.md）。"""
    try:
        return _run_impl(schema, text)
    except MemoryError:
        raise
    except Exception as exc:  # noqa: BLE001 —— 兜底，绝不穿透
        return {"ok": False, "stage": "internal",
                "error": "%s: %s" % (type(exc).__name__, exc), "code": None}


# ===========================================================================
# 版本
# ===========================================================================
def dcg_version():
    """仓库内 dcg 的版本号（如 ``"2.2.2"``）。"""
    try:
        return str(dcg.__version__)
    except Exception:  # noqa: BLE001
        return "unknown"
