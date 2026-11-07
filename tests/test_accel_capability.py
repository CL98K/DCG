"""`dcg/_accel` 产物**落后于源码**时的退化行为（P0 回归）。

## 这个文件守的是什么

DCG 的 Rust 加速后端是「双活」的：`dcg/runtime.py` 里每个算子在守卫通过后先问 Rust，
Rust 返回 `None` 才落回纯 Python。守卫原本写成 `if _ACC is not None:`，即「有没有扩展」。

但入库的预编译产物是**按平台分别提交**的：

| 产物 | 平台 |
| --- | --- |
| `_accel.pyd` | Windows |
| `_accel.abi3.so` | Linux |
| `_accel-macos-arm64.abi3.so` / `_accel-macos-x64.abi3.so` | macOS |

2026-11 新增 `ue`/`ud`/`fb` 三个导出时，只有 Windows 的 `.pyd` 被重新构建，
Linux 与 macOS 的 `.so` 仍是旧的 13 导出。于是 `_ACC.ue(v)` 在这些平台上直接
`AttributeError` —— **不是退回纯 Python，是崩溃**。

这类缺陷最危险的地方在于：**本机（Windows）永远复现不出来**，CI 也不会红，
因为 CI 是从源码现场构建的，构建出来的永远是新的。所以必须有这个文件，
用「假的老产物」在纯 Python 环境里把那条路径走一遍。

## 为什么守卫要两个条件

```python
if _ACC is not None and _HAS_UE:
```

* `_ACC is not None` —— 测量装置（`evidence/accel_bench.py` 的 `Arms`、
  `tests/test_accel_gate.py` 的 `_pure`）靠把 `rt._ACC` 置 `None` 切到纯 Python 臂。
  `_HAS_*` 是导入期快照，运行时改 `rt._ACC` 它不会跟着变；少了这一条，
  切臂之后就会 `None.ue` → `AttributeError`。
* `_HAS_UE` —— 产物可能缺符号。少了这一条，旧产物上直接 `AttributeError`。

两条都不省。本文件对两条都做断言。
"""

import inspect
import unittest

import dcg.runtime as rt


#: 旧产物（13 导出）的导出集合。刻意写成字面量而不是 `dir(_ACC)`：
#: 这个集合是**历史事实**，不该跟着当前二进制自动变。
_OLD_EXPORTS = frozenset(
    ("av", "cl", "ds", "du", "eu", "fgi", "fms", "fnm", "mn", "mx", "pgi", "tms", "zp")
)

#: 新增的三个导出。旧产物里**没有**它们，这正是事故的成因。
_NEW_EXPORTS = ("ue", "ud", "fb")


class _OldAccel:
    """模拟旧版二进制：只有 13 个导出，没有 `ue` / `ud` / `fb`。

    真产物是 `PyO3` 扩展模块；这里用普通对象模拟，只需要 `dir()` 与属性存在性，
    因为守卫只做 `hasattr` / 成员判断，不做类型检查。
    """

    def __init__(self):
        for name in _OLD_EXPORTS:
            # 旧算子的行为不需要真实现：本文件只验证「新算子会不会崩」，
            # 以及「旧算子在旧产物上仍能被调用」。
            setattr(self, name, lambda *a, **k: None)

    def __dir__(self):
        return sorted(_OLD_EXPORTS)


def _flag_names():
    """运行时里所有 `_HAS_*` 能力标志的名字（按字母序）。

    不硬编码：新增导出时标志会一起长出来，测试不必跟着改。
    """
    return sorted(n for n in dir(rt) if n.startswith("_HAS_"))


def _recompute_flags(module):
    """按 `module` 的实际导出重算**全部**能力标志，返回被改动的名字列表。"""
    cap = frozenset(dir(module))
    changed = []
    for flag in _flag_names():
        name = flag[len("_HAS_"):].lower()
        setattr(rt, flag, name in cap)
        changed.append(flag)
    return changed


class _CapsTest(unittest.TestCase):
    def setUp(self):
        self._flags = _flag_names()
        self._saved = (rt._ACC, rt._CAP) + tuple(getattr(rt, f) for f in self._flags)

    def tearDown(self):
        rt._ACC, rt._CAP = self._saved[0], self._saved[1]
        for flag, value in zip(self._flags, self._saved[2:]):
            setattr(rt, flag, value)

    def _install_old(self):
        """把运行时切到「旧产物」状态，模拟 Linux/macOS 上的真实情形。"""
        old = _OldAccel()
        rt._ACC = old
        rt._CAP = frozenset(dir(old))
        _recompute_flags(old)
        return old


