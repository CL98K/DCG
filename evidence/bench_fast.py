"""对照基线：每类负载的「手写等价代码」与「理论最优形态」。

与 bench_optimallib.py 的区别：那个库覆盖 57 项，这个库只放**用户点名的
高倍率项**，并且每一项都在自己的 docstring 里写清「DCG 产物哪一行引入了
额外开销」，便于逐行对照优化。
"""
from __future__ import annotations

import math
import os
import random
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from dcg.runtime import _ix_get          # noqa: E402


def rows(n, src="tuple"):
    if src == "tuple":
        return [(i, "s%03d" % (i % 200), i % 97, (i * 7) % 1000,
                 float(i % 50) / 4.0, i % 7 == 0, [i, i + 1], {"lv": i % 3},
                 "n%02d" % (i % 20)) for i in range(n)]
    raise ValueError(src)


# ---------------------------------------------------------------- hires
def hires_reduce(d, col=3):
    """`$reduce($.6, function($a,$b){$a+$b})` 的手写下界。

    DCG 产物：``_rd(R[6], lambda a,b:_ad(a,b), 0)`` -> 每行一次 Python 调用，
    再加 helper 链 ``_rd`` 内部的循环。手写直接用内建 ``sum``（C 循环）。
    """
    return [[sum(r[6])] for r in d]


def hires_filter(d, col=6):
    """`$filter($.6, function($v){$v>1})` 的手写下界。

    DCG 产物：``_fi(R[6], lambda v:_gt(v,1))`` -> 每元素一次 Python 调用。
    手写用列表推导 + 内联比较，C 级循环。
    """
    return [[[v for v in r[6] if v > 1]] for r in d]


def hires_map(d, col=6):
    """`$map($.6, function($v){$v*2})` 的手写下界。"""
    return [[[v * 2 for v in r[6]] for r in d]]


def hires_sort(d, col=6):
    """`$sort($.6)` 的手写下界（内建 sorted）。"""
    return [[sorted(r[6])] for r in d]


def agg_container(d):
    """aggregate 含 optional 子键时的等价手写。

    注意：早先这里写的是 `"qty_sum": [r[3] ...]`，而对应 schema 的子键是
    `{"value": "$.6", "optional": true}` —— **字段取错了**，两者比的不是同一个
    东西（r[3] vs r[6]），那个 1.17x 不可信。等价手写必须同样做「缺键落 None」
    的探测：`None if (_v := _ix_get(r, 6)) is _U else _v`，与 DCG 产物同形态。
    """
    from dcg.runtime import _ix_get, UNDEF as _U
    return {"a": [r[0] for r in d], "b": [r[2] for r in d],
            "stats": {"n_rows": 1,
                      "lvl": [None if (_v := _ix_get(r, 6)) is _U else _v
                              for r in d]}}


def agg_direct(d):
    """aggregate 直出列的手写下界。"""
    return {"a": [r[0] for r in d],
            "stats": {"n_rows": 1, "qty_sum": [r[3] for r in d]}}


# ---------------------------------------------------------------- shape
def shape_rows(d):
    """shape=rows 的手写下界：每行一个 dict。"""
    keys = ("a", "b", "c")
    return [dict(zip(keys, (r[0], r[2], r[3]))) for r in d]


def shape_dict(d):
    """shape=dict 的手写下界：三列。"""
    return {"a": [r[0] for r in d], "b": [r[2] for r in d], "c": [r[3] for r in d]}


def shape_list(d):
    """shape=list 的手写下界：每行一个 list。"""
    return [[r[0], r[2], r[3]] for r in d]


def shape_index(d):
    """shape=index 的手写下界。"""
    return dict(enumerate([r[0], r[2], r[3]] for r in d))


# ---------------------------------------------------------------- optional
def optwide_hit(d):
    """两列 optional 且都存在的手写下界。"""
    return {"a": [r[0] for r in d],
            "lv": [r[7]["lv"] for r in d],
            "c": [r[2] for r in d]}


