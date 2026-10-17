# -*- coding: utf-8 -*-
"""
决定性对比: 在 DCG 真实执行形态 (编译成函数, fast locals, 见 dcg.py:1247-1252)
下, undefined 语义到底要付多少代价?

三种形态, 语义完全等价:
  A 裸:       row["a"] + row["b"] * 2                 <- 今天手写 ext 的写法
  B helper:   _add(_x(row,"a"), _mul(_x(row,"b"), 2)) <- 原型现在的产物
  C 内联:     守卫展开, 无函数调用

之前的 bench_overhead.py 用 eval(source, ns) 测, 结果偏悲观 2.2-3.4x —— 因为
eval 走 LOAD_NAME 查 dict, 而 DCG 是把表达式嵌进生成源码后 compile 成函数,
走 LOAD_FAST (dcg.py:1247)。这里用真实函数重测。
"""
import time
from jsonata_poc import UNDEF, _add, _mul, _upper, _str, _concat, _length

N = 300_000
rows = [{"a": i, "b": i * 2, "s": " abc "} for i in range(1000)]

def bench(fn, repeat=5):
    best = 1e18
    for _ in range(repeat):
        t0 = time.perf_counter()
        for i in range(N):
            fn(rows[i % 1000])
        best = min(best, (time.perf_counter() - t0) / N)
    return best

def _x(row, k):
    return row.get(k, UNDEF)

# ---------------- 形态 A: 裸 ----------------
def a_arith(row):
    return row["a"] + row["b"] * 2
def a_str(row):
    return row["s"].upper() + "-" + str(row["a"])
def a_obj(row):
    return {"x": row["a"] + row["b"], "u": row["s"].upper()}
def a_pipe(row):
    return len(row["s"]) + row["a"]

# ---------------- 形态 B: helper ----------------
def b_arith(row):
    return _add(_x(row, "a"), _mul(_x(row, "b"), 2))
def b_str(row):
    return _concat(_concat(_upper(_x(row, "s")), "-"), _str(_x(row, "a")))
def b_obj(row):
    return {"x": _add(_x(row, "a"), _x(row, "b")), "u": _upper(_x(row, "s"))}
def b_pipe(row):
    return _add(_length(_x(row, "s")), _x(row, "a"))

# ---------------- 形态 C: 内联守卫 ----------------
def c_arith(row):
    return (lambda a, b: (a + b * 2 if (a is not UNDEF and b is not UNDEF) else UNDEF))(_x(row, "a"), _x(row, "b"))
def c_pipe(row):
    return (lambda a, b: (a + len(b) if (a is not UNDEF and b is not UNDEF) else UNDEF))(_x(row, "a"), _x(row, "s"))

print(f"{'形状':<14}{'A 裸':>10}{'B helper':>11}{'C 内联':>11}{'B/A':>8}{'C/A':>8}")
print("-" * 64)
for name, fa, fb, fc in (
    ("算术 a+b*2",  a_arith, b_arith, c_arith),
    ("字符串拼接",   a_str,   b_str,   None),
    ("对象构造",     a_obj,   b_obj,   None),
    ("$length+a",    a_pipe,  b_pipe,  c_pipe),
):
    ta = bench(fa); tb = bench(fb)
    if fc:
        tc = bench(fc); cs, cr = f"{tc*1e6:>9.3f}us", f"{tc/ta:>7.2f}x"
    else:
        cs, cr = f"{'—':>10}", f"{'—':>8}"
    print(f"{name:<14}{ta*1e6:>8.3f}us{tb*1e6:>9.3f}us{cs}{tb/ta:>7.2f}x{cr}")

# ---------------- 收益侧: 编译固定成本能否摊薄 ----------------
print("\n=== 编译固定成本 (一次性) ===")
from jsonata_poc import P, lex, compile_py
t0 = time.perf_counter()
for _ in range(200):
    src = compile_py(P(lex("$uppercase(s) & '-' & $string(a)")).parse())
t_gen = (time.perf_counter() - t0) / 200
t0 = time.perf_counter()
for _ in range(200):
    compile(src, "<x>", "eval")
t_c = (time.perf_counter() - t0) / 200
print(f"  compile_py 生成源码  {t_gen*1e6:7.2f}us")
print(f"  compile 成字节码     {t_c*1e6:7.2f}us")
print(f"  合计每表达式         {(t_gen+t_c)*1e6:7.2f}us  <- 在 1266 行里构建期付一次")

# ---------------- 真实场景折算 ----------------
print("\n=== 折算到真实批处理 (8 字段/行, 假设每字段 2 个运算) ===")
ta = bench(a_arith); tb = bench(b_arith)
for rows_n in (1_000, 100_000, 10_000_000):
    extra = (tb - ta) * 16 * rows_n          # 8 字段 * 2 运算
    print(f"  {rows_n:>12,} 行: 额外 {extra*1000:9.2f} ms   (裸 {ta*16*rows_n*1000:9.1f} ms)")

# ============================================================================
# 公平 A/B/C: 把 "字段访问成本" 从 "守卫成本" 里剥出来
# ============================================================================
# 上一段 A 用 row["a"] 直取, B 却要额外调 _x(row,'a') —— 那 2.45x 混进了字段
# 访问方式差异, 不是纯守卫成本。DCG 现有代码同样要经 createIdx 取字段(dcg.py:203),
# 所以下方 D 才是与 B 真正的 apples-to-apples 对照。
def d_fair(row):                      # 字段访问用 .get, 语义=原样未做 undefined 处理
    a = row.get("a"); b = row.get("b")
    return a + b * 2
def e_islocal(row):                   # 字段查一次 + 显式守卫, 无 helper 调用
    a = row.get("a", UNDEF); b = row.get("b", UNDEF)
    return (a + b * 2) if (a is not UNDEF and b is not UNDEF) else UNDEF
def f_guardfree(row):                 # 已知字段必然存在(有 schema 保证) -> 守卫可消除
    a = row["a"]; b = row["b"]
    return a + b * 2

print(f"\n{'形态':<40}{'耗时':>11}{'对 D 倍数':>12}")
print("-" * 64)
td = bench(d_fair)
for label, fn in (
    ("D 公平基线: .get 取字段 + 裸运算",        d_fair),
    ("E D + 显式 undefined 守卫 (无 helper)",   e_islocal),
    ("F schema 保证存在 -> 守卫消除(== 裸)",    f_guardfree),
    ("B 完整 helper 版 (_x+_add+_mul)",        b_arith),
    ("A 直取 row[] + 裸运算 (最快, 但语义不等价)", a_arith),
):
    t = bench(fn)
    print(f"{label:<40}{t*1e6:>9.3f}us{t/td:>11.2f}x")

print("\n=== 结论折算: 守卫相对 '取字段+裸运算' 的额外成本 ===")
te = bench(e_islocal)
per_op = te - td
for rows_n in (1_000, 100_000, 10_000_000):
    print(f"  {rows_n:>12,} 行 × 8 字段: 纯守卫额外 {per_op*8*rows_n*1000:9.2f} ms"
          f"   (基线 {td*8*rows_n*1000:9.1f} ms)")

