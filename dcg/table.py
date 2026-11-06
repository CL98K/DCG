# -*- coding: utf-8 -*-
"""DCG 表连接运行时（2.2.3）：``Table`` / ``Index`` / ``RangeIndex``。

右表在运行时注入（``run(data, tables=...)``）。索引在 ``prepare`` / ``bind``
阶段**一次性**建好，产物里只剩槽位访问（``_t["customers"].s0``），运行期不再
判断模式或策略。设计见 ``docs/join-design.md``。

用法::

    t = dcg.Table(customers)                 # 一份行数据
    t.index(key="id", mode="strict", strategy="one")
    t.index(key="region", mode="loose", strategy="all")
    out = run(orders, {"customers": t})      # 或先 dcg.prepare(schema, {...})

索引只存**行引用**，不复制行；列不建索引，探测拿到行后 ``row.get(col)``。
"""
from bisect import bisect_right

from .runtime import UNDEF as _U

__all__ = ["Table", "Index", "RangeIndex", "prepare", "bind"]

#: `all` 策略未命中的返回值（空元组，只读；生成代码里绑成局部名）
_EMPTY = ()
#: 区间索引里「上界开放」用的哨兵（`hi: null`）
_INF = float("inf")


def _norm_path(key):
    """``'id'`` / ``'$.id'`` / ``'$.a.b'`` → 列名元组。"""
    if key.startswith("$."):
        key = key[2:]
    elif key.startswith("$"):
        key = key[1:]
    return tuple(key.split(".")) if "." in key else (key,)


def _mk_getter(key):
    """把右键（列名或 ``$.`` 路径）编译成一个取值的函数。

    复合键传列表/元组：逐个取分量，任一分量为 ``None`` 时整体返回 ``None``
    （NULL 分量永不匹配），否则返回元组——与生成代码里的探测键形状一致。
    """
    if type(key) is list or type(key) is tuple:
        if not key:
            raise ValueError("复合键不能为空")
        getters = [_mk_getter(k) for k in key]

        def get_composite(row):
            vals = []
            for g in getters:
                v = g(row)
                if v is None:
                    return None
                vals.append(v)
            return tuple(vals)

        return get_composite
    parts = _norm_path(key)
    if len(parts) == 1:
        name = parts[0]
        return lambda row: row.get(name)

    def get(row):
        v = row
        for p in parts:
            if type(v) is not dict:
                return None
            v = v.get(p)
            if v is None:
                return None
        return v

    return get


def _key_id(key):
    """索引缓存键：复合键用元组，单键用字符串。"""
    if type(key) is list or type(key) is tuple:
        return tuple(key)
    return key


