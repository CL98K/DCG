"""双活门控的等价性守护：装了 `dcg._accel` 时，helper 的结果必须与纯 Python 逐值相同。

这组测试的做法不是「猜 Rust 该返回什么」，而是**把纯 Python 版现算一遍**：
用 `inspect.getsource` 取出 helper 源码，`exec` 到一个 `_ACC = None` 的命名空间，
得到「没有扩展时会走的那条路」，再和现役（可能走了 Rust）的结果比较。

这样无论本机有没有编译扩展都成立：
* 没扩展 → 两条路是同一份代码，测试等价于恒真（仍会跑，防止门控写错分支）；
* 有扩展 → 真正覆盖 Rust 与 Python 的逐值一致性。

脏数据是重点：`bool` 是 `int` 的子类但 `_eq` 规定它与非 bool 永不相等；`NaN != NaN`；
`i64` 之外的大整数；`float` 与 `int` 在同一集合里要合并；`UNDEF`；空数组；标量单值提升。
"""
import importlib
import inspect
import math
import os
from pathlib import Path
import random
import re
import shutil
import sys
import unittest
import warnings

from dcg import runtime as rt

ROOT = Path(__file__).resolve().parents[1]

GATED = ("_mn", "_mx", "_av", "_ds")

#: 2.2.2 新注册的三臂。它们的签名与上面四个不同（`_zp` 是变参、`_fgi` 是二元），
#: 所以不进 `GATED` 的批量对拍，各自有专用用例（见文件后半段的 `Test*Arms`）。


def _pure(fn):
    """同源代码、但强制 `_ACC = None`：这就是「没装扩展」时的实现。"""
    ns = dict(vars(rt))
    ns["_ACC"] = None
    exec(compile(inspect.getsource(fn), "<pure>", "exec"), ns)
    return ns[fn.__name__]


def _same(x, y):
    """逐值比较：NaN 视作相等（同一输入两次算出的 NaN 应视为一致），类型必须相同。"""
    if type(x) is not type(y):
        return False
    if isinstance(x, float) and math.isnan(x) and math.isnan(y):
        return True
    if isinstance(x, (list, tuple)):
        return len(x) == len(y) and all(_same(a, b) for a, b in zip(x, y))
    if isinstance(x, dict):
        return x.keys() == y.keys() and all(_same(x[k], y[k]) for k in x)
    return x is y or x == y


class _IntSub(int):
    """`int` 的子类：`is_exact_instance_of::<PyInt>` 为假，必须落回 Python。"""


def _call(fn, *args):
    """`(异常类型, 值)`：让「两条路抛同样的异常」也能被逐值对拍覆盖。"""
    try:
        return None, fn(*args)
    except BaseException as e:  # noqa: BLE001
        return type(e), None


def _spy_acc():
    """返回 `(calls, wrap)`：`calls` 记录 Rust 被调用的函数名，用于断言门控边界。"""
    calls = []

    class _Spy:
        def __init__(self, inner):
            self._inner = inner

        def __getattr__(self, name):
            fn = getattr(self._inner, name)

            def wrapper(*a, **k):
                calls.append(name)
                return fn(*a, **k)

            return wrapper

    return calls, _Spy


ATOMS = [
    0, 1, -1, 7, 1.0, -0.0, 0.5, True, False, None, "a", "", "1",
    2 ** 63, -(2 ** 63) - 1, 10 ** 20, float("nan"), float("inf"), float("-inf"),
    rt.UNDEF, [1], [1, 1], {"k": 1}, (1, 2), _IntSub(3),
]


def _random_case(rng):
    kind = rng.randrange(6)
    if kind == 0:                                   # 纯 int（Rust 的快路径）
        n = rng.choice([0, 1, 2, 3, 4, 7, 8, 9, 40])
        return [rng.choice([0, 1, -1, 5, 2 ** 62, -(2 ** 62), 7]) for _ in range(n)]
    if kind == 1:                                   # 脏标量混合
        n = rng.choice([0, 1, 2, 4, 8, 20])
        return [rng.choice(ATOMS) for _ in range(n)]
    if kind == 2:                                   # 大范围 int
        n = rng.choice([1, 2, 4, 8, 16])
        return [rng.randrange(-10 ** 6, 10 ** 6) for _ in range(n)]
    if kind == 3:                                   # tuple 入参
        n = rng.choice([0, 2, 4, 8])
        return tuple(rng.choice([1, 2, 3, True, 1.5]) for _ in range(n))
    if kind == 4:                                   # 标量（单值提升）
        return rng.choice(ATOMS)
    n = rng.choice([4, 8, 16, 32])                   # bool/数值/None 混合
    return [rng.choice([True, 1, 1.0, 0, None, "x"]) for _ in range(n)]


