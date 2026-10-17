# -*- coding: utf-8 -*-
"""辅助函数层的开销量化: 到底为了 undefined 语义多付了多少?

结论导引: helper 调用只值 50ns, 但整体慢 2.2-3.4x —— 差距来自 (a) eval 框架的
LOAD_NAME 全局查表, (b) 守卫的重复求值, (c) 字符串操作本身的代码量。
下面同时实测"内联守卫 + walrus 单次求值"能否把差距压回去, 以及纯表达式的真实
开销 (排除 eval 框架, 用 lambda 预绑定)。"""
import sys, time
from jsonata_poc import (UNDEF, _helpers, _add, _mul, _concat, _upper, _str,
                         build, run, P, lex, compile_py)

print(sys.version.split()[0])

N = 200_000
rows = [{"a": i, "b": i * 2, "s": " abc "} for i in range(1000)]

def bench(fn, n=N, repeat=5):
    best = 1e18
    for _ in range(repeat):
        t0 = time.perf_counter()
        for i in range(n):
            fn(i)
        best = min(best, (time.perf_counter() - t0) / n)
    return best

def mk(expr, inline=None):
    """返回逐行执行闭包。expr = JSONata 源; inline = 手写展开等价物"""
    code = build(expr, None)
    ns = _helpers()
    def compiled(i):
        r = rows[i % 1000]
        ns["a"] = r["a"]; ns["b"] = r["b"]; ns["s"] = r["s"]
        return eval(code, ns)
    if inline is None:
        return compiled
    def handwritten(i):
        r = rows[i % 1000]
        return inline(r["a"], r["b"], r["s"])
    return handwritten

print(f"\n{'形状':<34}{'直接Python':>12}{'JSONata编译':>14}{'倍数':>8}")
print("-" * 70)
cases = [
    ("a + b * 2",                       lambda a, b, s: a + b * 2),
    ("$uppercase(s) & '-' & $string(a)", lambda a, b, s: s.upper() + "-" + str(a)),
    ("{'x': a + b, 'u': $uppercase(s)}", lambda a, b, s: {"x": a + b, "u": s.upper()}),
    ("$length(s) + a",                  lambda a, b, s: len(s) + a),
]
for expr, direct in cases:
    t_d = bench(lambda i, d=direct: d(rows[i % 1000]["a"], rows[i % 1000]["b"], rows[i % 1000]["s"]))
    t_c = bench(mk(expr))
    # 手写"内联守卫"等价物: 不做 undefined 检查的直接版本就是上界
    print(f"{expr:<34}{t_d*1e6:>10.3f}us{t_c*1e6:>12.3f}us{t_c/t_d:>7.2f}x")

# --- helper 调用本身的开销 ---
print("\n=== 单个 operator helper 的纯调用开销 (同一语义, 无 eval 框架) ===")
def raw_add(i):
    a = rows[i % 1000]["a"]; b = rows[i % 1000]["b"]
    return a + b
def hlp_add(i):
    a = rows[i % 1000]["a"]; b = rows[i % 1000]["b"]
    return _add(a, b)
t_raw, t_hlp = bench(raw_add), bench(hlp_add)
print(f"  裸 a + b            {t_raw*1e6:7.3f}us")
print(f"  _add(a, b)          {t_hlp*1e6:7.3f}us   +{(t_hlp-t_raw)*1e9:5.0f}ns/次")

def chain_raw(i):
    a = rows[i % 1000]["a"]; b = rows[i % 1000]["b"]
    return a + b * 2 - b
def chain_hlp(i):
    a = rows[i % 1000]["a"]; b = rows[i % 1000]["b"]
    return _add(_mul(_add(b, 0) if False else b, 2) if False else _mul(b, 2), a)
# 上面的写法不构成对照, 直接测 3 个 helper 嵌套
def chain3(i):
    a = rows[i % 1000]["a"]; b = rows[i % 1000]["b"]
    return _add(a, _mul(b, 2))
t_cr, t_ch = bench(chain_raw), bench(chain3)
print(f"  裸 a+b*2-b(3 op)    {t_cr*1e6:7.3f}us")
print(f"  3 个 helper 嵌套    {t_ch*1e6:7.3f}us   +{(t_ch-t_cr)*1e9:5.0f}ns/表达式")

# --- 固定成本 ---
print("\n=== 固定成本 ===")
t0 = time.perf_counter(); tree = P(lex("$uppercase(s) & '-' & $string(a)")).parse(); t_parse = time.perf_counter() - t0
t0 = time.perf_counter(); src = compile_py(tree); t_gen = time.perf_counter() - t0
t0 = time.perf_counter(); code = compile(src, "<x>", "eval"); t_compile = time.perf_counter() - t0
print(f"   parse(词法+Pratt)  {t_parse*1e6:8.2f}us")
print(f"   compile_py(生成)   {t_gen*1e6:8.2f}us")
print(f"   compile(字节码)    {t_compile*1e6:8.2f}us")
print(f"   合计每表达式       {(t_parse+t_gen+t_compile)*1e6:8.2f}us  (一次性, 可缓存)")