class Index:
    """哈希索引。一个 ``Table`` 可以挂多个，索引只存行引用。

    分桶：``strict`` 按值的 ``type`` 分到 ``b_int`` / ``b_str`` / ``b_float`` /
    ``b_bool`` / ``b_other``（左键类型可证时生成代码直接进桶，省一次 ``type()``）；
    ``loose`` 一律 ``str(v)`` 后落 ``b_loose``。
    """

    def __init__(self, table, key, mode="strict", strategy="one"):
        self.table = table
        self.key = key
        self.mode = mode
        self.strategy = strategy
        self.b_int = {}
        self.b_str = {}
        self.b_float = {}
        self.b_bool = {}
        self.b_other = {}
        self.b_loose = {}
        self._get = _mk_getter(key)
        self._agg = {}
        self._build()

    # -- 构建 -------------------------------------------------------------
    def _build(self):
        rows = self.table.rows
        if rows is None:
            raise RuntimeError(
                "Table 的行数据已释放（keep_rows=False），无法再建索引")
        get = self._get
        all_mode = self.strategy == "all"
        loose = self.mode == "loose"
        bi, bs, bf, bb, bo = (self.b_int, self.b_str, self.b_float,
                              self.b_bool, self.b_other)
        for row in rows:
            k = get(row)
            if k is None:            # NULL 永不匹配
                continue
            if loose:
                k = str(k)
                d = self.b_loose
            else:
                t = type(k)
                if t is str:
                    d = bs
                elif t is bool:
                    d = bb
                elif t is int:
                    d = bi
                elif t is float:
                    d = bf
                else:
                    try:
                        hash(k)
                    except TypeError:
                        continue     # 不可哈希的值不进索引（标量键永远不会命中）
                    d = bo
            if all_mode:
                cur = d.get(k)
                if cur is None:
                    d[k] = [row]
                else:
                    cur.append(row)
            elif k in d:
                if self.strategy == "one":
                    raise ValueError(
                        "右表键重复：%r 的 %r 命中多行（strategy='one'）；"
                        "改用 strategy='first' 或 'all'" % (self.key, k))
                # strategy='first'：保留首条
            else:
                d[k] = row

    # -- 探测 -------------------------------------------------------------
    def _probe(self, k):
        """把探测键规整成索引里存的样子。

        复合键的左键是数组（``[1,'x']``），索引里存的是元组；不转就永远探测不到
        （``list`` 不可哈希，还会在 ``dict.get`` 上抛 ``TypeError``）。``loose``
        两侧必须 ``str()`` 同一个形状——先折元组再 ``str()``：否则左键得
        ``"[1, 'x']"``、索引里存的是 ``"(1, 'x')"``，复合键永不命中。
        """
        if type(k) is list:
            k = tuple(k)
        if self.mode == "loose":
            return str(k)
        return k

    def bucket(self, k):
        """按左键类型选桶（strict）；loose 恒为 ``b_loose``。"""
        if self.mode == "loose":
            return self.b_loose
        t = type(k)
        if t is str:
            return self.b_str
        if t is int:
            return self.b_int
        if t is float:
            return self.b_float
        if t is bool:
            return self.b_bool
        return self.b_other

    def g(self, k):
        """``one`` / ``first``：返回行或 ``None``。

        返回的是**表内行对象本身**（不是副本）——热路径不复制。调用方只读；
        要能安全改写请用 ``gr``（整行副本）或 ``gv``（单列值）。
        """
        k = self._probe(k)
        return self.bucket(k).get(k)

    def a(self, k):
        """``all``：返回行列表；未命中返回 ``()``（只读，不共享可写容器）。

        命中时返回的是**索引内部列表本体**（``all`` 桶里就是它），不得原地修改；
        要副本请用 ``gar``。
        """
        k = self._probe(k)
        return self.bucket(k).get(k, _EMPTY)

    # -- 生成代码入口（`$lookup` 编译到这些方法，见 codegen.lookup）---------
    def gv(self, k, col):
        """``one`` / ``first`` + 列：命中行的列值，未命中 ``None``。

        右表行里没有这一列时同样得 ``None``（与「该列值为 null」不可区分）。
        """
        k = self._probe(k)
        r = self.bucket(k).get(k)
        return None if r is None else r.get(col)

    def ga(self, k, col):
        """``all`` + 列：命中的所有列值（跳过 ``None``），未命中 ``[]``。"""
        k = self._probe(k)
        rows = self.bucket(k).get(k, _EMPTY)
        return [v for v in (r.get(col) for r in rows) if v is not None]

    def gr(self, k):
        """``one`` / ``first`` + 整行：行副本，未命中 ``undefined``。"""
        k = self._probe(k)
        r = self.bucket(k).get(k)
        return _U if r is None else dict(r)

    def gar(self, k):
        """``all`` + 整行：行副本列表，未命中 ``[]``。"""
        return [dict(r) for r in self.a(k)]

    def _cand(self, k):
        """谓词形态的候选行：``all`` 桶是列表，``one``/``first`` 桶是单行。

        未命中时桶取不到值，必须返回空元组——否则 ``(_EMPTY,)`` 会被当成一行
        候选，谓词里访问字段即 ``AttributeError``。
        """
        k = self._probe(k)
        v = self.bucket(k).get(k, _EMPTY)
        if self.strategy == "all":
            return v
        if v is _EMPTY or v is None:
            return ()
        return (v,)

    def q(self, k, left, pred, col):
        """谓词：首个命中的行（取列或整行副本；未命中列值 ``None``、整行 ``undefined``）。"""
        for r in self._cand(k):
            if pred(left, r):
                return dict(r) if col is None else r.get(col)
        return _U if col is None else None

    def qa(self, k, left, pred, col):
        """谓词 + 全量：所有命中的列值（跳过 ``None``）或行副本。"""
        out = []
        for r in self._cand(k):
            if pred(left, r):
                if col is None:
                    out.append(dict(r))
                else:
                    v = r.get(col)
                    if v is not None:
                        out.append(v)
        return out

    # -- 聚合（C7）：按列缓存，`$sum($lookup(...,'all'))` 变 O(1) ------------
    def agg(self, col, kind):
        """``kind`` ∈ ``sum/count/max/min/avg``；只对 ``all`` 索引有意义。"""
        if kind not in ("sum", "count", "max", "min", "avg"):
            raise ValueError(
                "聚合种类必须是 'sum' / 'count' / 'max' / 'min' / 'avg'，"
                "得到 %r" % (kind,))
        cache = self._agg.get((col, kind))
        if cache is not None:
            return cache
        if self.strategy != "all":
            raise ValueError("聚合预计算只对 strategy='all' 的索引有意义")
        get = _mk_getter(col)
        out = {}
        for k, rows in self._iter_buckets():
            vals = []
            for row in rows:
                v = get(row)
                if v is None:
                    continue
                vals.append(v)
            if not vals:
                out[k] = 0 if kind in ("sum", "count") else None
            elif kind == "sum":
                out[k] = sum(vals)
            elif kind == "count":
                out[k] = len(vals)
            elif kind == "max":
                out[k] = max(vals)
            elif kind == "min":
                out[k] = min(vals)
            else:
                out[k] = sum(vals) / len(vals)
        self._agg[(col, kind)] = out
        return out

    def _iter_buckets(self):
        for d in (self.b_int, self.b_str, self.b_float, self.b_bool,
                  self.b_other, self.b_loose):
            for k, v in d.items():
                yield k, v

    def spec(self):
        return ("hash", self.key, self.mode, self.strategy, None)