class TestAccelGateEquivalence(unittest.TestCase):
    """门控不得改变任何结果；扩展不可用时的代价只是一次 `is not None`。"""

    maxDiff = None

    def test_random_batches_match_pure_python(self):
        rng = random.Random(20261101)
        for _ in range(1200):
            a = _random_case(rng)
            for name in GATED:
                with self.subTest(helper=name, arg=repr(a)[:120]):
                    self.assertTrue(
                        _same(getattr(rt, name)(a), _pure(getattr(rt, name))(a)),
                        "%s 在 %r 上与纯 Python 版不一致" % (name, a),
                    )

    def test_exceptions_are_identical(self):
        """两条路必须抛同样的异常类型（或都不抛）——不能一边 TypeError 一边给值。"""
        rng = random.Random(20261102)
        for _ in range(300):
            a = _random_case(rng)
            for name in GATED:
                live = getattr(rt, name)
                base = _pure(live)
                try:
                    got = live(a)
                    gerr = None
                except BaseException as e:  # noqa: BLE001
                    got, gerr = None, type(e)
                try:
                    exp = base(a)
                    eerr = None
                except BaseException as e:  # noqa: BLE001
                    exp, eerr = None, type(e)
                with self.subTest(helper=name, arg=repr(a)[:120]):
                    self.assertIs(gerr, eerr)
                    if gerr is None:
                        self.assertTrue(_same(got, exp))

    def test_fixed_edge_cases(self):
        cases = [
            ([], "_mn"), ([], "_mx"), ([], "_av"), ([], "_ds"),
            ([True, 1], "_mn"), ([True, 1], "_ds"),
            ([1, 1.0, True, False], "_ds"),
            ([2 ** 62, 2 ** 62], "_av"),           # 和超 2^53：必须落回 Python
            ([2 ** 62, 2 ** 62], "_mn"),
            ([float("nan")], "_mn"), ([float("nan")], "_ds"),
            ([1, 2, 3, 4, 5, 6, 7, 8], "_mn"),
            ([1, 2, 3, 4, 5, 6, 7, 8], "_av"),
            ([3, "a", 5, None, 2, 9, 1, 4], "_mn"),
            ([3, "a", 5, None, 2, 9, 1, 4], "_ds"),
            (1, "_mn"), (1, "_ds"), (rt.UNDEF, "_mn"), (rt.UNDEF, "_ds"),
        ]
        for a, name in cases:
            with self.subTest(helper=name, arg=repr(a)):
                self.assertTrue(_same(getattr(rt, name)(a), _pure(getattr(rt, name))(a)))

    def test_bool_never_merges_with_int(self):
        """`_eq(True, 1)` 是 False，所以 `$distinct` 必须保留 `True` 与 `1` 两个元素。"""
        self.assertEqual(rt._ds([True, 1, 1.0]), [True, 1])
        self.assertEqual(rt._ds([1, True]), [1, True])

    def test_average_matches_big_integer_division(self):
        """和超过 2^53 时 Rust 侧必须放弃，Python 用大整数算出精确结果。"""
        a = [2 ** 62, 2 ** 62, 3]
        got = rt._av(a)
        self.assertEqual(got, sum(a) / 3)
        self.assertIsInstance(got, float)

    def test_gate_absent_gives_identical_result(self):
        """显式把后端摘掉：结果与装着时一致，且不抛异常。"""
        saved = rt._ACC
        try:
            rt._ACC = None
            for name in GATED:
                a = list(range(1, 40))
                self.assertTrue(_same(getattr(rt, name)(a), _pure(getattr(rt, name))(a)))
        finally:
            rt._ACC = saved


class TestZipArm(unittest.TestCase):
    """`$zip` 臂：变参 + 列数/列长双重门控（`_ZP_COLS` / `_ZP_MIN`）。"""

    maxDiff = None

    CASES = [
        (),
        ([1, 2, 3], [4, 5, 6]),
        ([1, 2, 3], [4, 5]),                     # 按最短截断
        ([1, 2, 3], ["a", "b", "c"], [True, False, None]),
        ((1, 2), (3, 4)),                        # tuple 入参
        ([1, 2], (3, 4)),
        ([], []),
        ([1, 2], []),                            # 空列 -> 空结果
        ([1, 2], [3, 4], [5, 6], [7, 8], [9, 10]),
        ([1, 2], 3),                             # 非 seq -> UNDEF
        (1, 2),
        ([rt.UNDEF, 1], [2, rt.UNDEF]),          # undefined 原样搬运
        ([rt.UNDEF] * 3, [None] * 3),
        ([{"k": 1}, [1]], [[2], {"k": 2}]),      # 逐元素引用复制而非深拷贝
    ]

    def test_matches_pure_python(self):
        base = _pure(rt._zp)
        for args in self.CASES:
            with self.subTest(args=repr(args)[:100]):
                self.assertTrue(_same(rt._zp(*args), base(*args)))

    def test_long_columns_match_pure_python(self):
        """跨过阈值：扁平列的逐元素搬运必须与列表推导逐值相同。"""
        base = _pure(rt._zp)
        for n in (self._min() - 1, self._min(), 3000):
            a = list(range(n))
            b = [x * 2 for x in range(n)]
            with self.subTest(n=n):
                self.assertTrue(_same(rt._zp(a, b), base(a, b)))

    @staticmethod
    def _min():
        return rt._ZP_MIN

    def test_threshold_is_exclusive_below(self):
        """`n = _ZP_MIN - 1` 不许进 Rust，`n = _ZP_MIN` 必须进（边界归属明确）。"""
        calls, wrap = _spy_acc()

        saved = rt._ACC
        try:
            rt._ACC = wrap(saved)
            small = self._min() - 1
            rt._zp(list(range(small)), list(range(small)))
            self.assertEqual(calls, [], "低于阈值时不得调用 Rust")
            rt._zp(list(range(self._min())), list(range(self._min())))
            self.assertEqual(calls, ["zp"], "到达阈值必须调用 Rust")
        finally:
            rt._ACC = saved

    def test_single_column_never_gates(self):
        """单列 `$zip` 只是「每元素包一层」，Rust 反而更慢，必须不走门控。"""
        calls, wrap = _spy_acc()

        saved = rt._ACC
        try:
            rt._ACC = wrap(saved)
            rt._zp(list(range(rt._ZP_MIN * 4)))
            self.assertEqual(calls, [])
        finally:
            rt._ACC = saved

    def test_more_than_two_columns_never_gates(self):
        """三列起 Rust 的收益被 Python 侧行构造摊薄，实测过不了 1.3× 门槛。

        `_ZP_COLS` 是**上界**而不是下界：列数一多，Python 的
        `[[c[i] for c in cols] for i in range(n)]` 里的跨语言边界成本占比下降，
        而 Rust 逐行建 `PyList` 的成本没下降 —— 1.3× 门槛重测：三列 n=192–320
        为 0.989–0.999×、四列 0.995–1.040×，仍不到门槛（重测见
        `evidence/accel_threshold_1_3.md` §2）。
        """
        calls, wrap = _spy_acc()

        saved = rt._ACC
        try:
            rt._ACC = wrap(saved)
            n = rt._ZP_MIN * 2
            for ncols in range(rt._ZP_COLS + 1, rt._ZP_COLS + 3):
                rt._zp(*[list(range(n)) for _ in range(ncols)])
            self.assertEqual(calls, [], "超过 _ZP_COLS 列时不得调用 Rust")
        finally:
            rt._ACC = saved