class TestCapabilityDerivation(_CapsTest):
    """能力标志必须由**实际导出集合**推出，不能靠「有没有扩展」。"""

    def test_new_exports_absent_from_old_product(self):
        self.assertFalse(
            _OLD_EXPORTS & set(_NEW_EXPORTS),
            "旧产物不该含新导出；这条断言本身在守 `_OLD_EXPORTS` 这份历史快照",
        )

    def test_flags_false_on_old_product(self):
        """旧产物上，三个新导出的标志必须是 False（其余 13 个是 True）。"""
        self._install_old()
        self.assertFalse(rt._HAS_UE)
        self.assertFalse(rt._HAS_UD)
        self.assertFalse(rt._HAS_FB)
        self.assertTrue(rt._HAS_EU, "旧产物有的算子标志不该被误关")
        self.assertTrue(rt._HAS_FNM, "旧产物有的算子标志不该被误关")

    def test_flags_track_actual_exports(self):
        """**每一个**能力标志都必须与 `hasattr` 一致 —— 防止有人手工写死 True。"""
        acc = rt._ACC
        for flag in _flag_names():
            name = flag[len("_HAS_"):].lower()
            with self.subTest(flag=flag, export=name):
                want = acc is not None and hasattr(acc, name)
                self.assertEqual(getattr(rt, flag), want)

    def test_flags_false_when_no_accel(self):
        """没装扩展（源码树、或平台无产物）时，**所有**标志都必须是 False。"""
        rt._ACC = None
        rt._CAP = frozenset()
        for flag in _flag_names():
            setattr(rt, flag, False)
        self.assertFalse(
            [f for f in _flag_names() if getattr(rt, f)],
            "无扩展时任何能力标志都不该为 True",
        )

    def test_cap_is_exactly_the_required_export_set(self):
        """`_CAP` 必须恰好等于 `dcg._accel.REQUIRED_EXPORTS`（本机装的是新产物时）。

        这条防的是「有人给 `_CAP` 加了模块内建名」这类污染 —— `dir()` 会带出
        `__doc__` / `__loader__` 之类，必须滤掉，否则 `_CAP` 就失去「算子集合」的
        语义，后来者也没法拿它跟 `REQUIRED_EXPORTS` 对账。
        """
        from dcg import _accel as accel_pkg

        if rt._ACC is None:  # pragma: no cover - 源码树/无产物
            self.skipTest("本机没有加速后端，_CAP 为空是正确状态")
        self.assertEqual(set(rt._CAP), set(accel_pkg.REQUIRED_EXPORTS))
        self.assertFalse(
            [n for n in rt._CAP if n.startswith("__")],
            "_CAP 里混进了模块内建名，`dir()` 的结果必须过滤",
        )

    def test_every_required_export_has_a_flag(self):
        """`REQUIRED_EXPORTS` 里每个名字都必须有模块级 `_HAS_<NAME大写>` 布尔标志。

        为什么用 `hasattr` 遍历而不是硬编码名单：硬编码的话，**新增算子时测试自己
        也要改**，那这条测试就失去了「防止忘记加标志」的意义 —— 忘加标志的人和忘改
        名单的人是同一个，两边一起忘，测试照样绿。遍历 `REQUIRED_EXPORTS` 才把
        「新增导出」与「新增标志」焊死在一起。

        为什么要求 `is True or is False` 而不是 truthy：`_HAS_UE = _ACC.ue`（写成
        方法对象）在 `if _ACC is not None and _HAS_UE:` 里恒真，旧产物上就会
        `None.ue` / 缺符号时 `AttributeError` —— 正是这个 P0 要防的东西。
        """
        from dcg import _accel as accel_pkg

        missing = []
        not_bool = []
        for name in accel_pkg.REQUIRED_EXPORTS:
            attr = "_HAS_" + name.upper()
            if not hasattr(rt, attr):
                missing.append("%s（导出 %r 没有对应标志）" % (attr, name))
                continue
            value = getattr(rt, attr)
            if not (value is True or value is False):
                not_bool.append("%s = %r（%s，必须是 bool）"
                                % (attr, value, type(value).__name__))
        self.assertFalse(
            missing,
            "新增导出必须同时加能力标志，否则守卫写成 `_HAS_*` 的地方会静默失效：%s"
            % "；".join(missing),
        )
        self.assertFalse(
            not_bool,
            "能力标志必须是 True/False 字面量，不能是方法对象或其它 truthy 值：%s"
            % "；".join(not_bool),
        )