class RangeIndex:
    """区间索引：按 ``lo`` 排序 + bisect；``pick(x)`` 找 ``lo <= x < hi`` 的行。

    ``hi`` 为 ``None`` 表示上界开放（``lo <= x``），按 ``+inf`` 处理——否则
    「最后一档没有上界」这种最常见的税率/阶梯表永远命中不了。
    """

    def __init__(self, table, key, hi, mode="strict", strategy="one"):
        self.table = table
        self.key = key
        self.hi = hi
        self.mode = mode
        self.strategy = strategy
        get_lo = _mk_getter(key)
        get_hi = _mk_getter(hi)
        rows = table.rows
        if rows is None:
            # 与 `Index._build` 同口径：行数据已释放时**显式报错**，不能静默建成
            # 空索引——那会让后续查询永远未命中，错得无声无息。
            raise RuntimeError(
                "Table 的行数据已释放（keep_rows=False），无法再建索引")
        pairs = []
        for row in rows:
            lo = get_lo(row)
            if lo is None:
                continue
            h = get_hi(row)
            pairs.append((lo, _INF if h is None else h, row))
        pairs.sort(key=lambda p: p[0])
        self._los = [p[0] for p in pairs]
        self._his = [p[1] for p in pairs]
        self._rows = [p[2] for p in pairs]

    def pick(self, x):
        """第一个命中的行；区间可重叠时从后往前扫。"""
        i = bisect_right(self._los, x) - 1
        los = self._los
        his = self._his
        while i >= 0:
            if his[i] > x:
                return self._rows[i]
            # 后面的区间 lo 更大，不会更早命中；往前继续找重叠区间
            i -= 1
        return None

    def pick_all(self, x):
        """命中的**全部**行（区间可重叠时会有多条），按 ``lo`` 升序。"""
        i = bisect_right(self._los, x) - 1
        los = self._los
        his = self._his
        rows = self._rows
        out = []
        while i >= 0:
            if his[i] > x:
                out.append(rows[i])
            i -= 1
        out.reverse()
        return out

    def pv(self, x, col):
        """区间 + 列：命中行的列值，未命中 ``None``。"""
        r = self.pick(x)
        return None if r is None else r.get(col)

    def pr(self, x):
        """区间 + 整行：行副本，未命中 ``undefined``。"""
        r = self.pick(x)
        return _U if r is None else dict(r)

    def pva(self, x, col):
        """区间 + 列 + ``all``：命中的所有列值（跳过 ``None``），未命中 ``[]``。"""
        return [v for v in (r.get(col) for r in self.pick_all(x)) if v is not None]

    def pra(self, x):
        """区间 + 整行 + ``all``：命中行的副本列表，未命中 ``[]``。"""
        return [dict(r) for r in self.pick_all(x)]

    def spec(self):
        return ("range", self.key, self.mode, self.strategy, self.hi)


