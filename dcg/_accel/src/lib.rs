//! DCG 可选加速后端（`dcg._accel._accel`）。
//!
//! **只做「和 `dcg.runtime` helper 逐值等价」的那部分**：读不到精确类型、遇到
//! 溢出、遇到任何不能证明等价的输入，一律返回 Python `None`，由 helper 内的
//! 门控落回纯 Python 路径。因此本模块**永不改变语义**，最坏情况只是「没加速」。
//!
//! 为什么不用异常表达「不支持」：`NotImplementedError` 的构造 + 抛出 + 捕获在热
//! 路径上要 ~1 µs，比整个算子还贵；`None` 是一次引用返回，~20 ns。
//!
//! 为什么空数组也返回 `None`：空数组的正确答案是 `undefined`，而拿到那个单例要
//! `import dcg.runtime`（本模块正是被它导入的，构成循环）。让 Python 侧处理空
//! 数组既避免循环，也避免把单例缓存进 Rust。
//!
//! 子集边界（与 `rust_poc/src/ops.rs` 的严格契约一致）：
//! * 入参必须是精确 `list` 或 `tuple`；
//! * 元素必须是精确 `int` —— `bool` 是 `int` 的子类，但 `is_exact_instance_of`
//!   为假，所以 `True`/`False` 自动落回 Python（`_mn`/`_mx` 要跳过它们，`_av`
//!   遇到它们要返回 undefined，这些规则不在 Rust 里猜）；
//! * `i64` 装不下的整数落回 Python（Python 是大整数，Rust 不是）；
//! * `av` 另有一条 float 窄域（全 `float` 数组按 f64 同序累加，与 Python 侧
//!   逐位相同）；混合 int/float 仍落回 Python。
//!
//! 本文件导出 4 个 `#[pyfunction]`：`mn` / `mx` / `av` / `ds`。
//! `$sum`/`$sort`/`$uppercase`/`$distinct(str)` 实测**恒慢**（Python 侧是 C 内建
//! `sum()`/`sorted()`/`str.upper()`），按「≥2× 才实现」的门槛已从本 crate 删除。
//! 字符串类、时间类、URL 类、整数格式化类的加速在 `newfn.rs`。

use pyo3::prelude::*;
use pyo3::types::{PyAny, PyBool, PyDict, PyFloat, PyInt, PyList, PyString, PyTuple};

/// 精确 int 视图；任何元素不是精确 `int` 或超出 `i64` → `None`（落回 Python）。
fn ints(data: &Bound<'_, PyAny>) -> Option<Vec<i64>> {
    // 直接在迭代器上判类型 + 抽取，**不要先 collect 成 `Vec<Bound<PyAny>>`**：
    // 那样每个元素都要做一次 incref/decref，而「元素不是 int 就拒绝」这条
    // 快路径（浮点数组、混合数组）会被这笔开销拖慢 —— 实测纯浮点入参
    // 会从「拒绝几乎免费」变成净亏。列表与元组分开写是因为 `PyList::len()`
    // 与 `PyTuple::len()` 都是 `usize`，不需要 `PyResult` 包装。
    if let Ok(l) = data.cast::<PyList>() {
        let n = l.len();
        if n == 0 {
            return None;  // 空数组的正确答案是 undefined，交给 Python
        }
        let mut out = Vec::with_capacity(n);
        for it in l.iter() {
            if !it.is_exact_instance_of::<PyInt>() {
                return None;
            }
            out.push(it.extract::<i64>().ok()?);
        }
        return Some(out);
    }
    if let Ok(t) = data.cast::<PyTuple>() {
        let n = t.len();
        if n == 0 {
            return None;
        }
        let mut out = Vec::with_capacity(n);
        for it in t.iter() {
            if !it.is_exact_instance_of::<PyInt>() {
                return None;
            }
            out.push(it.extract::<i64>().ok()?);
        }
        return Some(out);
    }
    None
}

/// 精确 float 视图；任何元素不是精确 `float` → `None`。
///
/// `$average` 的浮点分支需要它：Python 侧 `total += v` 从 `0` 起累加，第一次
/// `0 + f` 就是 `f` 本身（IEEE 双方一致），之后每一步都是 `f64` 加法，**顺序
/// 相同则结果逐位相同**。所以浮点数组不必落回 Python —— 落回反而让「门控放行
/// 又在 Rust 里被拒」变成净亏。
fn floats(data: &Bound<'_, PyAny>) -> Option<Vec<f64>> {
    fn take<'py, I>(items: I, cap: usize) -> Option<Vec<f64>>
    where
        I: Iterator<Item = Bound<'py, PyAny>>,
    {
        let mut out = Vec::with_capacity(cap);
        for it in items {
            if !it.is_exact_instance_of::<PyFloat>() {
                return None;
            }
            out.push(it.extract::<f64>().ok()?);
        }
        Some(out)
    }
    if let Ok(l) = data.cast::<PyList>() {
        let n = l.len();
        if n == 0 {
            return None;
        }
        return take(l.iter(), n);
    }
    if let Ok(t) = data.cast::<PyTuple>() {
        let n = t.len();
        if n == 0 {
            return None;
        }
        return take(t.iter(), n);
    }
    None
}

// ---------------------------------------------------------------- 门控启用者
// `dcg/runtime.py` 只对这四个做门控：实测在 n 达到阈值后稳定 ≥1.3×。

