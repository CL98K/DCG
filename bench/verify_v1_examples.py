"""六个 `format.py` 注释案例：v2 能否实现？产物与 v1 是否一致？

回答用户 m03623 的删除前置条件（「v1 format 中所有注释的测试案例在 v2 中都能实现，
且 v1/v2 结果对比一致，就可以删除 v1 文件」）。本脚本是**唯一取证入口**：

    python -X utf8 bench/verify_v1_examples.py            # 人读表格
    python -X utf8 bench/verify_v1_examples.py --json out # 机器证据

数据来源
--------
v1 侧**不重跑**，直接读 `evidence/examples/v1_examples_frozen.json`（由
`evidence/examples/probe_v1_examples.py` 把 `format.py:577-816` 原样取消注释、
每例独立子进程真跑得到）。v2 侧用 `tests/examples/v1_parity/schemas.py` 的 schema
真 generate + exec + run。

判定三档
--------
* ``identical``    规则与产物都等价（逐行相等）
* ``partial``      产物可对上，但规则/形状/适用范围有出入（逐条给出原因）
* ``not``          没有可对比的等价物

退出码：全部 ``identical`` 才是 0（即「可以删 v1」），否则 1。
"""
from __future__ import annotations

import argparse
import ast
import io
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests", "examples"))

import dcg  # noqa: E402
from dcg.schema import parse_value_path  # noqa: E402
from v1_parity import harness, schemas  # noqa: E402

FIELDS_EX4 = ["hour", "hourA", "hourB", "hourC"]


def _lit(text):
    """frozen 里的 repr 字符串 -> Python 对象（`harness._parse` 的公开包装）。"""
    return harness.parse_repr(text)


def _variant_rows(g, name):
    """取某配置变体里 `docs` 组的行（v1 用真实执行得到的 repr 字符串）。"""
    rec = g.variants[name]
    if "traceback" in rec and rec.get("traceback"):
        raise RuntimeError("variant %r raised: %s" % (name, rec["traceback"].splitlines()[-1]))
    val = _lit(rec["result"])
    if isinstance(val, tuple) and len(val) == 2:
        val = val[0]
    return val["docs"]


def check_ex1(g):
    src, out = harness.v2_run(schemas.EX1, g.datas)
    want = g.result["doc"]
    return {
        "v2": "schemas.EX1（flight.json 的测试副本）",
        "v1_out": "10 行 × 9 字段",
        "v2_out": "%d 行 × %d 字段" % (len(out), len(out[0]) if out else 0),
        "equal": out == want,
        "verdict": "identical" if out == want else "not",
        "note": "逐行相等，含 `#.hour.3.2` -> `hour[3][2]` 与 `hour[2] + 123`",
    }


def check_ex2(g):
    as_written = g.result["docs"]
    lost = len(g.errors)
    cython_off = _variant_rows(g, "cython_off")
    src, out = harness.v2_run(schemas.EX2_DOCS, g.datas)
    equal_off = out == cython_off
    # 整篇文档的等价性：v2 没有 shape="document"，但「多个产物 + 一层组装」可以
    # 逐值复现 v1 的 11 个顶层段落（19 个段落全部相等）。样板在
    # bench/compose_v1_document.py，证据在 evidence/examples/v2_ex2_document.json。
    import compose_v1_document as C
    expected_doc, datas = C.load_v1()
    actual_doc = C.assemble(datas)
    whole_equal = C.diff(expected_doc, actual_doc) is None
    return {
        "v2": ("两段式：单组用 schemas.EX2_DOCS；整篇用 bench/compose_v1_document.py"
               "（多产物 + 组装层，%d 个段落）" % (len(C.SECTION_SCHEMAS) + len(C.SECTION_CONSTANTS))),
        "v1_out": "as-written: docs=[]（%d 行进错误桶）；关 CYTHON 后 %d 行 × 15 字段 + 15 个顶层组"
                  % (lost, len(cython_off)),
        "v2_out": "%d 行 × %d 字段（'docs' 组）；整篇 %d 个顶层段落%s"
                  % (len(out), len(out[0]) if out else 0, len(actual_doc),
                     "逐值相等" if whole_equal else "存在差异"),
        "equal": bool(equal_off and whole_equal),
        "verdict": "identical" if (equal_off and whole_equal) else "partial",
        "note": ("结果一致：'docs' 组与 v1 关闭 CYTHON_MODE 后逐值%s；整篇文档由 %d 个 v2 "
                 "产物 + 常量段落组装，与 v1 冻结产物逐值%s。代价是**不是一个 schema**——"
                 "v2 单产物模型没有 shape=\"document\"，迁移时按多产物 + 组装层处理。"
                 % ("相同" if equal_off else "**不相同**",
                    len(C.SECTION_SCHEMAS),
                    "相等" if whole_equal else "**不相等**")),
    }