class Table:
    """一份行数据 + 若干索引。``index()`` 按 ``(key, mode, strategy)`` 幂等去重。"""

    def __init__(self, rows, keep_rows=True):
        if isinstance(rows, Table):
            rows = rows.rows
        self.rows = list(rows)
        self.keep_rows = bool(keep_rows)
        self._indexes = {}

    # -- 建索引 -----------------------------------------------------------
    def index(self, key, mode="strict", strategy="one"):
        if mode not in ("strict", "loose"):
            raise ValueError("mode 必须是 'strict' 或 'loose'，得到 %r" % (mode,))
        if strategy not in ("one", "first", "all"):
            raise ValueError(
                "strategy 必须是 'one' / 'first' / 'all'，得到 %r" % (strategy,))
        ck = (_key_id(key), mode, strategy, None)
        idx = self._indexes.get(ck)
        if idx is None:
            idx = Index(self, key, mode, strategy)
            self._indexes[ck] = idx
        return idx

    def range(self, key, hi, mode="strict", strategy="one"):
        """区间索引（按 ``(key, hi, mode, strategy)`` 幂等）。

        ``mode`` / ``strategy`` 只用于规格匹配：``RangeIndex`` 本身恒按
        「严格 + 单条」语义工作，但 ``specs_of`` 里用户写的模式/策略会带过来，
        规格必须原样记住，否则 ``bind`` 永远匹配不上。
        """
        ck = (_key_id(key), mode, strategy, hi)
        idx = self._indexes.get(ck)
        if idx is None:
            idx = RangeIndex(self, key, hi, mode, strategy)
            self._indexes[ck] = idx
        return idx

    def find(self, kind, key, mode, strategy, hi):
        """按规格找已有索引（``bind`` 用）；没有返回 ``None``。

        区间索引的 ``mode`` / ``strategy`` 不参与运行期语义（``RangeIndex`` 恒按
        「严格 + 单条」工作，``all`` 形态由生成码选 ``pva`` / ``pra``），所以
        区间规格先按精确四元组找，找不到再按 ``(key, hi)`` 兜底——手工
        ``t.range(key, hi)`` 建的索引因此也能被 ``bind`` 认出来。
        """
        ck = (_key_id(key), mode, strategy, hi)
        idx = self._indexes.get(ck)
        if idx is None and kind == "range":
            kid = _key_id(key)
            for (k2, _m, _s, h2), v in self._indexes.items():
                if k2 == kid and h2 == hi and isinstance(v, RangeIndex):
                    return v
        return idx

    def release(self):
        """释放行数据（``keep_rows=False`` 的 Table 在索引建齐后调用）。"""
        if self.keep_rows:
            raise RuntimeError("keep_rows=True 的 Table 不允许释放行数据")
        self.rows = None

    def __len__(self):
        return 0 if self.rows is None else len(self.rows)

    def __repr__(self):
        return "<dcg.Table rows=%r indexes=%d>" % (
            0 if self.rows is None else len(self.rows), len(self._indexes))


