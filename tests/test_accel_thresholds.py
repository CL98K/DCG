"""`evidence/accel_thresholds.py` 判据脚本自身的守护测试。

这个脚本是「要不要把某个算子交给 Rust」的唯一判据来源（`--target` 默认 1.3×，
元素门槛从 `dcg/runtime.py` 源码里现读）。它自己出错不会有任何测试变红 ——
报告会照样打印一张漂亮的表，只是数字对不上源码。所以这里钉两件事：

1. `current_thresholds()` 真的把现役的每条门控都抽出来了，且抽到的值与
   `dcg/runtime.py` 的现役常量一致；
2. 源码里的门槛改了，抽取结果跟着变（用临时假源码跑，不动真实 `runtime.py`）。
"""
import os
from pathlib import Path
import sys
import tempfile
import textwrap
import unittest

ROOT = Path(__file__).resolve().parents[1]

#: 脚本不是包，用路径导入（与 `tests/test_accel_gate.py` 的做法一致）。
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from evidence import accel_thresholds as AT  # noqa: E402

#: 期望被抽到的门控。这里**故意不写死条数** —— 每新增一条门控都要改集合，
#: 但测试名不该跟着数字变（上一版叫 `test_exactly_six_gates`，加 `_fb` 之后
#: 名字就成了谎话）。
#:
#: `_fgi` **不该出现**：它的门控是
#: `picture not in _FGI_ALPHA and "." not in picture`，`_FGI_ALPHA` 是 frozenset
#: （判据是集合成员关系，不是可比大小的数值），没有数值阈值。
#:
#: 注意这七个数的**量纲并不相同**（见 `accel_thresholds.current_thresholds`
#: 的 docstring）：`_av`/`_cl`/`_ds`/`_mn`/`_mx`/`_zp` 是「元素个数」，
#: `_fb` 是「整数绝对值」。集合相等只保证「没有漏抽、没有多抽」，
#: 不要拿它们横向比大小。
EXPECTED_KEYS = {"_av", "_cl", "_ds", "_fb", "_mn", "_mx", "_zp"}


class TestCurrentThresholds(unittest.TestCase):
    """判据脚本从源码抽阈值：键集合与取值都要与 `dcg/runtime.py` 一致。"""

    def test_gate_set_matches_expected(self):
        got = AT.current_thresholds()
        self.assertEqual(EXPECTED_KEYS, set(got),
                         "判据脚本抽到的门控集合变了：%r" % (sorted(got),))

    def test_values_match_runtime_constants(self):
        from dcg import runtime as rt

        got = AT.current_thresholds()
        self.assertEqual(got["_zp"], rt._ZP_MIN)
        # `_cl` 有 dict / 序列两条分支，汇总取较大者（序列门槛更保守）。
        self.assertEqual(got["_cl"], max(rt._CL_MIN_DICT, rt._CL_MIN_SEQ))
        # `_mn`/`_mx`/`_av` 是同一组数组门槛（源码里的数字字面量），`_ds` 更小。
        self.assertEqual(got["_mn"], got["_mx"])
        self.assertEqual(got["_mn"], got["_av"])
        self.assertGreater(got["_mn"], got["_ds"],
                           "序列门槛应比 distinct 门槛更保守，否则说明抽错了行")
        # `_fb` 的量纲与上面几条**不同**：上面是「元素个数」，它是「整数绝对值」。
        self.assertEqual(10 ** 6, got["_fb"],
                         "`$formatBase` 的量级门槛应是 10**6（|n| >= 10**6 才走 Rust）")
        self.assertEqual(got["_fb"], rt._FB_RUST_MIN,
                         "抽取到的 `_fb` 门槛与 `dcg/runtime.py` 的现役常量不一致")

    def test_fgi_is_not_a_numeric_threshold(self):
        """`_fgi` 的门控是 frozenset 成员关系，不是元素个数门槛。"""
        self.assertNotIn("_fgi", AT.current_thresholds(),
                         "`_FGI_ALPHA` 是 frozenset，把它当数值门槛会凭空造出"
                         "一个不存在的尺寸判据")


class TestThresholdExtractionTracksSource(unittest.TestCase):
    """改源码里的门槛，抽取结果必须跟着变（否则报告会与实现漂移）。"""

    def _fake_module(self, body):
        """把一段假 runtime 源码落成临时模块，返回其路径。"""
        tmp = tempfile.mkdtemp(prefix="dcg_thr_")
        path = os.path.join(tmp, "fake_runtime.py")
        with open(path, "w", encoding="utf-8") as f:
            f.write(textwrap.dedent(body))
        return path

    def test_gate_conditions_pick_up_changed_numbers(self):
        src = '''
        def _mn(a):
            """doc"""
            if _ACC is not None and len(a) >= 999:
                r = _ACC.mn(a)
                if r is not None:
                    return r
            return None
        '''
        conds = AT._gate_conditions(textwrap.dedent(src))
        self.assertEqual(1, len(conds), conds)
        self.assertIn("999", conds[0],
                      "抽取逻辑没有读到改动后的门槛数字：%r" % (conds[0],))

    def test_const_reference_is_resolved_through_module(self):
        """`min(len(c) ...) >= _ZP_MIN` 这类常量门槛要从模块里现取，不能写死。"""
        src = '''
        def _zp(*cols):
            """doc"""
            if _ACC is not None and len(cols) == _ZP_COLS and min(len(c) for c in cols) >= _ZP_MIN:
                r = _ACC.zp(*cols)
                if r is not None:
                    return r
            return None
        '''
        conds = AT._gate_conditions(textwrap.dedent(src))
        self.assertEqual(1, len(conds), conds)
        consts = AT._GATE_CONST.findall(conds[0])
        self.assertIn("_ZP_MIN", consts,
                      "常量门槛没被识别出来：%r" % (conds[0],))
        self.assertIn("_ZP_COLS", consts)
        # 数字字面量正则只认下界，`==` 的列数条件不该被当成尺寸门槛。
        self.assertEqual([], AT._GATE_NUM.findall(conds[0]),
                         "`==` 分支被误当成尺寸门槛：%r" % (conds[0],))

    def test_equal_comparison_is_not_a_size_threshold(self):
        """`len(parts) == 1`（`_fnm` 里的分支）不是尺寸门槛，不能收进报告。"""
        src = '''
        def _fnm(v):
            """doc"""
            if _ACC is not None and len(parts) == 1:
                r = _ACC.fnm(v)
                if r is not None:
                    return r
            return None
        '''
        conds = AT._gate_conditions(textwrap.dedent(src))
        self.assertEqual(1, len(conds))
        self.assertEqual([], AT._GATE_NUM.findall(conds[0]),
                         "`== 1` 被当成尺寸门槛（会让 `_fnm` 凭空出现在报告里）")


class TestDefaultTarget(unittest.TestCase):
    """`--target` 默认值就是用户口径的倍级门槛：比 Python 快 1.3 倍就用 Rust。"""

    def test_default_target_is_1_3(self):
        parser = AT.build_parser()
        args = parser.parse_args([])
        self.assertAlmostEqual(1.3, args.target, places=6,
                               msg="判据脚本默认门槛不是 1.3×（用户 2026-11 指令）")

    def test_target_can_be_overridden(self):
        parser = AT.build_parser()
        self.assertAlmostEqual(1.5, parser.parse_args(["--target", "1.5"]).target)


if __name__ == "__main__":
    unittest.main()