def optwide_missing(d):
    """两列 optional 且都缺失的手写下界 —— **不可比项，仅作记录**。

    这里没有公平的等价手写：

    * `[None for r in d]` 是「已经知道数据里没有这个键」才写得出的代码，而
      生成器在编译期并不知道运行期数据有没有该键，必须逐行探测；
    * 逐行探测的等价写法是 `r.get("zz", None)`，但本基准的行是 9 元组
      （`rows()` 生成位置式数据），tuple 没有 `.get`，只能 `_ix_get(r, 'zz')`；
    * 而「知道行是 dict」这件事，DCG 的 `_rows_mapping()` 同样证不出来
      （字段用名字路径取值、`input.source` 为空 → 行可能是 tuple 也可能是
      dict），所以它也只能发 `_ix_get`。

    也就是说：**两边都被同一个不确定性限制，没有「更快的正确写法」存在**。
    保留本项只为记录绝对成本与噪声带宽；两侧都受同一个不确定性限制，
    比值接近 1.10 阈值时不宣称快慢（报告 §5.3 把它记为「逐行探测上限」）。
    """
    from dcg.runtime import UNDEF as _U
    return {"a": [r[0] for r in d],
            "miss": [None if (_v := _ix_get(r, "zz")) is _U else _v for r in d],
            "c": [r[2] for r in d]}


# ---------------------------------------------------------------- order_pipeline
#: 与 ``bench.py`` 用的随机种子一致，保证同一份数据形态。
SEED = 20261024


def make_orders(n, sep_every=13, seed=SEED):
    """订单文档 ``{"payload": {"orders": [...]}}``（README 附录 B 的完整样例同款）。

    ``qty`` 每 13 条出现一次 0（被 ``before`` 过滤掉，产出 12/13 行）；
    ``buyer.level`` 出现 1/3、``buyer.note`` 出现 1/7（optional 缺键分支）。
    """
    rnd = random.Random(seed)
    orders = []
    for i in range(n):
        o = {
            "id": "ORD-%05d" % i,
            "qty": 0 if (sep_every and i % sep_every == 0) else (i % 13),
            "price": float((i % 97) + 1) / 4,
            "item": {"sku": "SKU%04d" % (i % 501), "from": "URC", "to": "PEK"},
            "buyer": {"name": "user%d" % (i % 37)},
        }
        if i % 3 == 0:
            o["buyer"]["level"] = "VIP"
        if i % 7 == 0:
            o["buyer"]["note"] = "gift"
        orders.append(o)
    rnd.shuffle(orders)
    return {"payload": {"orders": orders}}


def order_pipeline_hand(payload):
    """完整样例 ``order_pipeline`` 的手写等价实现。

    与 DCG 产物语义一致：``before`` 过滤 ``qty <= 0``；optional 缺键落 ``None``；
    ``after`` 追加 ``orders``/``revenue``/``count``/``total_revenue`` 四列。
    """
    data = payload["payload"]["orders"]
    order_id, sku, qty_col, unit_price, customer = [], [], [], [], []
    level, note, amount, bulk, discount = [], [], [], [], []
    fee, tag, label, leg, qty_sum, pair = [], [], [], [], [], []
    sqrt = math.sqrt
    for o in data:
        if not (o["qty"] > 0):
            continue
        item = o["item"]
        buyer = o["buyer"]
        q = o["qty"]
        p = o["price"]
        a = q * p
        big = q >= 10
        disc = a * 0.9 if big else a
        order_id.append(o["id"])
        sku.append(item["sku"])
        qty_col.append(q)
        unit_price.append(p)
        customer.append(buyer["name"])
        level.append(buyer.get("level"))
        note.append(buyer.get("note"))
        amount.append(a)
        bulk.append(big)
        discount.append(disc)
        fee.append(disc + sqrt(a))
        tag.append(item["sku"][:3].upper() + '-' + str(q))
        label.append('BULK' if big else 'SINGLE')
        leg.append({"from": item["from"], "to": item["to"], "span": q * 2})
        qty_sum.append(q)
        pair.append([q, q * p])
    return {
        "order_id": order_id, "sku": sku, "qty": qty_col,
        "unit_price": unit_price, "customer": customer, "level": level,
        "note": note, "amount": amount, "bulk": bulk, "discount": discount,
        "fee": fee, "tag": tag, "label": label, "leg": leg,
        "stats": {"n_rows": 1, "qty_sum": qty_sum, "pair": pair},
        "orders": order_id, "revenue": amount, "count": len(qty_col),
        "total_revenue": sum(amount),
    }