class TestCloneArm(unittest.TestCase):
    """`$clone` 臂：本层宽度门槛（dict `_CL_MIN_DICT`、序列 `_CL_MIN_SEQ`）+ Rust 深度上限 64。

    下面 `test_sequence_threshold_is_higher_than_dict` 的 docstring 记了实测比值，
    读的时候注意口径：比值随**元素类型**变化很大（扁平 int/str 与容器差近一倍），
    门槛是按最不利的那一类（`list<int>`）定的。
    """

    maxDiff = None

    def _deep(self, n):
        v = 1
        for _ in range(n):
            v = [v]
        return v

    @staticmethod
    def _spy():
        return _spy_acc()

    def test_matches_pure_python(self):
        base = _pure(rt._cl)
        cases = [
            1, 0, "s", True, None, rt.UNDEF, 1.5,
            [], {}, [1, 2], {"k": 1, "j": [2, {"n": None}]},
            (1, 2), ((1,), (2,)), [rt.UNDEF, [rt.UNDEF]], {"k": rt.UNDEF},
            list(range(rt._CL_MIN_SEQ)), tuple(range(rt._CL_MIN_SEQ)),
            dict(("k%d" % i, i) for i in range(rt._CL_MIN_DICT)),
            self._deep(60), self._deep(70), self._deep(200),
        ]
        for c in cases:
            with self.subTest(c=repr(c)[:80]):
                self.assertTrue(_same(rt._cl(c), base(c)))

    def test_result_is_independent_copy(self):
        """深拷贝不是浅拷贝：改副本不能影响原对象。"""
        src = {"a": [1, {"b": 2}]}
        out = rt._cl(src)
        self.assertIsNot(out, src)
        out["a"].append(3)
        out["a"][1]["b"] = 99
        self.assertEqual(src, {"a": [1, {"b": 2}]})

    def test_width_threshold_is_exclusive_below(self):
        """门槛是排他的：dict 看 `_CL_MIN_DICT`、序列看 `_CL_MIN_SEQ`，低一档一律不进 Rust。"""
        calls, spy_cls = self._spy()
        saved = rt._ACC
        try:
            rt._ACC = spy_cls(saved)
            rt._cl(dict(("k%d" % i, i) for i in range(rt._CL_MIN_DICT - 1)))
            rt._cl(list(range(rt._CL_MIN_SEQ - 1)))
            rt._cl(tuple(range(rt._CL_MIN_SEQ - 1)))
            self.assertEqual(calls, [], "低于宽度门槛时不得调用 Rust")
            rt._cl(dict(("k%d" % i, i) for i in range(rt._CL_MIN_DICT)))
            self.assertEqual(calls, ["cl"], "dict 到达门槛必须调用 Rust")
            rt._cl(list(range(rt._CL_MIN_SEQ)))
            self.assertEqual(calls, ["cl", "cl"], "序列到达门槛必须调用 Rust")
        finally:
            rt._ACC = saved

    def test_sequence_threshold_is_higher_than_dict(self):
        """dict 与序列的阶跃点不同：序列门槛必须严格高于 dict（实测 list 宽 8 只有 1.39–1.66×）。

        **上面这个区间的口径**：直接计时 `rt._cl`、元素为**扁平 int 或 str** 的第三轮
        独立测量（详见 `evidence/accel_threshold_1_3.md` §5）。它不能当成「任何元素
        都这样」来读 —— 比值强烈依赖元素类型：同为 `w=8`，ABBA 11 轮取中位数的重测
        给出 `list<int>` 1.30–1.58×（中位 1.46×），而 `list<dict>` 2.47–2.60×
        （中位 2.55×）。容器元素让 Python 侧的递归深拷贝更贵、Rust 的相对优势更大，
        所以元素是容器时比值**更高**，1.39–1.66× 是最不利的那一类。

        门槛取 8 而不是 4，正是按最不利口径（`list<int>`）定的：同口径下宽 4 的中位
        只有 0.97×，也就是说宽 4 确实不安全。三处看似矛盾的区间（1.39–1.66× /
        1.30–1.58× / 2.47–2.60×）差别来自**口径不同 + 元素类型未控制**，不是同一个
        量的三个值。
        """
        self.assertGreater(rt._CL_MIN_SEQ, rt._CL_MIN_DICT)

    def test_deep_overflow_falls_back(self):
        """宽够门槛但深 70 层：Rust `deep_copy` 返回 `None`，门控必须落回 Python。"""
        calls, wrap = _spy_acc()
        deep = [self._deep(70)] * rt._CL_MIN_SEQ
        self.assertIsNone(rt._ACC.cl(deep), "深度超过 64 时 Rust 应返回 None")
        saved = rt._ACC
        try:
            rt._ACC = wrap(saved)
            out = rt._cl(deep)
            self.assertEqual(calls, ["cl"], "门控应先把宽够门槛的容器交给 Rust")
        finally:
            rt._ACC = saved
        self.assertTrue(_same(out, _pure(rt._cl)(deep)))

    def test_scalar_top_level_is_undefined(self):
        for c in (1, "a", True, None, 1.5):
            with self.subTest(c=c):
                self.assertIs(rt._cl(c), rt.UNDEF)