def _as_table(v):
    if isinstance(v, Table):
        return v
    return Table(v)


def _spec_parts(spec):
    """规格：``(kind, key, mode, strategy, hi)`` 元组或同名字段的 dict。"""
    if isinstance(spec, dict):
        return (spec.get("kind", "hash"), spec["key"], spec.get("mode", "strict"),
                spec.get("strategy", "one"), spec.get("hi"))
    return spec


def prepare(specs, tables, release=True):
    """按规格建齐索引并绑定槽位 ``s0``、``s1``…

    ``specs`` 是有序的规格列表（顺序即槽位号），每项
    ``(kind, key, mode, strategy, hi)``。``tables`` 的值可以是 ``Table`` 或裸行列表
    （裸列表会被包成 ``Table``，此时忽略其 ``keep_rows``）。
    """
    bound = {}
    for i, spec in enumerate(specs):
        kind, key, mode, strategy, hi = _spec_parts(spec)
        name = spec["table"] if isinstance(spec, dict) else None
        if name is None:
            raise TypeError("规格缺少表名")
        if name not in tables:
            # 与 `_tm` 同口径：缺表要说清注入了什么，别甩裸 `KeyError`
            raise TypeError("表 %r 未注入；已注入：%s" % (
                name, sorted(tables) if hasattr(tables, "__iter__") else tables))
        table = _as_table(tables[name])
        if kind == "range":
            idx = table.range(key, hi, mode, strategy)
        else:
            idx = table.index(key, mode, strategy)
        setattr(table, "s%d" % i, idx)
        bound[name] = table
    if release:
        for t in bound.values():
            if not t.keep_rows and t.rows is not None:
                t.release()
    return bound


def _tm(tables, specs):
    """``run(_d, _t)`` 的入口：规整成 ``Table``、按规格绑定槽位，返回 ``{名: Table}``。

    裸行列表会被包成 ``Table``（每次调用都重建索引——性能敏感场景请直接传
    ``Table`` 或先 ``prepare``）。``keep_rows=False`` 的 Table 在这里**不**释放行。
    """
    if not tables:
        need = sorted({s["table"] for s in specs})
        raise TypeError(
            "未注入表：schema 需要 %s；请写 run(data, tables={...})" % need)
    bound = {}
    for name, v in tables.items():
        bound[name] = v if isinstance(v, Table) else Table(v)
    for spec in specs:
        if spec["table"] not in bound:
            raise TypeError("表 %r 未注入；已注入：%s" % (
                spec["table"], sorted(bound)))
    return prepare(specs, bound, release=False)


def bind(specs, tables):
    """只做规格匹配与槽位绑定：索引必须已存在，缺就报错并列出。"""
    bound = {}
    for i, spec in enumerate(specs):
        kind, key, mode, strategy, hi = _spec_parts(spec)
        name = spec["table"] if isinstance(spec, dict) else None
        if name is None:
            raise TypeError("规格缺少表名")
        table = tables.get(name) if hasattr(tables, "get") else None
        if not isinstance(table, Table):
            raise TypeError(
                "bind 的 tables[%r] 必须是 dcg.Table，得到 %r；裸列表请用 dcg.prepare"
                % (name, type(table).__name__))
        idx = table.find(kind, key, mode, strategy, hi)
        if idx is None:
            have = sorted(
                "%s/%s/%s" % (s[1], s[2], s[3]) for s in
                (v.spec() for v in table._indexes.values()))
            raise ValueError(
                "表 %r 缺少索引 %s；现有：%s" % (
                    name, "%s/%s/%s" % (key, mode, strategy),
                    ", ".join(have) or "无"))
        setattr(table, "s%d" % i, idx)
        bound[name] = table
    return bound
