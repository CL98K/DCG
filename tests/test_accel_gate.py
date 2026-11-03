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
import inspect
import math
import random
import unittest

from dcg import runtime as rt

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
        """三列起 Rust 的收益被 Python 侧行构造摊薄，实测过不了 1.5× 门槛。

        `_ZP_COLS` 是**上界**而不是下界：列数一多，Python 的
        `[[c[i] for c in cols] for i in range(n)]` 里的跨语言边界成本占比下降，
        而 Rust 逐行建 `PyList` 的成本没下降 —— 4 列以上实测只有 1.28–1.41×。
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
    """`$clone` 臂：本层宽度门槛（dict `_CL_MIN_DICT`、序列 `_CL_MIN_SEQ`）+ Rust 深度上限 64。"""

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
        """dict 与序列的阶跃点不同：序列门槛必须严格高于 dict（实测 list 宽 8 只有 1.39–1.66×）。"""
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
            big = (type(v) is int or type(v) is float) and abs(v) > 4000
            for p in self.PICTURES:
                # 罗马图样在大数上双方都会无限膨胀（10**21 曾直接吃掉 32 GB），
                # 这是纯 Python 自身的缺陷，与门控无关，这里绕开。
                if p in ("I", "i") and big:
                    continue
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


if __name__ == "__main__":
    unittest.main()
