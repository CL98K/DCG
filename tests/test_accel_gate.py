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


if __name__ == "__main__":
    unittest.main()