/// `$min`：单遍扫描取最小。
#[pyfunction]
fn mn(py: Python<'_>, data: &Bound<'_, PyAny>) -> PyResult<Py<PyAny>> {
    let Some(v) = ints(data) else {
        return Ok(py.None());
    };
    let mut best = v[0];
    for &x in &v[1..] {
        if x < best {
            best = x;
        }
    }
    Ok(PyInt::new(py, best).into_any().unbind())
}

/// `$max`：单遍扫描取最大。
#[pyfunction]
fn mx(py: Python<'_>, data: &Bound<'_, PyAny>) -> PyResult<Py<PyAny>> {
    let Some(v) = ints(data) else {
        return Ok(py.None());
    };
    let mut best = v[0];
    for &x in &v[1..] {
        if x > best {
            best = x;
        }
    }
    Ok(PyInt::new(py, best).into_any().unbind())
}

/// `$average`：和超出 `2^53` 时落回 Python（Python 用大整数累加后做真除法，
/// `f64` 在这里会丢位，必须让 Python 算）。
///
/// 浮点数组单独一条路：Python 的 `total += v` 从 `0` 起累加，`0 + f` 就是 `f`，
/// 之后每步都是 `f64` 加法；这里按**同样顺序**累加，结果逐位相同。
/// 混合 int/float 落回 Python（Python 在首个浮点之前保持整数精确，那个跨越点
/// 的舍入行为不在窄域里复刻）。
#[pyfunction]
fn av(py: Python<'_>, data: &Bound<'_, PyAny>) -> PyResult<Py<PyAny>> {
    if let Some(v) = floats(data) {
        let mut total: f64 = 0.0;
        for &x in &v {
            total += x;
        }
        let avg = total / (v.len() as f64);
        return Ok(PyFloat::new(py, avg).into_any().unbind());
    }
    let Some(v) = ints(data) else {
        return Ok(py.None());
    };
    let mut total: i64 = 0;
    for &x in &v {
        match total.checked_add(x) {
            Some(t) => total = t,
            None => return Ok(py.None()),
        }
    }
    const LIMIT: i64 = 1i64 << 53;
    if total > LIMIT || total < -LIMIT {
        return Ok(py.None());
    }
    let avg = (total as f64) / (v.len() as f64);
    Ok(PyFloat::new(py, avg).into_any().unbind())
}

/// `$distinct`（精确 int）：保序去重。
///
/// `dcg.runtime._ds` 已经是 O(n) 的键快路径，这里的收益来自「省掉每元素的
/// Python 字节码与 set 操作」，实测 n≥2 即 1.28×，n=512 到 2.13×。
#[pyfunction]
fn ds(py: Python<'_>, data: &Bound<'_, PyAny>) -> PyResult<Py<PyAny>> {
    let Some(v) = ints(data) else {
        return Ok(py.None());
    };
    let mut seen = std::collections::HashSet::with_capacity(v.len());
    let out = PyList::empty(py);
    for x in v {
        if seen.insert(x) {
            out.append(PyInt::new(py, x))?;
        }
    }
    Ok(out.into_any().unbind())
}

// ---------------- 供 newfn.rs 复用的类型读取（`use super::*` 可见） ----------------

/// `dcg.runtime.UNDEF` 单例（`newfn.rs` 的 `$fromMillis` 负毫秒 / 空数组等形态要用）。
static UNDEF: pyo3::sync::PyOnceLock<Py<PyAny>> = pyo3::sync::PyOnceLock::new();

fn undef<'py>(py: Python<'py>) -> PyResult<&'py Bound<'py, PyAny>> {
    let p = UNDEF.get_or_try_init(py, || -> PyResult<Py<PyAny>> {
        Ok(py.import("dcg.runtime")?.getattr("UNDEF")?.unbind())
    })?;
    Ok(p.bind(py))
}

fn as_str(v: &Bound<'_, PyAny>) -> Option<String> {
    if !v.is_exact_instance_of::<PyString>() {
        return None;
    }
    v.extract::<String>().ok()
}

fn is_seq(v: &Bound<'_, PyAny>) -> bool {
    v.is_exact_instance_of::<PyList>() || v.is_exact_instance_of::<PyTuple>()
}

fn seq_items<'py>(v: &Bound<'py, PyAny>) -> Option<Vec<Bound<'py, PyAny>>> {
    if !is_seq(v) {
        return None;
    }
    let mut out = Vec::new();
    for item in v.try_iter().ok()? {
        out.push(item.ok()?);
    }
    Some(out)
}

/// 数值：精确 int（非 bool）或精确 float。
#[allow(dead_code)]
enum Num {
    I(i128),
    F(f64),
}

#[allow(dead_code)]
fn as_num(v: &Bound<'_, PyAny>) -> Option<Num> {
    if v.is_exact_instance_of::<PyInt>() {
        return v.extract::<i128>().ok().map(Num::I);
    }
    if v.is_exact_instance_of::<PyFloat>() {
        return v.extract::<f64>().ok().map(Num::F);
    }
    None
}

fn str_py(py: Python<'_>, s: &str) -> Py<PyAny> {
    PyString::new(py, s).into_any().unbind()
}

mod newfn;

#[pymodule]
fn _accel(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(mn, m)?)?;
    m.add_function(wrap_pyfunction!(mx, m)?)?;
    m.add_function(wrap_pyfunction!(av, m)?)?;
    m.add_function(wrap_pyfunction!(ds, m)?)?;
    newfn::register(m)?;
    Ok(())
}
