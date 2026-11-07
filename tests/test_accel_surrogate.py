"""C1（双活违约）回归：孤立代理项上，装了 Rust 与没装 Rust 必须**完全同行为**。

## 这个文件守的是什么

DCG 的加速后端是「双活」的：`dcg/runtime.py` 的 `_eu` / `_du` 在守卫通过后先问 Rust，
Rust 返回 `None` 才落回纯 Python：

```python
if _ACC is not None and type(v) is str:
    r = _ACC.eu(v)
    if r is not None:      # ← 关键：判的是 `is not None`，不是 `is not UNDEF`
        return r
```

`UNDEF` 是 `_Undef` 的**单例实例**，`UNDEF is not None` 为真。所以只要 Rust 在
「自己算不了」时返回 `UNDEF` 而不是 `None`，那个 `undefined` 就会被当成**结果**
直接顶替掉 Python 本该做的事 —— 回退路径永远走不到。

这正是 C1 的成因。改动前（`dcg/_accel/src/newfn.rs` 的 `o_eu` / `o_du`）：

| 输入 | 纯 Python（`_ACC = None`） | 改动前 live | 改动后 live |
| --- | --- | --- | --- |
| `_eu('\\ud800')` | 抛 `UnicodeEncodeError` | `undefined` ❌ | 抛 `UnicodeEncodeError` ✅ |
| `_du('\\ud800')` | 返回 `'\\ud800'` | `undefined` ❌ | 返回 `'\\ud800'` ✅ |

后果不是「结果略有不同」，而是**关掉 Rust 就换了语义**：`$encodeUrl` 在含孤立代理项时
本该报错，装了扩展却静默给出 `undefined`。这类缺陷最阴的地方是本机（Windows）复现不出来
之外 —— 它连「本机装了扩展」这个前提都藏起来了：CI 从源码现场构建，行为永远正确。

## 修法（只动 Rust，不动 Python）

Rust 对「是精确 `str` 但提取不出 `String`」的入参返回 `None`（= 我不接管），
让 Python 侧原样接管。**不能**返回 `UNDEF`，理由见上。

## 关于「成对代理」

`'\\ud83d\\ude00'` 在 Python 的 `str` 里是**两个孤立代理码点**（`len == 2`），
不是合法 UTF-8 —— `'\\ud83d\\ude00'.encode('utf-8')` 抛 `UnicodeEncodeError`。
真正的 emoji 是单个码点 `'\\U0001F600'`（`len == 1`）。

所以本文件对两者分开断言：

* `'\\ud83d\\ude00'` —— **非法**，必须与纯 Python 同行为（`_eu` 抛、`_du` 原样返回）；
* `'\\U0001F600'` —— **合法**，必须仍然走 Rust（不能被这次修复误挡掉）。

如果只测前者，一个「把所有非 ASCII 都推回 Python」的过度修复也能让测试变绿，
那等于把 4.34×/7.51× 的收益白扔了。第二条断言就是防这个。
"""

import unittest

import dcg.runtime as rt


#: 孤立代理项与含孤立代理项的串。全部不是合法 UTF-8。
SURROGATE_CASES = (
    "\ud800",
    "\udfff",
    "\udbff",
    "\udc00",
    "a\ud800b",
    "%41\ud800",
    "\ud800%zz",
    "\ud800%41",
    "\u4e2d\ud800",
    "\ud800\u4e2d",
    "\ud800\udfff",
    "\ud83d\ude00",   # 成对代理：在 str 里仍是两个孤立码点，非法
    "\ud83d",         # 半个
    "\ude00",         # 另一半
    "x\ud83d\ude00y",
)

#: 合法 UTF-8 的非 ASCII（含真正的 emoji 单码点）。这些**必须**继续走 Rust。
LEGAL_CASES = (
    "\U0001F600",           # 😀 单个码点
    "\U0001F600x",
    "\u4e2d\U0001F600",
    "\U0001F1E8\U0001F1F3",  # 🇨🇳 两个区域指示符
    "\u4e2d\u6587",
    "\u00e9",
)


def _call(fn, arg):
    """统一成 `(kind, payload)`：返回值或 `异常类型名: 消息`。"""
    try:
        return ("RET", fn(arg))
    except BaseException as e:  # noqa: BLE001 - 双活等价要求连异常一起比
        return ("EXC", type(e).__name__ + ": " + str(e))


def _norm(v):
    return "<UNDEF>" if v is rt.UNDEF else v