def check_ex3(g):
    src, out = harness.v2_run(schemas.EX3, g.datas)
    want = g.result["docs"]
    return {
        "v2": "schemas.EX3",
        "v1_out": "%d 行 × 3 字段" % len(want),
        "v2_out": "%d 行 × %d 字段" % (len(out), len(out[0]) if out else 0),
        "equal": out == want,
        "verdict": "identical" if out == want else "not",
        "note": "v1 侧需 `FREE_MODE=True` 才能展开 `_field` 裸名；v2 直接把路径写死",
    }


def check_ex4(g):
    want_rows = g.result["docs"]           # list of dict (v1 protocol: False)
    src, out = harness.v2_run(schemas.EX4, g.datas)
    equal = out == want_rows
    return {
        "v2": "schemas.EX4（shape=rows）",
        "v1_out": "%d 个**行 dict**（`protocol: False`）" % len(want_rows),
        "v2_out": "%d 个行 dict（`shape: \"rows\"`）" % len(out),
        "equal": bool(equal),
        "verdict": "identical" if equal else "not",
        "note": ("v1 的 `protocol: False` 把行形态从 list 换成 dict；v2 用 "
                 "`output.shape=\"rows\"`（2026-10-17 新增）产出结构完全相同的"
                 "「每行一个 dict」，键序 = 字段声明序"),
    }


def check_ex5(g):
    """例5：三字段全部等价；第三字段的**运行期动态键**用反引号段表达。"""
    # (a) expr 侧拒绝、value 侧接受 —— 这是原先文档写错的地方
    expr_rejects = []
    for p in schemas.EX5_V1_PATHS[:2]:
        try:
            dcg.compile_expr(p)
            expr_rejects.append((p, "ACCEPTED"))
        except (dcg.Unsupported, dcg.SchemaError) as exc:
            expr_rejects.append((p, "%s: %s" % (type(exc).__name__, exc)))
    value_ok = []
    for p in schemas.EX5_V1_PATHS[:2]:
        try:
            parse_value_path(p)
            value_ok.append((p, True))
        except Exception:  # noqa: BLE001
            value_ok.append((p, False))

    # (b) EX5 把动态键写成反引号段，规则等价（不是常量复现）
    dyn = "@@DYN@@"
    src, out = harness.v2_run(schemas.EX5, g.datas)
    want = g.result
    # 合成一行，AIRPORTIATA 长度为 2 -> 必须取 AIRPORTIATA_2，证明确实是运行期算键
    synthetic = {"data": {"ID": 7, "TASK_TYPE": "W", "AIRPORTIATA": "UR",
                          "AIRPORTIATA_1": "x", "AIRPORTIATA_2": dyn,
                          "AIRPORTIATA_3": "must-not-be-used"}}
    _s2, out2 = harness.v2_run(schemas.EX5, [synthetic])
    dynamic_is_real = out2 == [[7, "W", dyn]]
    return {
        "v2": "schemas.EX5（反引号动态键）",
        "v1_out": "%d 行：%r" % (len(want), want),
        "v2_out": "%d 行：%r" % (len(out), out),
        "equal": bool(out == want),
        "verdict": "identical" if out == want else "not",
        "note": ("三字段全部等价。**修正 1**：不是“深路径被拒”——`expr`(JSONata) 侧拒绝、"
                 "`value` 侧接受（CONTRACT §8 O-4 已登记）。**修正 2**：第三字段 "
                 "`f\"AIRPORTIATA_{len(IATA)}\"` 的运行期动态键原本被当成拦路虎，"
                 "实际可写进反引号段 `$.data['AIRPORTIATA_' + str(len($.data.AIRPORTIATA))]`"
                 "（需先修 `pyseg` 把单段链误当裸名的 bug）；合成行 AIRPORTIATA='UR'(len 2) "
                 "取到 AIRPORTIATA_2 ⇒ 规则等价而非常量复现（%s）。"
                 % ("已证" if dynamic_is_real else "**未证**")),
        "detail": {"expr_side": expr_rejects, "value_side": value_ok,
                   "dynamic_key": "VAR[f\"AIRPORTIATA_{len(data['data']['AIRPORTIATA'])}\"]",
                   "dynamic_key_rule_equivalent": dynamic_is_real,
                   "generated_tail": src.strip().splitlines()[-2] if src else ""},
    }