class TestFormatIntegerArm(unittest.TestCase):
    """`$formatInteger` 臂：Rust 只在窄域外回退 —— 回退臂的结局必须与 Python 相同。

    这里特意包含**超出 i128 的整数**与**超出 trillion 量级的英文单词**：它们曾是
    `o_fgi` 复用 `as_num` 造成的真实不等价（Rust 给 `undef`，Python 给字符串）。
    """

    maxDiff = None

    PICTURES = ["A", "a", "I", "i", "w", "W", "Ww", "0", "000", "0000",
                "#,##0", "0.0", "", ",0", "0,", "x0", "0000000000", "##0",
                "#", "0#", "##0,000"]
    VALUES = [0, 1, -1, 26, 27, 52, 53, 702, 703, 1000, 1990, 3999, 4000,
              -3999, -1.0, 0.5, -0.5, 1.9, -1.9, 2 ** 62, -(2 ** 62),
              2 ** 63, -(2 ** 63), 2 ** 100, -(2 ** 100), 10 ** 18, 10 ** 40,
              True, False, None, "5", rt.UNDEF, 1e29, 1e30,
              float("inf"), float("-inf"), float("nan")]

    def _iter_cases(self):
        for v in self.VALUES:
            for p in self.PICTURES:
                # 罗马图样在大数上产出极长字符串（10**21 约 10**18 个 `M`）：
                # `_roman_num` 已改成整段相乘，双方都会干净地抛 `MemoryError`，
                # `_call` 抓得住，所以这里不再绕开（D-ROMAN-01）。
                yield v, p

    def test_matches_pure_python(self):
        base = _pure(rt._fgi)
        for v, p in self._iter_cases():
            with self.subTest(v=repr(v)[:40], picture=p):
                live = _call(rt._fgi, v, p)
                pure = _call(base, v, p)
                # `int(nan)`/`int(inf)` 在两侧都会抛（ValueError/OverflowError），
                # 门控不得把异常变成值或相反。
                self.assertEqual(live[0], pure[0],
                                 "$formatInteger(%r, %r) 异常类型不一致" % (v, p))
                if live[0] is None:
                    self.assertTrue(_same(live[1], pure[1]),
                                    "$formatInteger(%r, %r) 门控前后不一致" % (v, p))

    def test_big_integer_is_not_undefined(self):
        """回归：`10**40` 超出 i128，Rust 必须回退，Python 有任意精度能算出来。"""
        self.assertEqual(rt._fgi(10 ** 40, "A"), "BJLJJHNKKLZNGAGYVKNAYFMPRFJLP")
        self.assertEqual(rt._fgi(10 ** 40, "a"), "bjljjhnkklzngagyvknayfmprfjlp")
        self.assertEqual(rt._fgi(10 ** 40, "0"), "1" + "0" * 40)

    def test_english_words_beyond_trillion_fall_back(self):
        """`en_words` 只有 5 档量级；超出时必须回退到 Python 的同一条边界行为。"""
        base = _pure(rt._fgi)
        for v in (10 ** 15, 10 ** 18, 2 ** 62):
            for p in ("w", "W", "Ww"):
                with self.subTest(v=v, picture=p):
                    self.assertEqual(_call(rt._fgi, v, p), _call(base, v, p))

    def test_bool_and_non_string_picture_are_undefined(self):
        for v, p in [(True, "0"), (False, "0"), (1, None), (1, 0), (1, ""),
                     (None, "0"), ("5", "0"), (rt.UNDEF, "0")]:
            with self.subTest(v=v, picture=p):
                self.assertIs(rt._fgi(v, p), rt.UNDEF)

    def test_only_decimal_masks_gate(self):
        """门控只开给十进制掩码图样：字母/英文单词/含 `.` 的图样一律不进 Rust。"""
        calls, wrap = _spy_acc()
        saved = rt._ACC
        try:
            rt._ACC = wrap(saved)
            for p in sorted(rt._FGI_ALPHA) + ["#,##0.00", "0.0"]:
                rt._fgi(1234567, p)
            self.assertEqual(calls, [], "非十进制掩码图样不得调用 Rust")
            rt._fgi(1234567, "#,##0")
            self.assertEqual(calls, ["fgi"], "十进制掩码图样必须调用 Rust")
        finally:
            rt._ACC = saved