t0 = time.perf_counter()
for _ in range(1000): _helpers()
print(f"   _helpers() 重建ns  {(time.perf_counter()-t0)*1e6:8.2f}us/次  <- 每行都调就是纯浪费")

t0 = time.perf_counter()
ns = _helpers()
for _ in range(1000): dict(ns)
print(f"   复用 ns (cache)    {(time.perf_counter()-t0)*1e6:8.2f}us/次")

print("\n=== 折算 ===")
for rows_n in (1000, 1_000_000):
    per_field = (t_hlp - t_raw)
    print(f"  若 8 个字段, 每字段 2 个 helper: 额外 {per_field*16*rows_n*1000:.3f} ms / {rows_n} 行")

# ============================================================================
# 第二部分: 差距到底在哪 —— 三种执行形态 + 真实守卫成本
# ============================================================================
_REAL = {"a": 1, "_U": UNDEF}

def as_lambda(expr):
    """脱离 eval/LOAD_NAME 框架: 预绑定字段, 纯测表达式本身"""
    src = compile_py(P(lex(expr)).parse(), None)
    ns = _helpers()
    ns.update(_REAL)
    return eval(f"lambda a, b: {src}", ns)

def as_lambda_inline(expr):
    """内联守卫: 用 walrus 保证每个子表达式只求值一次"""
    src = compile_py(P(lex(expr)).parse(), None, inline=True)
    ns = _helpers()
    ns.update(_REAL)
    return eval(f"lambda a, b: {src}", ns)

print("\n=== 纯表达式开销 (lambda 预绑定, 排除 eval 框架) ===")
print(f"{'形状':<26}{'裸Python':>11}{'helper版':>11}{'内联守卫':>11}{'vs裸':>8}{'vshelper':>10}")
print("-" * 80)
for expr, direct, sub in [
    ("a + b * 2",             lambda a, b: a + b * 2, 2),
    ("a + b * 2 - b",         lambda a, b: a + b * 2 - b, 3),
    ("(((a + b) * 2) - b) / 2", lambda a, b: (((a + b) * 2) - b) / 2, 5),
]:
    lam_h = as_lambda(expr)
    lam_i = as_lambda_inline(expr)
    t_b = bench(lambda i, d=direct: d(rows[i % 1000]["a"], rows[i % 1000]["b"]))
    t_h = bench(lambda i, l=lam_h: l(rows[i % 1000]["a"], rows[i % 1000]["b"]))
    t_i = bench(lambda i, l=lam_i: l(rows[i % 1000]["a"], rows[i % 1000]["b"]))
    print(f"{expr:<26}{t_b*1e6:>9.3f}us{t_h*1e6:>9.3f}us{t_i*1e6:>9.3f}us"
          f"{t_h/t_b:>7.2f}x{t_h/t_i:>9.2f}x")

# ============================================================================
# 第三部分: ctx 查表 ns["a"]=... 与 eval 框架的真实成本
# ============================================================================
print("\n=== eval 框架自身的成本 (逐行) ===")
code = build("a + b * 2", None)
ns = _helpers()
def with_eval(i):
    r = rows[i % 1000]
    ns["a"] = r["a"]; ns["b"] = r["b"]; ns["s"] = r["s"]
    return eval(code, ns)
def without_eval(i):
    r = rows[i % 1000]
    return r["a"] + r["b"] * 2
def only_lookup(i):
    r = rows[i % 1000]
    ns["a"] = r["a"]; ns["b"] = r["b"]; ns["s"] = r["s"]
    return None
for name, fn in (("裸表达式", without_eval), ("仅 ns 写入(3鍵)", only_lookup), ("+eval(helper版)", with_eval)):
    print(f"  {name:<20}{bench(fn)*1e6:8.3f}us")

# ctx 形式对比: 裸名 vs .get() 查表
print("\n=== 字段访问方式: 裸名(交 DCG 重写) vs _ctx.get() ===")
ns2 = _helpers(); ns2.update(_REAL)
lam_bare = eval("lambda a, b: %s" % compile_py(P(lex("a + b * 2")).parse(), None), ns2)
lam_ctx  = eval("lambda _ctx: %s" % compile_py(P(lex("a + b * 2")).parse(), "_ctx"), ns2)
row = {"a": 3, "b": 4}
t1 = bench(lambda i, l=lam_bare, r=row: l(r["a"], r["b"]))
t2 = bench(lambda i, l=lam_ctx, r=row: l(r))
print(f"  裸名(形参)      {t1*1e6:8.3f}us")
print(f"  _ctx.get()      {t2*1e6:8.3f}us   ({t2/t1:.2f}x)")