def _both(fn, arg):
    """跑 live（现役 `_ACC`）与 pure（`_ACC = None`）两臂，返回两者。"""
    saved = rt._ACC
    try:
        rt._ACC = saved
        live = _call(fn, arg)
        rt._ACC = None
        pure = _call(fn, arg)
    finally:
        rt._ACC = saved
    if live[0] == "RET":
        live = ("RET", _norm(live[1]))
    if pure[0] == "RET":
        pure = ("RET", _norm(pure[1]))
    return live, pure


class _SpyAccel:
    """包住真产物，记录被调用的导出名 —— 用来断言「确实走了 Rust」。"""

    def __init__(self, inner):
        self._inner = inner
        self.calls = []

    def __getattr__(self, name):
        target = getattr(self._inner, name)

        def wrapper(*args, **kwargs):
            self.calls.append(name)
            return target(*args, **kwargs)

        return wrapper


class _OldAccel:
    """模拟旧产物：13 个导出，`eu` / `du` 一律返回 `None`（= 不接管）。

    真产物是 PyO3 扩展；守卫只做 `is not None` 与属性存在性判断，所以普通对象足够。
    `eu` / `du` 返回 `None` 表示「这份产物没能力接管」，运行时必须落回纯 Python。
    """

    _EXPORTS = ("av", "cl", "ds", "du", "eu", "fgi", "fms", "fnm",
                "mn", "mx", "pgi", "tms", "zp")

    def __init__(self):
        for name in self._EXPORTS:
            setattr(self, name, lambda *a, **k: None)

    def __dir__(self):
        return sorted(self._EXPORTS)


class _SurrogateTest(unittest.TestCase):
    def setUp(self):
        self._saved = rt._ACC

    def tearDown(self):
        rt._ACC = self._saved


class TestSurrogateEquivalence(_SurrogateTest):
    """孤立代理项上 live 必须逐项等于 pure —— 返回值、异常类型、异常消息都要一样。"""

    def test_eu_matches_pure_on_surrogates(self):
        for arg in SURROGATE_CASES:
            with self.subTest(arg=arg):
                live, pure = _both(rt._eu, arg)
                self.assertEqual(
                    live, pure,
                    "_eu(%r)：装了 Rust 与没装必须同行为。live=%r pure=%r。"
                    "Rust 侧在提取 String 失败时必须返回 None（不接管），"
                    "返回 UNDEF 会被 `if r is not None` 当成结果顶替掉异常"
                    % (arg, live, pure),
                )

    def test_du_matches_pure_on_surrogates(self):
        for arg in SURROGATE_CASES:
            with self.subTest(arg=arg):
                live, pure = _both(rt._du, arg)
                self.assertEqual(
                    live, pure,
                    "_du(%r)：装了 Rust 与没装必须同行为。live=%r pure=%r"
                    % (arg, live, pure),
                )

    def test_eu_raises_unicode_encode_error_on_lone_surrogate(self):
        """钉死具体语义：`_eu` 在孤立代理项上必须**抛**，不是返回 undefined。"""
        with self.assertRaises(UnicodeEncodeError):
            rt._eu("\ud800")

    def test_du_returns_the_surrogate_unchanged(self):
        """钉死具体语义：`_du` 没有 `%` 时原样返回那个字符。"""
        self.assertEqual(rt._du("\ud800"), "\ud800")
        self.assertEqual(rt._du("a\ud800b"), "a\ud800b")

    def test_paired_surrogate_is_still_illegal(self):
        """`'\\ud83d\\ude00'` 是两个码点、不是合法 UTF-8 —— 断言这条前提本身。

        这条会红在「有人以为它是 emoji 就把它从 SURROGATE_CASES 里删掉」的时候。
        """
        self.assertEqual(len("\ud83d\ude00"), 2)
        with self.assertRaises(UnicodeEncodeError):
            "\ud83d\ude00".encode("utf-8")
        with self.assertRaises(UnicodeEncodeError):
            rt._eu("\ud83d\ude00")