class TestBuildConfig(unittest.TestCase):
    """手工 `cargo build` 一个 cdylib 扩展模块，需要**两处**配置同时到位。

    这条是 2026-11 CI 首跑抓到的，分两步才修完：

    ① 只开 feature 不够，得先确认它真的开着。`extension-module` 是 cargo feature；
    pyo3-build-config 其实也认 `PYO3_BUILD_EXTENSION_MODULE` 环境变量
    （`impl_.rs::is_extension_module` 两条 or 分支之一），但把它藏在
    `build_accel.py` 的环境 dict 里既看不见、也不好验证，所以统一收进 Cargo.toml。

    ② 开了 feature 之后仍会失败一轮：feature 只解决「别链接 libpython」，剩下
    「未定义符号怎么办」要 macOS 的 `-undefined dynamic_lookup`。pyo3 **只提供**
    `pyo3_build_config::add_extension_module_link_args`，从不自己调用
    （`pyo3` 与 `pyo3-ffi` 的 build.rs 都只做 build_config 转发）；maturin /
    setuptools-rust 会替项目加，`build_accel.py` 这种手工 `cargo build` 不会。
    缺了它的表现：

        ld: symbol(s) not found for architecture arm64
        "_Py_IncRef", referenced from: ... in _accel...rcgu.o

    Linux 的共享对象允许未定义符号拖到运行期、Windows 链接 python3xx.lib 侥幸
    通过，**只有 macOS 当场失败**——而 macOS 恰好是 abi3「一个二进制覆盖 3.8–3.15」
    唯一没被本地验证过的平台，所以直到 CI 首跑才暴露。

    本用例钉住这两处配置本身：以后谁删 feature、删 build.rs、或把 build.rs 里的
    调用删掉，不用等到 CI 跑到 macOS 才发现。
    """

    ROOT = Path(__file__).resolve().parents[1]

    def _cargo_toml(self):
        return (self.ROOT / "dcg" / "_accel" / "Cargo.toml").read_text(encoding="utf-8")

    def test_accel_is_built_as_an_extension_module(self):
        toml = self._cargo_toml()
        self.assertIn('crate-type = ["cdylib"]', toml, "crate-type 必须是 cdylib")

        dep = re.search(r"^pyo3\s*=\s*\{([^}]*)\}", toml, re.M)
        self.assertIsNotNone(dep, "Cargo.toml 里找不到 pyo3 的依赖声明")
        feats = re.search(r"features\s*=\s*\[([^\]]*)\]", dep.group(1))
        self.assertIsNotNone(feats, "pyo3 声明里没有 features 列表")
        names = {n.strip().strip('"') for n in feats.group(1).split(",") if n.strip()}
        self.assertIn("abi3-py38", names)
        self.assertIn("extension-module", names,
                      "缺 extension-module 时产物会按「链接 libpython」链接，"
                      "abi3 下没有可匹配的 libpython，macOS arm64 报 _Py_* 未定义")

        script = (self.ROOT / "build_accel.py").read_text(encoding="utf-8")
        offenders = [ln for ln in script.splitlines()
                     if "PYO3_BUILD_EXTENSION_MODULE" in ln
                     and not ln.lstrip().startswith("#")]
        self.assertEqual([], offenders,
                         "链接配置统一放 Cargo.toml / build.rs，别再靠环境变量开关")

    def test_macos_gets_undefined_dynamic_lookup(self):
        """macOS 手工构建必须自己 emit `-undefined dynamic_lookup`。"""
        build_rs = self.ROOT / "dcg" / "_accel" / "build.rs"
        self.assertTrue(build_rs.is_file(),
                        "缺 dcg/_accel/build.rs：macOS 上编译器不会拿到 "
                        "-undefined dynamic_lookup，链接会报 _Py_* 未定义")
        body = build_rs.read_text(encoding="utf-8")
        live = [ln for ln in body.splitlines() if not ln.lstrip().startswith("//")]
        self.assertTrue(any("add_extension_module_link_args()" in ln for ln in live),
                        "build.rs 里没有调用 pyo3_build_config::"
                        "add_extension_module_link_args()")

        toml = self._cargo_toml()
        self.assertIn("[build-dependencies]", toml,
                      "build.rs 用到 pyo3_build_config，必须声明为 build-dependency")
        dep = re.search(r"^\[build-dependencies\](.*?)(?=^\[|\Z)", toml, re.M | re.S)
        self.assertIsNotNone(dep)
        self.assertIn("pyo3-build-config", dep.group(1))


