//! DCG 可选加速后端（`dcg._accel._accel`）。
//!
//! 只做「和 `dcg.runtime` helper 逐值等价」的那部分：读不到精确类型、遇到溢出、
//! 遇到任何不能证明等价的输入，一律返回 Python `None`，由 helper 的门控落回纯
//! Python。本模块**永不改变语义**，最坏只是没加速。
//!
//! 子集边界：入参必须是精确 `list`/`tuple`，元素必须是精确 `int`（`bool` 不算）；
//! `i64` 装不下的整数落回 Python；`av` 另有全 `float` 数组的窄域。
//! 本文件导出 `mn` / `mx` / `av` / `ds`；字符串、时间、URL、整数格式化类在 `newfn.rs`。

use pyo3::prelude::*;
use pyo3::types::{PyAny, PyBool, PyDict, PyFloat, PyInt, PyList, PyString, PyTuple};

/// 精确 int 视图；任何元素不是精确 `int` 或超出 `i64` → `None`（落回 Python）。
fn ints(data: &Bound<'_, PyAny>) -> Option<Vec<i64>> {
    // 直接在迭代器上判类型 + 抽取，不要先 collect 成 Vec：那会多一次 incref/decref，把「不是 int 就拒绝」的快路径拖慢。
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

// ---- 门控启用者（`dcg/runtime.py` 只对这四个做门控）

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

/// `$average`：和超出 `2^53` 时落回 Python（Python 用大整数累加后做真除法）。
/// 全 `float` 数组按同样顺序累加，结果逐位相同；混合 int/float 落回 Python。
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