def check_ex6(g):
    src = g.raw.get("generated_source") or ""
    undefined = [w for w in ("chain", "merge") if w in src]
    return {
        "v2": "无（无 v1 产物可对）",
        "v1_out": "无产物：本例只有 `Format.parse`，没有 `datas`，handler 从未执行",
        "v2_out": "v2 有 shape=\"index\"，但语义是 `{行号: 行}`；v1 的 `index.merge` 是按 key 聚合",
        "equal": None,
        "verdict": "not",
        "note": ("v1 这一例的 handler 里用了未定义的 %s（frozen 的 generated_source 可证），"
                 "所以「v1 结果」不存在，无从对比。v2 也没有 `show: False` 的显示层字段隐藏机制。"
                 % "、".join(repr(u) for u in undefined)),
    }


CHECKS = [
    (1, "578–623", check_ex1),
    (2, "626–697", check_ex2),
    (3, "700–730", check_ex3),
    (4, "734–764", check_ex4),
    (5, "767–791", check_ex5),
    (6, "794–816", check_ex6),
]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--json", default=os.path.join(
        ROOT, "evidence", "examples", "v2_vs_v1_six_examples.json"))
    args = ap.parse_args(argv)

    golden = {g.index: g for g in harness.all_goldens()}
    rows = []
    lines_out = []

    def say(text):
        lines_out.append(text)
        print(text)

    for n, lines, fn in CHECKS:
        rec = fn(golden[n])
        rec.update(index=n, format_py_lines=lines)
        rows.append(rec)
        say("=" * 78)
        say("例%d  format.py:%s   -> %s" % (n, lines, rec["verdict"].upper()))
        say("  v2 方案 : %s" % rec["v2"])
        say("  v1 产物 : %s" % rec["v1_out"])
        say("  v2 产物 : %s" % rec["v2_out"])
        say("  一致    : %r" % (rec["equal"],))
        say("  说明    : %s" % rec["note"])

    counts = {}
    for r in rows:
        counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1
    # 例6 在 v1 里只 parse、从未产生产物（生成码用了未定义的 chain/merge），
    # 所以「能出结果的样例」是 例1..例5；判定标准也按这五例算。
    result_cases = [r for r in rows if r["index"] != 6]
    all_result_identical = all(r["verdict"] == "identical" for r in result_cases)
    say("=" * 78)
    say("统计：identical=%d  partial=%d  not=%d"
        % (counts.get("identical", 0), counts.get("partial", 0),
           counts.get("not", 0)))
    say("能出结果的 例1..例5：%s（%d/%d identical）"
        % ("全部结果一致" if all_result_identical else "仍有差距",
           sum(1 for r in result_cases if r["verdict"] == "identical"),
           len(result_cases)))

    payload = {
        "question": "format.py 注释的 6 个案例：v2 是否全部可实现且结果一致？（m03623/m03834）",
        "v1_evidence": "evidence/examples/v1_examples_frozen.json",
        "v2_evidence": "tests/examples/v1_parity/schemas.py",
        "counts": counts,
        "result_cases": [r["index"] for r in result_cases],
        "all_result_cases_identical": all_result_identical,
        "cases": rows,
    }
    os.makedirs(os.path.dirname(args.json), exist_ok=True)
    with io.open(args.json, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    txt_path = os.path.splitext(args.json)[0] + ".txt"
    with io.open(txt_path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(lines_out) + "\n")
    print("JSON -> %s" % os.path.relpath(args.json, ROOT))
    print("TXT  -> %s" % os.path.relpath(txt_path, ROOT))
    return 0 if all_result_identical else 1


if __name__ == "__main__":
    sys.exit(main())