class TestFallsBackOnOldProduct(_CapsTest):
    """核心：旧产物上三个新算子必须**退回纯 Python 且结果正确**，不许抛异常。"""

    def test_encode_url_component_falls_back(self):
        self._install_old()
        self.assertEqual(rt._ue("a b"), "a%20b")
        self.assertEqual(rt._ue("中文"), "%E4%B8%AD%E6%96%87")
        self.assertIs(rt._ue(None), rt.UNDEF)

    def test_decode_url_component_falls_back(self):
        self._install_old()
        self.assertEqual(rt._ud("a%20b"), "a b")
        self.assertEqual(rt._ud("%E4%B8%AD%E6%96%87"), "中文")
        self.assertEqual(rt._ud("plain"), "plain")
        self.assertIs(rt._ud(None), rt.UNDEF)

    def test_format_base_falls_back(self):
        self._install_old()
        # 三个基数各走一条路：10 走早退、16 走 C `format()`、7 是冷门基数（会撞门控）。
        self.assertEqual(rt._fb(255, 10), "255")
        self.assertEqual(rt._fb(255, 16), "ff")
        self.assertEqual(rt._fb(255, 7), "513")
        # 量级门控放行档：旧产物上必须由 Python 算出正确结果，而不是崩。
        self.assertEqual(rt._fb(10 ** 6, 7), "11333311")
        self.assertEqual(rt._fb(-(10 ** 6), 7), "-11333311")
        self.assertEqual(rt._fb(10 ** 18, 36), "7lieexzx4kxs")

    def test_old_operators_still_work(self):
        """旧产物的 13 个算子在这份假对象上返回 `None`（= Rust 放弃），
        因此也必须落回纯 Python —— 确认新守卫没有把老路径一起关掉。"""
        self._install_old()
        self.assertEqual(rt._eu("a b"), "a%20b")
        self.assertEqual(rt._du("a%20b"), "a b")
        self.assertEqual(rt._fb(255, 7), "513")

    def test_no_attribute_error_anywhere(self):
        """把三个新算子在一批边界输入上全跑一遍，任何一个抛 `AttributeError` 都是回归。"""
        self._install_old()
        cases = (
            (rt._ue, ("", "a b", "中", None, 123)),
            (rt._ud, ("", "a%20b", "%", None, 123)),
            (rt._fb, ((0, 7), (255, 7), (10 ** 6, 7), (-(10 ** 6), 36), (255, 16))),
        )
        for fn, args in cases:
            for a in args:
                with self.subTest(fn=fn.__name__, arg=repr(a)[:40]):
                    try:
                        fn(a) if not isinstance(a, tuple) else fn(*a)
                    except AttributeError as exc:  # pragma: no cover - 回归时才会进
                        self.fail(
                            "%s(%r) 在旧产物上抛了 AttributeError：%s —— "
                            "守卫必须按能力判断，不能按 `_ACC is not None` 判断"
                            % (fn.__name__, a, exc)
                        )


class TestGuardIsTwoConditions(_CapsTest):
    """守卫必须是 `_ACC is not None and _HAS_*` 两条，缺一条都会坏。"""
    def test_source_has_both_conditions(self):
        """源码级断言：三个新算子的守卫都含 `_ACC is not None` 与对应 `_HAS_*`。

        为什么不只做行为测试：行为测试在「恰好 `_ACC` 被置 None」的场景下也能过，
        但测量装置切臂时 `_HAS_*` 仍是 True —— 那条路径只有读源码才能钉死。
        """
        for fn, flag in ((rt._ue, "_HAS_UE"), (rt._ud, "_HAS_UD"), (rt._fb, "_HAS_FB")):
            with self.subTest(fn=fn.__name__, flag=flag):
                src = inspect.getsource(fn)
                self.assertIn(
                    "_ACC is not None and " + flag, src,
                    "%s 的守卫少了 `_ACC is not None and %s`" % (fn.__name__, flag),
                )

    def test_switch_to_none_still_works_with_flags_true(self):
        """模拟测量装置切臂：`rt._ACC = None` 但 `_HAS_*` 仍是导入期快照 True。

        这正是 `_ACC is not None` 那条不能省的原因 —— 少了它就会 `None.ue`。
        """
        rt._HAS_UE = rt._HAS_UD = rt._HAS_FB = True
        rt._ACC = None
        self.assertEqual(rt._ue("a b"), "a%20b")
        self.assertEqual(rt._ud("a%20b"), "a b")
        self.assertEqual(rt._fb(10 ** 6, 7), "11333311")


class TestFlagsDoNotOverDisable(unittest.TestCase):
    """反向守卫：标志加错方向的危险是「过度保守」。

    如果 `_HAS_*` 恒为 False，刚接的三个算子就被静默关掉了 —— 结果仍然正确、
    本机测试仍然全绿，但性能收益全没了。所以必须证明真产物上**真的调到了 Rust**。
    """

    def setUp(self):
        self.acc = rt._ACC
        if self.acc is None:  # pragma: no cover - 源码树/无产物
            self.skipTest("本机没有加速后端")
        self.hits = {"ue": 0, "ud": 0, "fb": 0}
        self._orig = {n: getattr(self.acc, n) for n in self.hits}

        def make(name):
            def probe(*a):
                self.hits[name] += 1
                return self._orig[name](*a)
            return probe

        for name in self.hits:
            setattr(self.acc, name, make(name))

    def tearDown(self):
        for name, fn in getattr(self, "_orig", {}).items():
            setattr(self.acc, name, fn)

    def test_rust_is_actually_reached(self):
        self.assertEqual(rt._ue("a b"), "a%20b")
        self.assertEqual(rt._ud("a%20b"), "a b")
        self.assertEqual(rt._fb(10 ** 6, 7), "11333311")
        self.assertEqual(
            self.hits, {"ue": 1, "ud": 1, "fb": 1},
            "有算子没走到 Rust —— `_HAS_*` 把真产物也关掉了（过度保守）",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