class TestLegalUnicodeStillUsesRust(_SurrogateTest):
    """合法 UTF-8 不能被这次修复误挡回 Python —— 否则等于白扔 4.34×/7.51×。"""

    def setUp(self):
        super().setUp()
        if rt._ACC is None:  # pragma: no cover - 源码树/无产物
            self.skipTest("本机没有加速后端，无法断言「走了 Rust」")

    def test_eu_legal_unicode_goes_through_rust(self):
        spy = _SpyAccel(rt._ACC)
        rt._ACC = spy
        for arg in LEGAL_CASES:
            with self.subTest(arg=arg):
                spy.calls.clear()
                live = rt._eu(arg)
                self.assertIn("eu", spy.calls, "合法 UTF-8 被挡回 Python 了：%r" % (arg,))
                rt._ACC = None
                pure = rt._eu(arg)
                rt._ACC = spy
                self.assertEqual(live, pure)

    def test_du_legal_unicode_goes_through_rust(self):
        spy = _SpyAccel(rt._ACC)
        rt._ACC = spy
        for arg in LEGAL_CASES + ("%41", "%E4%B8%AD", "a%20b"):
            with self.subTest(arg=arg):
                spy.calls.clear()
                live = rt._du(arg)
                self.assertIn("du", spy.calls, "合法输入被挡回 Python 了：%r" % (arg,))
                rt._ACC = None
                pure = rt._du(arg)
                rt._ACC = spy
                self.assertEqual(live, pure)

    def test_surrogates_do_not_reach_rust_result(self):
        """反向断言：代理项上 Rust 确实**没**给值（返回 None），所以落回了 Python。"""
        acc = rt._ACC
        for name in ("eu", "du"):
            fn = getattr(acc, name)
            for arg in SURROGATE_CASES:
                with self.subTest(export=name, arg=arg):
                    self.assertIsNone(
                        fn(arg),
                        "_ACC.%s(%r) 必须返回 None（不接管）；返回 UNDEF 会让"
                        "`if r is not None` 直接采用它，这正是 C1" % (name, arg),
                    )


class TestNormalPathNoRegression(_SurrogateTest):
    """正常路径一字不能变。"""

    def test_eu_normal(self):
        self.assertEqual(rt._eu("a b"), "a%20b")
        self.assertEqual(rt._eu("~-._"), "~-._")
        self.assertEqual(rt._eu(""), "")
        self.assertEqual(rt._eu("\U0001F600"), "%F0%9F%98%80")

    def test_du_normal(self):
        self.assertEqual(rt._du("%41"), "A")
        self.assertEqual(rt._du("%41%42"), "AB")
        self.assertEqual(rt._du("%E4%B8%AD"), "\u4e2d")
        self.assertIs(rt._du("%zz"), rt.UNDEF)
        self.assertIs(rt._du("%4"), rt.UNDEF)
        self.assertIs(rt._du("100%"), rt.UNDEF)

    def test_non_str_still_undefined(self):
        for arg in (None, 5, ["%20"], {"a": 1}):
            with self.subTest(arg=arg):
                self.assertIs(rt._du(arg), rt.UNDEF)
                live, pure = _both(rt._eu, arg)
                self.assertEqual(live, pure)


class TestOldProductFallsBack(_SurrogateTest):
    """旧产物（`eu`/`du` 返回 `None`）必须退回纯 Python，不能 `AttributeError`。"""

    def test_old_product_does_not_raise_attribute_error(self):
        """旧产物上不能 `AttributeError` —— 那正是 P0 事故的形态（见 test_accel_capability）。"""
        rt._ACC = _OldAccel()
        try:
            for arg in SURROGATE_CASES + LEGAL_CASES:
                for fn, name in ((rt._eu, "_eu"), (rt._du, "_du")):
                    with self.subTest(fn=name, arg=arg):
                        _call(fn, arg)   # 抛 AttributeError 就会被 subTest 记成失败
        finally:
            rt._ACC = self._saved

    def test_old_product_matches_pure(self):
        """旧产物上两臂必须同行为（此时 live 臂的 Rust 一律返回 `None`，等于没接管）。

        这条防的是「旧产物的 `eu` 恰好返回了某个非 `None` 值」——那会让守卫采信它，
        而不是落回 Python。
        """
        old = _OldAccel()
        for fn, name in ((rt._eu, "_eu"), (rt._du, "_du")):
            for arg in SURROGATE_CASES + LEGAL_CASES:
                with self.subTest(fn=name, arg=arg):
                    rt._ACC = old
                    live = _call(fn, arg)
                    rt._ACC = None
                    pure = _call(fn, arg)
                    rt._ACC = self._saved
                    self.assertEqual(live, pure,
                                     "旧产物上 %s(%r) 没退回纯 Python" % (name, arg))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