class TestCIAccelMatrix(unittest.TestCase):
    """`accel` 矩阵的 runner 标签必须**当前仍然存在**。

    已退役的标签不会报错，只会让 job 永远 `queued`：没有 runner 能匹配
    `runs-on`，既不跑也不红，那个平台等于从来没验过。
    """

    #: 已退役的 macOS 标签，禁止再出现在任何平台里。
    RETIRED_MACOS = ("macos-13", "macos-14")

    ROOT = Path(__file__).resolve().parents[1]

    def _accel_legs(self):
        """把 `accel` job 的 matrix.include 解析成 [{'os':…, 'strict_38':…}]。"""
        text = (self.ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        start = text.index("\n  accel:")
        legs, cur = [], None
        for line in text[start:].splitlines():
            m = re.match(r"\s*-\s*os:\s*([A-Za-z0-9._-]+)\s*$", line)
            if m:
                cur = {"os": m.group(1), "strict_38": None}
                legs.append(cur)
                continue
            m = re.match(r"\s*strict_38:\s*(true|false)\s*$", line)
            if m and cur is not None:
                cur["strict_38"] = m.group(1) == "true"
        return legs

    def test_accel_matrix_uses_live_runner_labels(self):
        legs = self._accel_legs()
        self.assertGreaterEqual(len(legs), 4, "accel 矩阵丢了平台：%r" % (legs,))

        for leg in legs:
            self.assertNotIn(
                leg["os"], self.RETIRED_MACOS,
                "%s 这个标签已退役：用它会永远排不上队（job 停在 queued，"
                "既不跑也不红），必须换成在售标签" % leg["os"])

        # x64 平台是 abi3「真有 Python 3.8」的唯一落脚点：标准标签里 macOS 的
        # x86_64 镜像统一带 `-intel` 后缀（`macos-13`/`macos-14` 那种不带后缀
        # 的老写法已经没有了）。
        x64 = [l for l in legs if l["os"].startswith("macos") and l["os"].endswith("-intel")]
        self.assertEqual(len(x64), 1,
                         "macOS x64 平台应当恰好一条（3.8 硬断言靠它）：%r" % (legs,))
        self.assertTrue(x64[0]["strict_38"],
                        "macOS x64 平台必须保留 3.8 硬断言，否则 abi3 覆盖没有结论")

        arm = [l for l in legs if l["os"] == "macos-latest"]
        self.assertEqual(len(arm), 1, "少了 macos-latest（arm64）平台：%r" % (legs,))
        self.assertFalse(arm[0]["strict_38"],
                         "arm64 没有 3.8 预编译产物，只能是软失败平台")


class TestMacOSArtifacts(unittest.TestCase):
    """wheel 里必须带 macOS 的加速产物，否则那两个平台静默退回纯 Python。

    本机没有 macOS，产物只能由 CI 构建（`accel` job 里的 upload-artifact 步骤）
    再下载进仓库；文件名带架构后缀，因为一个目录放不下两个都叫 `_accel.abi3.so`
    的 Mach-O。宿主包按 `sys.platform`/`platform.machine()` 选一个加载。
    """

    ROOT = Path(__file__).resolve().parents[1]
    ACCEL = ROOT / "dcg" / "_accel"

    #: Mach-O 64 位小端 magic；ELF 是 b"\x7fELF"，拿错文件说明放错了。
    MACHO = b"\xcf\xfa\xed\xfe"
    #: Mach-O header 里 cputype 的偏移与取值。
    CPUTYPE_OFFSET = 4
    CPU_ARM64 = 0x0100000C
    CPU_X86_64 = 0x01000007

    def _binary(self, name):
        path = self.ACCEL / name
        self.assertTrue(path.is_file(),
                        "缺 %s：macOS 会退回纯 Python（从 CI 的 accel job "
                        "下载 artifact 后放进 dcg/_accel/）" % name)
        return path.read_bytes()

    def _assert_macho(self, data, cputype, label):
        self.assertEqual(data[:4], self.MACHO,
                         "%s 不是 Mach-O（前 4 字节 %r）" % (label, data[:4]))
        got = int.from_bytes(data[self.CPUTYPE_OFFSET:self.CPUTYPE_OFFSET + 4], "little")
        self.assertEqual(got, cputype,
                         "%s 的架构不对：cputype=%#x" % (label, got))

    def test_macos_arm64_binary_is_shipped(self):
        self._assert_macho(self._binary("_accel-macos-arm64.abi3.so"),
                           self.CPU_ARM64, "arm64 产物")

    def test_macos_x64_binary_is_shipped(self):
        self._assert_macho(self._binary("_accel-macos-x64.abi3.so"),
                           self.CPU_X86_64, "x64 产物")

    def test_host_package_dispatches_by_platform(self):
        body = (self.ACCEL / "__init__.py").read_text(encoding="utf-8")
        self.assertIn("_accel-macos-arm64.abi3.so", body,
                      "宿主包没有 arm64 的文件名")
        self.assertIn("_accel-macos-x64.abi3.so", body,
                      "宿主包没有 x64 的文件名")
        self.assertIn("platform.machine()", body,
                      "宿主包必须按机器架构选产物，不能写死")


def _load_build_script():
    """按路径加载 `build_accel.py`（它 import 的只有标准库，执行不会构建）。"""
    path = ROOT / "build_accel.py"
    spec = importlib.util.spec_from_file_location("dcg_build_accel_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestArtifactNameContract(unittest.TestCase):
    """构建脚本产出的文件名 == 加载器去找的文件名。

    这是 Q-01 的根因：脚本在 macOS 上产出 `_accel.abi3.so`，加载器只找
    `_accel-macos-{arch}.abi3.so`。名字对不上时不会报错 —— `copy2` 不删旧文件，
    于是**上一次**的二进制继续被加载，而 CI 的 `import dcg._accel._accel`
    命中新文件，验的和跑的不是同一个东西。
    """

    ROOT = Path(__file__).resolve().parents[1]

    def test_macos_names_match_on_every_arch_spelling(self):
        build = _load_build_script()
        from dcg import _accel as host

        for machine, arch in (("arm64", "arm64"), ("aarch64", "arm64"),
                              ("x86_64", "x64"), ("AMD64", "x64"), ("x64", "x64")):
            with self.subTest(machine=machine):
                self.assertEqual("_accel-macos-%s.abi3.so" % arch,
                                 build.artifact_name("darwin", machine))
                self.assertEqual(build.artifact_name("darwin", machine),
                                 host._macos_artifact_name(machine),
                                 "构建脚本与加载器对 %r 的产物名不一致" % (machine,))

    def test_non_macos_names_match_the_loader(self):
        build = _load_build_script()
        self.assertEqual("_accel.pyd", build.artifact_name("win32", "AMD64"))
        self.assertEqual("_accel.abi3.so", build.artifact_name("linux", "x86_64"))

    def test_unknown_macos_arch_is_a_loud_error(self):
        build = _load_build_script()
        with self.assertRaises(RuntimeError):
            build.artifact_name("darwin", "ppc")
        from dcg import _accel as host
        self.assertIsNone(host._macos_artifact_name("ppc"),
                          "不认识的架构不该猜一个产物名出来")

    def test_build_script_does_not_hardcode_a_local_toolchain(self):
        """Q-10：发版脚本不能把作者本机的 MinGW 路径当默认值。"""
        body = (self.ROOT / "build_accel.py").read_text(encoding="utf-8")
        # 注释/docstring 里提到那个路径是历史说明，可以留；**代码**里不能再出现。
        offenders = [ln.strip() for ln in body.splitlines()
                     if re.search(r'Path\(\s*["\']D:/', ln)]
        self.assertEqual([], offenders,
                         "build_accel.py 里还有写死的本机路径：%r" % (offenders,))
        self.assertIn("DCG_MINGW_BIN", body,
                      "MinGW 目录应可由 DCG_MINGW_BIN 指定")
        self.assertIn('shutil.which("gcc"', body,
                      "没有环境变量时应按 PATH 上的 gcc 反查目录")


class TestLoaderStatus(unittest.TestCase):
    """加载失败必须出声（Q-05）：有候选却加载不起来 → RuntimeWarning + status()。

    「没编译扩展」是正常情况，那种情况**不能**刷警告 —— 否则源码树里每个
    `import dcg` 都会警告，用户很快就会学会无视它。
    """

    ROOT = Path(__file__).resolve().parents[1]

    #: 假包目录落在**项目内**的 `_tmp/build/` 下，而不是 `tempfile.mkdtemp()`。
    #:
    #: 被测代码 `dcg/_accel/__init__.py::_candidates()` 用 `glob` 列举包目录
    #: （这是 Q-01 的正式修复：macOS 产物名带架构后缀，只能靠列举发现），
    #: 所以这些用例**要求包目录可列举**。而临时目录恰恰是受限环境里最可能
    #: 被剥掉列举权限的地方（见 `tests/conftest.py` 的 `build_dir` 夹具与
    #: `_probe_tmpdir`）：一旦不可列举，`_candidates()` 返回 `[]`，告警文本里
    #: 自然没有候选文件名，用例就会失败于**环境**而不是缺陷
    #: （实测报错：`'_accel.pyd' not found in []`）。
    #:
    #: 用项目内固定路径既保住了 glob 兜底要测的东西，又让目录内容完全由本用例
    #: 掌控（「只有一个候选」「一个候选都没有」都是我们自己写进去的，不靠系统
    #: 临时目录的可列举性）。清理用 `rmtree(ignore_errors=True)`，删除失败只
    #: 影响本用例自己的产物，不影响结果。
    PKG_ROOT = ROOT / "_tmp" / "build" / "accel_gate_loader"

    def _make_package(self, binaries):
        """包目录：复制真实 `__init__.py`，按需塞入（损坏的）二进制。"""
        pkg = self.PKG_ROOT / self._testMethodName
        shutil.rmtree(pkg, ignore_errors=True)
        pkg.mkdir(parents=True)
        self.addCleanup(shutil.rmtree, pkg, ignore_errors=True)
        shutil.copy2(self.ROOT / "dcg" / "_accel" / "__init__.py", pkg / "__init__.py")
        for name, data in binaries.items():
            (pkg / name).write_bytes(data)
        return pkg

    def _import_package(self, pkg, modname):
        spec = importlib.util.spec_from_file_location(
            modname, pkg / "__init__.py", submodule_search_locations=[str(pkg)])
        module = importlib.util.module_from_spec(spec)
        sys.modules[modname] = module
        try:
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                spec.loader.exec_module(module)
        finally:
            sys.modules.pop(modname, None)
            for extra in [n for n in sys.modules if n.startswith(modname + ".")]:
                sys.modules.pop(extra, None)
        return module, caught

    def test_corrupt_binary_warns_with_reason_and_status(self):
        pkg = self._make_package({"_accel.pyd": b"not a real extension"})
        module, caught = self._import_package(pkg, "dcg_fake_accel_corrupt")

        self.assertFalse(module.status()["loaded"])
        self.assertTrue(module.status()["error"],
                        "加载失败但 status()['error'] 是空的")
        self.assertIn("_accel.pyd", module.status()["candidates"])
        messages = [str(w.message) for w in caught
                    if issubclass(w.category, RuntimeWarning)]
        self.assertTrue(messages,
                        "目录里有候选二进制却加载失败，必须发 RuntimeWarning（否则"
                        "双活悄悄退化成单活，用户永远不知道）")
        self.assertIn("_accel.pyd", messages[0], "警告里没说查找过的文件")
        self.assertIn("纯 Python", messages[0], "警告里没说明已退回纯 Python")

    def test_missing_binary_is_silent(self):
        pkg = self._make_package({})
        module, caught = self._import_package(pkg, "dcg_fake_accel_missing")

        self.assertFalse(module.status()["loaded"])
        self.assertEqual([], module.status()["candidates"])
        self.assertEqual([], [w for w in caught if issubclass(w.category, RuntimeWarning)],
                         "源码树里没编译扩展是正常情况，不该警告")

    def test_status_shape(self):
        from dcg import _accel as host

        st = host.status()
        self.assertEqual({"loaded", "path", "error", "candidates", "missing"}, set(st))
        self.assertIsInstance(st["loaded"], bool)
        self.assertIsInstance(st["candidates"], list)
        self.assertIsInstance(st["missing"], list)
        if st["loaded"]:
            self.assertIsNone(st["error"])
            self.assertTrue(os.path.isfile(st["path"]),
                            "status() 报的路径不是真实文件：%r" % (st["path"],))
            self.assertEqual([], st["missing"],
                             "本机加载的二进制导出面不完整：%r" % (st["missing"],))


class TestExportContract(unittest.TestCase):
    """导出面校验（跨平台崩溃的守护）：旧二进制缺符号必须出声，而不是崩在调用点。

    事故形态：`dcg/runtime.py` 无条件调 `_ACC.ue/_ud/_fb`，而仓库里只有 Windows 的
    `.pyd` 是新的 16 导出，Linux/macOS 的 `.so` 还是上一次 CI 的 13 导出产物。
    Windows 全绿、CI 也不会红，但 Linux/macOS 用户一调 `$encodeUrlComponent` 就是
    `AttributeError: 'module' object has no attribute 'ue'` —— **不是退回纯 Python**。
    本机永远测不出来，所以只能靠加载时校验导出面。

    这里用**纯 Python 的假 `_accel` 子模块**（`_accel.py` 而不是二进制）来模拟两种
    产物：标准导入分支会像加载真扩展一样 `from . import _accel` 成功，然后走同一套
    `_check_exports()`。比造假二进制可靠 —— 后者加载阶段就失败了，根本到不了校验。
    """

    ROOT = Path(__file__).resolve().parents[1]

    #: 与 `TestLoaderStatus.PKG_ROOT` 同样的理由：包目录必须**可列举**。
    PKG_ROOT = ROOT / "_tmp" / "build" / "accel_gate_exports"

    #: 上一次 CI 产出的 13 导出面（缺 `fb` / `ud` / `ue`）。
    OLD_EXPORTS = ("av", "cl", "ds", "du", "eu", "fgi", "fms", "fnm", "mn", "mx",
                   "pgi", "tms", "zp")

    def _import_package(self, exports):
        """造一个带假 `_accel` 的包并导入，返回 `(module, 捕获的警告)`。"""
        pkg = self.PKG_ROOT / self._testMethodName
        shutil.rmtree(pkg, ignore_errors=True)
        pkg.mkdir(parents=True)
        self.addCleanup(shutil.rmtree, pkg, ignore_errors=True)
        shutil.copy2(self.ROOT / "dcg" / "_accel" / "__init__.py", pkg / "__init__.py")
        stub = ["# 假扩展：只有这些名字，模拟旧产物。"]
        stub += ["%s = lambda *a, **k: None" % n for n in exports]
        (pkg / "_accel.py").write_text("\n".join(stub) + "\n", encoding="utf-8")

        modname = "dcg_fake_accel_exports_%s" % self._testMethodName
        spec = importlib.util.spec_from_file_location(
            modname, pkg / "__init__.py", submodule_search_locations=[str(pkg)])
        module = importlib.util.module_from_spec(spec)
        sys.modules[modname] = module
        try:
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                spec.loader.exec_module(module)
        finally:
            sys.modules.pop(modname, None)
            for extra in [n for n in sys.modules if n.startswith(modname + ".")]:
                sys.modules.pop(extra, None)
        return module, caught

    def _messages(self, caught):
        return [str(w.message) for w in caught
                if issubclass(w.category, RuntimeWarning)]

    def test_old_binary_warns_and_reports_missing(self):
        """13 导出的旧产物：警告 + `missing` 列出 3 个 + `loaded` 仍为 True。"""
        module, caught = self._import_package(self.OLD_EXPORTS)
        st = module.status()

        self.assertTrue(st["loaded"],
                        "缺符号不该让整个后端作废：13 个还能用，比全不可用强")
        self.assertEqual(["fb", "ud", "ue"], st["missing"],
                         "missing 必须按 REQUIRED_EXPORTS 的顺序列出缺的符号")

        messages = self._messages(caught)
        self.assertEqual(1, len(messages),
                         "缺符号必须发且只发一条 RuntimeWarning：%r" % (messages,))
        msg = messages[0]
        for name in ("fb", "ud", "ue"):
            self.assertIn(name, msg, "警告里没点名缺了 %s" % name)
        self.assertIn("16", msg, "警告里没说源码要求多少个")
        self.assertIn("_accel.py", msg, "警告里没说清是哪个产物文件")
        self.assertIn("重新构建", msg, "警告里没给出可执行的处置动作")

    def test_complete_binary_is_silent(self):
        """16 导出：无警告，`missing == []`。"""
        module, caught = self._import_package(TestExportContract._required())
        st = module.status()

        self.assertTrue(st["loaded"])
        self.assertEqual([], st["missing"])
        self.assertEqual([], self._messages(caught),
                         "导出面完整时不该有任何警告")

    @staticmethod
    def _required():
        from dcg import _accel as host
        return host.REQUIRED_EXPORTS

    def test_host_package_exports_match_the_matrix(self):
        """`__init__.py` 的必需集合 == `evidence/accel_matrix.py` 的 EXPECTED_EXPORTS。

        两处必须逐项相等（含顺序）：`__init__.py` 是运行期副本（wheel 里没有
        `evidence/`，不能 import），`accel_matrix.py` 是唯一来源（`ci.yml` 读它）。
        这条断言就是防这两份名单漂移的那颗钉子。
        """
        from dcg import _accel as host

        spec = importlib.util.spec_from_file_location(
            "dcg_accel_matrix_under_test",
            self.ROOT / "evidence" / "accel_matrix.py")
        matrix = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(matrix)

        self.assertEqual(list(matrix.EXPECTED_EXPORTS), list(host.REQUIRED_EXPORTS),
                         "运行期必需集合与 evidence/accel_matrix.py 漂移了："
                         "改导出面时两处都要改")

    def test_required_exports_has_no_duplicates_or_mt(self):
        """名单本身的自检：无重复、无 `mt`。

        `mt`（`$match`）自 2.2.5 起**不再是导出候选**：`o_mt` 已从 `newfn.rs`
        彻底删除（此前是「实现保留、故意不注册」，实测 0.56–0.68×，注册即
        负优化）。这条断言留着，防的是有人把 `mt` 重新加进必需集合 —— 那样
        完整产物会被判成「缺符号」，而实际上根本没有这个实现可补。
        """
        from dcg import _accel as host

        req = list(host.REQUIRED_EXPORTS)
        self.assertEqual(len(req), len(set(req)), "REQUIRED_EXPORTS 有重复项")
        self.assertNotIn("mt", req,
                         "mt（$match）的 Rust 实现已于 2.2.5 删除（实测 0.56–0.68× "
                         "负优化），不再是导出候选；把它加进必需集合会让完整产物"
                         "被判成「缺符号」，而那个符号已经不存在了")


class TestCIDriftCheck(unittest.TestCase):
    """Q-13：CI 要能提示「源码改了、入库二进制没重建」。"""

    ROOT = Path(__file__).resolve().parents[1]

    def test_ci_compares_fresh_build_hash_with_the_shipped_file(self):
        body = (self.ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        self.assertIn("hashlib.sha256", body, "CI 没有对产物做任何哈希校验")
        self.assertIn("accel binary drift", body, "没有漂移提示")
        drift = body[body.index("accel binary drift"):]
        self.assertIn("::warning", body,
                      "漂移必须用 warning 级 annotation，而不是静默或直接失败")
        self.assertNotIn("sys.exit(1)", drift.split("PY")[0],
                         "跨平台二进制不可能字节相同，漂移不能硬失败")


if __name__ == "__main__":
    unittest.main()
