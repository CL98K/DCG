//! JSONata 2.2.2 新增 helper 的加速实现（产品路径，随 `dcg._accel` 一起发布）。
//!
//! 来源：`_tmp/p3/rust/crate/src/extra.rs` 的对照实现。对照实测（比值 = Python/Rust，
//! 小输入 / 大输入，口径 `evidence/bench_protocol.py`，判读阈值 1.10）：
//!
//! | 函数 | 小输入 | 大输入 | 处置 |
//! |---|---|---|---|
//! | `o_du` `$decodeUrl` | 3.25× | 29.3× | **注册 + 门控** |
//! | `o_eu` `$encodeUrl` | 4.34× | 7.51× | **注册 + 门控** |
//! | `o_tms` `$toMillis` | 7.59× | 7.12× | **注册 + 门控** |
//! | `o_fms` `$fromMillis` | 7.41× | 3.16× | **注册 + 门控** |
//! | `o_fnm` `$formatNumber` | 3.77× | 3.68× | **注册 + 门控** |
//! | `o_pgi` `$parseInteger` | 2.74× | 3.29× | **注册 + 门控** |
//! | `o_fgi` `$formatInteger` | 2.17× | 1.49× | 未注册（大输入不过 2×） |
//! | `o_zp` `$zip` | 1.99× | 1.62× | 未注册（边缘） |
//! | `o_cl` `$clone` | 2.00× | 1.86× | 未注册（边缘） |
//! | `o_mt` `$match` | 0.63× | 0.63× | 未注册（更慢：只是 Python 正则引擎外面一层壳） |
//!
//! 契约与 `lib.rs` 一致：能证明逐值等价的窄域内直接算，域外返回 Python `None`
//! 让 `dcg.runtime` 的门控落回纯 Python。**永不改变语义**，最坏只是没加速。

#![allow(dead_code)]

//! JSONata 2.2.2 新增 helper 的 Rust 对照实现。
//!
//! 每个函数都严格照抄 `dcg/runtime.py` 的 Python 实现：
//!   * Python 会因为**入参非法**返回 `UNDEF` 的形态 —— 这里返回同一个 `UNDEF` 哨兵；
//!   * Python 能算、但 Rust 无法证明逐值等价的形态（浮点最短表示、Decimal 舍入、
//!     非 ASCII 的 picture / Unicode 数字等）—— 这里返回 Python `None`，表示「请回退」。
//!
//! 绝不抛异常：构造 + 抛出 + 捕获一次约 1 µs，比算子本身还贵。

use super::*;


// ---------------------------------------------------------------- 通用小工具

/// Python `str.zfill(width)`：左侧补 `0`（本文件里只用于无符号十进制串）。
fn zfill(s: &str, width: usize) -> String {
    let n = s.chars().count();
    if n >= width {
        return s.to_string();
    }
    let mut out = String::with_capacity(width);
    for _ in 0..(width - n) {
        out.push('0');
    }
    out.push_str(s);
    out
}

/// Python `str.ljust(width, "0")`（fillchar 固定 `0`）。
fn ljust0(s: &str, width: usize) -> String {
    let n = s.chars().count();
    if n >= width {
        return s.to_string();
    }
    let mut out = s.to_string();
    for _ in n..width {
        out.push('0');
    }
    out
}

/// Python `round()`（银行家舍入）在 double 上的行为，结果取整。
fn round_half_even_i64(x: f64) -> Option<i64> {
    if !x.is_finite() {
        return None;
    }
    let f = x.floor();
    let diff = x - f;
    let r = if diff > 0.5 {
        f + 1.0
    } else if diff < 0.5 {
        f
    } else if (f / 2.0).fract() == 0.0 {
        f
    } else {
        f + 1.0
    };
    if r > -9.0e18 && r < 9.0e18 {
        Some(r as i64)
    } else {
        None
    }
}

/// `min(ord(ch) - 64)` 用的 ASCII 字母判定（窄域：只认 ASCII）。
fn is_ascii_hex(c: char) -> bool {
    c.is_ascii_hexdigit()
}

fn hex_val(c: char) -> u8 {
    match c {
        '0'..='9' => c as u8 - b'0',
        'a'..='f' => c as u8 - b'a' + 10,
        'A'..='F' => c as u8 - b'A' + 10,
        _ => 0,
    }
}

/// Python 的 `str.strip()`（Unicode 空白）；发现 `\x1c..\x1f`（Python 认、Unicode
/// 不认的四个控制符）时返回 `None`，把这类干扰形态排除出窄域。
fn py_strip(s: &str) -> Option<String> {
    if s.chars().any(|c| matches!(c, '\u{1c}'..='\u{1f}')) {
        return None;
    }
    Some(s.trim_matches(|c: char| c.is_whitespace()).to_string())
}

// ================================================================ $encodeUrl

fn eu_unreserved(b: u8) -> bool {
    b.is_ascii_alphanumeric() || matches!(b, b'_' | b'.' | b'-' | b'~')
}

/// `urllib.parse.quote(v, safe="-._~!$&'()*+,;=:@/?#")`
fn eu_safe(b: u8) -> bool {
    eu_unreserved(b) || matches!(b, b'!' | b'$' | b'&' | b'\'' | b'(' | b')' | b'*' | b'+'
        | b',' | b';' | b'=' | b':' | b'@' | b'/' | b'?' | b'#')
}

#[pyfunction]
#[pyo3(name = "eu")]
pub fn o_eu(py: Python<'_>, v: &Bound<'_, PyAny>) -> PyResult<Py<PyAny>> {
    let u = undef(py)?;
    let raw: Vec<u8> = if v.is_exact_instance_of::<PyString>() {
        match v.extract::<String>() {
            Ok(s) => s.into_bytes(),
            Err(_) => return Ok(u.clone().unbind()),
        }
    } else if v.is_instance_of::<pyo3::types::PyBytes>() {
        // `urllib.parse.quote` 也接受 bytes，按字节百分号编码
        v.extract::<Vec<u8>>()?
    } else {
        return Ok(u.clone().unbind());
    };
    const HEX: &[u8; 16] = b"0123456789ABCDEF";
    let mut out = String::with_capacity(raw.len());
    for &b in &raw {
        if eu_safe(b) {
            out.push(b as char);
        } else {
            out.push('%');
            out.push(HEX[(b >> 4) as usize] as char);
            out.push(HEX[(b & 0xF) as usize] as char);
        }
    }
    Ok(str_py(py, &out))
}

// ================================================================ $decodeUrl

fn url_keep(c: char) -> bool {
    matches!(
        c,
        '-' | '.' | '_' | '~' | '/' | '?' | '#' | '&' | '=' | ':' | '@' | ',' | ';' | '$' | '+'
    )
}

#[pyfunction]
#[pyo3(name = "du")]
pub fn o_du(py: Python<'_>, v: &Bound<'_, PyAny>) -> PyResult<Py<PyAny>> {
    let u = undef(py)?;
    let s = match as_str(v) {
        Some(s) => s,
        None => return Ok(u.clone().unbind()),
    };
    let cs: Vec<char> = s.chars().collect();
    let n = cs.len();
    let mut out = String::with_capacity(n);
    let mut i = 0usize;
    while i < n {
        if cs[i] != '%' {
            out.push(cs[i]);
            i += 1;
            continue;
        }
        if i + 2 >= n || !is_ascii_hex(cs[i + 1]) || !is_ascii_hex(cs[i + 2]) {
            return Ok(u.clone().unbind());
        }
        let b = hex_val(cs[i + 1]) * 16 + hex_val(cs[i + 2]);
        if b < 0x80 {
            if url_keep(b as char) {
                // 保留字符：原样留下 `%XX`（大小写按输入）
                out.push('%');
                out.push(cs[i + 1]);
                out.push(cs[i + 2]);
            } else {
                out.push(b as char);
            }
            i += 3;
            continue;
        }
        let mut buf: Vec<u8> = Vec::new();
        let mut j = i;
        while j + 2 < n && cs[j] == '%' && is_ascii_hex(cs[j + 1]) && is_ascii_hex(cs[j + 2]) {
            let nb = hex_val(cs[j + 1]) * 16 + hex_val(cs[j + 2]);
            if nb < 0x80 {
                break;
            }
            buf.push(nb);
            j += 3;
        }
        match std::str::from_utf8(&buf) {
            Ok(t) => out.push_str(t),
            Err(_) => return Ok(u.clone().unbind()),
        }
        i = j;
    }
    Ok(str_py(py, &out))
}

// ================================================================ $zip

#[pyfunction]
#[pyo3(signature = (*args))]
pub fn o_zp(py: Python<'_>, args: &Bound<'_, PyTuple>) -> PyResult<Py<PyAny>> {
    let u = undef(py)?;
    if args.len() == 0 {
        return Ok(u.clone().unbind());
    }
    let mut cols: Vec<Vec<Bound<'_, PyAny>>> = Vec::with_capacity(args.len());
    for a in args.iter() {
        match seq_items(&a) {
            Some(items) => cols.push(items),
            None => return Ok(u.clone().unbind()),
        }
    }
    let limit = cols.iter().map(|c| c.len()).min().unwrap_or(0);
    let out = PyList::empty(py);
    for i in 0..limit {
        let row = PyList::empty(py);
        for col in &cols {
            row.append(col[i].clone())?;
        }
        out.append(row)?;
    }
    Ok(out.into_any().unbind())
}

// ================================================================ $clone

fn deep_copy<'py>(
    py: Python<'py>,
    v: &Bound<'py, PyAny>,
    depth: u32,
) -> Option<Bound<'py, PyAny>> {
    if depth > 64 {
        return None;
    }
    if v.is_exact_instance_of::<PyDict>() {
        let d = v.cast::<PyDict>().ok()?;
        let out = PyDict::new(py);
        for (k, val) in d.iter() {
            let nv = deep_copy(py, &val, depth + 1)?;
            out.set_item(&k, nv).ok()?;
        }
        return Some(out.into_any());
    }
    if is_seq(v) {
        let items = seq_items(v)?;
        let out = PyList::empty(py);
        for it in items {
            out.append(deep_copy(py, &it, depth + 1)?).ok()?;
        }
        return Some(out.into_any());
    }
    Some(v.clone())
}

#[pyfunction]
pub fn o_cl(py: Python<'_>, v: &Bound<'_, PyAny>) -> PyResult<Py<PyAny>> {
    let u = undef(py)?;
    if !(v.is_exact_instance_of::<PyDict>() || is_seq(v)) {
        return Ok(u.clone().unbind());
    }
    match deep_copy(py, v, 0) {
        Some(out) => Ok(out.unbind()),
        None => Ok(py.None()),
    }
}

// ================================================================ $match

#[pyfunction]
#[pyo3(signature = (v, rx, limit = None))]
pub fn o_mt(
    py: Python<'_>,
    v: &Bound<'_, PyAny>,
    rx: &Bound<'_, PyAny>,
    limit: Option<&Bound<'_, PyAny>>,
) -> PyResult<Py<PyAny>> {
    let u = undef(py)?;
    if !v.is_exact_instance_of::<PyString>() {
        return Ok(u.clone().unbind());
    }
    if !rx.hasattr("finditer")? {
        return Ok(u.clone().unbind());
    }
    let mut cap: i128 = -1;
    if let Some(lim) = limit {
        if !lim.is(u) {
            if lim.is_exact_instance_of::<PyBool>() {
                return Ok(u.clone().unbind());
            }
            match as_num(lim) {
                Some(Num::I(i)) => cap = i,
                Some(Num::F(f)) => {
                    if !f.is_finite() {
                        return Ok(u.clone().unbind());
                    }
                    cap = f.trunc() as i128;
                }
                None => return Ok(u.clone().unbind()),
            }
            if cap <= 0 {
                return Ok(u.clone().unbind());
            }
        }
    }

    let iter = match rx.call_method1("finditer", (v,)) {
        Ok(it) => it,
        Err(_) => return Ok(u.clone().unbind()),
    };
    let out = PyList::empty(py);
    let mut count: i128 = 0;
    let it = match iter.try_iter() {
        Ok(it) => it,
        Err(_) => return Ok(u.clone().unbind()),
    };
    for item in it {
        let m = match item {
            Ok(m) => m,
            Err(_) => return Ok(u.clone().unbind()),
        };
        let g0 = match m.call_method0("group") {
            Ok(g) => g,
            Err(_) => return Ok(u.clone().unbind()),
        };
        let idx = match m.call_method0("start") {
            Ok(i) => i,
            Err(_) => return Ok(u.clone().unbind()),
        };
        let groups = match m.call_method0("groups") {
            Ok(g) => g,
            Err(_) => return Ok(u.clone().unbind()),
        };
        let gl = PyList::empty(py);
        match groups.try_iter() {
            Ok(gi) => {
                for g in gi {
                    match g {
                        Ok(x) => {
                            gl.append(x)?;
                        }
                        Err(_) => return Ok(u.clone().unbind()),
                    }
                }
            }
            Err(_) => return Ok(u.clone().unbind()),
        }
        let d = PyDict::new(py);
        d.set_item("match", g0)?;
        d.set_item("index", idx)?;
        d.set_item("groups", gl)?;
        out.append(d)?;
        count += 1;
        if cap > 0 && count >= cap {
            break;
        }
    }
    if count == 0 {
        return Ok(u.clone().unbind());
    }
    Ok(out.into_any().unbind())
}

// ================================================================ 日历工具

const MONTHS: [&str; 12] = [
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
];
const WEEKDAYS: [&str; 7] = [
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
];

/// 1970-01-01 起的天数 -> (年, 月, 日)（Howard Hinnant 的 civil_from_days）。
fn civil_from_days(z: i64) -> (i64, i64, i64) {
    let z = z + 719_468;
    let era = if z >= 0 { z } else { z - 146_096 } / 146_097;
    let doe = z - era * 146_097;
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let y = yoe + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = doy - (153 * mp + 2) / 5 + 1;
    let m = if mp < 10 { mp + 3 } else { mp - 9 };
    (if m <= 2 { y + 1 } else { y }, m, d)
}

/// (年, 月, 日) -> 1970-01-01 起的天数。
fn days_from_civil(y: i64, m: i64, d: i64) -> i64 {
    let y = if m <= 2 { y - 1 } else { y };
    let era = if y >= 0 { y } else { y - 399 } / 400;
    let yoe = y - era * 400;
    let mp = (m + 9) % 12;
    let doy = (153 * mp + 2) / 5 + d - 1;
    let doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    era * 146_097 + doe - 719_468
}

fn days_in_month(y: i64, m: i64) -> i64 {
    match m {
        1 | 3 | 5 | 7 | 8 | 10 | 12 => 31,
        4 | 6 | 9 | 11 => 30,
        2 => {
            if (y % 4 == 0 && y % 100 != 0) || y % 400 == 0 {
                29
            } else {
                28
            }
        }
        _ => 0,
    }
}

// ================================================================ $fromMillis

struct Civil {
    year: i64,
    month: i64,
    day: i64,
    hour: i64,
    minute: i64,
    second: i64,
    micro: i64,
}

/// `datetime.fromtimestamp(ms/1000.0, timezone.utc)` 的窄域等价物。
///
/// CPython 走 `_PyTime_FromFloatObject`：先把秒换成整数纳秒（ROUND_HALF_EVEN），
/// 再 floor 到秒、把余数换成微秒（同样 HALF_EVEN）。这里逐字照做。
fn millis_to_civil(ms_f: f64) -> Option<Civil> {
    if !ms_f.is_finite() {
        return None;
    }
    let secs_f = ms_f / 1000.0;
    let ns_f = secs_f * 1.0e9;
    // _PyTime_FromFloatObject 的溢出边界（_PyTime_t 是 int64 纳秒）
    if !(ns_f > -9.2e18 && ns_f < 9.2e18) {
        return None;
    }
    let ns_total = round_half_even_i64(ns_f)?;
    let secs = ns_total.div_euclid(1_000_000_000);
    let rem = ns_total.rem_euclid(1_000_000_000);
    let micro = round_half_even_i64(rem as f64 / 1000.0)?;
    let mut secs = secs;
    let mut micro = micro;
    if micro >= 1_000_000 {
        micro -= 1_000_000;
        secs += 1;
    }
    let days = secs.div_euclid(86_400);
    let sod = secs.rem_euclid(86_400);
    let (y, mo, d) = civil_from_days(days);
    if !(1..=9999).contains(&y) {
        return None;
    }
    Some(Civil {
        year: y,
        month: mo,
        day: d,
        hour: sod / 3600,
        minute: (sod % 3600) / 60,
        second: sod % 60,
        micro,
    })
}

fn fms_one(c: &Civil, letter: char, rest: &str) -> Option<String> {
    let ascii = rest.chars().all(|ch| ch.is_ascii());
    if !ascii {
        return None; // 非 ASCII：`rest.isdigit()` 的 Unicode 语义不打算复刻
    }
    let width = if !rest.is_empty() && rest.bytes().all(|b| b.is_ascii_digit()) {
        rest.chars().count()
    } else {
        0
    };
    let out = match letter {
        'Y' => zfill(&c.year.to_string(), if width == 0 { 4 } else { width }),
        'M' => {
            if rest == "Nn" {
                MONTHS[(c.month - 1) as usize].to_string()
            } else if rest == "N" {
                MONTHS[(c.month - 1) as usize][..3].to_string()
            } else if rest == "n" {
                MONTHS[(c.month - 1) as usize].to_lowercase()
            } else {
                zfill(&c.month.to_string(), if width == 0 { 2 } else { width })
            }
        }
        'D' => zfill(&c.day.to_string(), if width == 0 { 2 } else { width }),
        'F' => {
            // 1970-01-01 是周四；WEEKDAYS 以周一为 0，故偏移 3。
            let wk = days_from_civil(c.year, c.month, c.day) + 3;
            let name = WEEKDAYS[wk.rem_euclid(7) as usize];
            if rest == "Nn" {
                name.to_string()
            } else if rest == "N" {
                name[..3].to_string()
            } else {
                name.to_lowercase()
            }
        }
        'H' => zfill(&c.hour.to_string(), if width == 0 { 2 } else { width }),
        'm' => zfill(&c.minute.to_string(), if width == 0 { 2 } else { width }),
        's' => zfill(&c.second.to_string(), if width == 0 { 2 } else { width }),
        'f' => {
            let w = if width == 0 { 3 } else { width };
            let ms = zfill(&(c.micro / 1000).to_string(), 3);
            let trimmed: String = ms.chars().take(w).collect();
            ljust0(&trimmed, w)
        }
        _ => return None, // 交给调用方原样回显
    };
    Some(out)
}

#[pyfunction]
#[pyo3(signature = (ms, picture = None))]
#[pyo3(name = "fms")]
pub fn o_fms(
    py: Python<'_>,
    ms: &Bound<'_, PyAny>,
    picture: Option<&Bound<'_, PyAny>>,
) -> PyResult<Py<PyAny>> {
    let u = undef(py)?;
    if ms.is_exact_instance_of::<PyBool>() {
        return Ok(u.clone().unbind());
    }
    let ms_f = match as_num(ms) {
        Some(Num::I(i)) => i as f64,
        Some(Num::F(f)) => f,
        None => return Ok(u.clone().unbind()),
    };
    // 本机（Windows）`datetime.fromtimestamp` 的可表示区间实测是
    // [-43200000, 32536850400000) 毫秒，等于「本地 1970 年初 ~ 公元 3001 年」，
    // 越界时 Python 抛 OSError -> UNDEF。该边界依赖宿主 TZ，不能硬编码，
    // 因此只在 [0, 3.0e13) 这个任何时区都安全的窗口内给值，其余一律回退。
    if !(ms_f >= 0.0 && ms_f < 3.0e13) {
        return Ok(py.None());
    }
    let c = match millis_to_civil(ms_f) {
        Some(c) => c,
        None => {
            // Python 会 OverflowError / ValueError -> UNDEF；
            // 但只要数值还在 datetime 可表示域内，这里就是纯回退。
            if !ms_f.is_finite() {
                return Ok(u.clone().unbind());
            }
            return Ok(py.None());
        }
    };
    let pic = match picture {
        None => None,
        Some(p) => {
            if p.is_none() {
                None
            } else {
                match as_str(p) {
                    Some(s) => Some(s),
                    None => return Ok(u.clone().unbind()),
                }
            }
        }
    };
    let pic = match pic {
        None => {
            return Ok(str_py(
                py,
                &format!(
                    "{:04}-{:02}-{:02}T{:02}:{:02}:{:02}.{:03}Z",
                    c.year,
                    c.month,
                    c.day,
                    c.hour,
                    c.minute,
                    c.second,
                    c.micro / 1000
                ),
            ))
        }
        Some(p) => p,
    };

    // `_MS_PIC.sub(one, picture)`，pattern = `\[([A-Za-z])([^\[\]]*)\]`
    let cs: Vec<char> = pic.chars().collect();
    let n = cs.len();
    let mut out = String::with_capacity(n);
    let mut i = 0usize;
    while i < n {
        if cs[i] == '[' && i + 1 < n && cs[i + 1].is_ascii_alphabetic() {
            let mut j = i + 2;
            let mut closed = false;
            while j < n {
                if cs[j] == ']' {
                    closed = true;
                    break;
                }
                if cs[j] == '[' {
                    break;
                }
                j += 1;
            }
            if closed {
                let letter = cs[i + 1];
                let rest: String = cs[i + 2..j].iter().collect();
                match fms_one(&c, letter, &rest) {
                    Some(text) => {
                        out.push_str(&text);
                    }
                    None => {
                        if rest.chars().all(|ch| ch.is_ascii()) {
                            // 未知字母：原样回显 `[...]`
                            out.push_str(&cs[i..=j].iter().collect::<String>());
                        } else {
                            return Ok(py.None());
                        }
                    }
                }
                i = j + 1;
                continue;
            }
        }
        out.push(cs[i]);
        i += 1;
    }
    Ok(str_py(py, &out))
}

// ================================================================ $toMillis

fn parse_ascii_digits(b: &[u8], mut i: usize, count: usize, min_count: usize) -> Option<(i64, usize)> {
    let start = i;
    while i < b.len() && i - start < count && b[i].is_ascii_digit() {
        i += 1;
    }
    let got = i - start;
    if got < min_count {
        return None;
    }
    let mut v: i64 = 0;
    for k in start..i {
        v = v * 10 + (b[k] - b'0') as i64;
    }
    Some((v, i))
}

#[pyfunction]
#[pyo3(name = "tms")]
pub fn o_tms(py: Python<'_>, v: &Bound<'_, PyAny>) -> PyResult<Py<PyAny>> {
    let u = undef(py)?;
    let s = match as_str(v) {
        Some(s) => s,
        None => return Ok(u.clone().unbind()),
    };
    let s = match py_strip(&s) {
        Some(s) => s,
        None => return Ok(py.None()),
    };
    if !s.is_ascii() {
        // 非 ASCII 时 `\d` / `\s` 的 Unicode 语义与 int() 的宽容解析无法逐值对齐
        return Ok(py.None());
    }
    if s.is_empty() {
        return Ok(u.clone().unbind());
    }
    let b = s.as_bytes();
    let n = b.len();
    let mut i = 0usize;
    let (y, ni) = match parse_ascii_digits(b, i, 4, 4) {
        Some(x) => x,
        None => return Ok(u.clone().unbind()),
    };
    i = ni;
    if i >= n || b[i] != b'-' {
        return Ok(u.clone().unbind());
    }
    i += 1;
    let (mo, ni) = match parse_ascii_digits(b, i, 2, 2) {
        Some(x) => x,
        None => return Ok(u.clone().unbind()),
    };
    i = ni;
    if i >= n || b[i] != b'-' {
        return Ok(u.clone().unbind());
    }
    i += 1;
    let (d, ni) = match parse_ascii_digits(b, i, 2, 2) {
        Some(x) => x,
        None => return Ok(u.clone().unbind()),
    };
    i = ni;

    let (mut hh, mut mi, mut ss, mut frac) = (0i64, 0i64, 0i64, String::new());
    if i < n && (b[i] == b'T' || b[i] == b' ') {
        i += 1;
        let (h, ni) = match parse_ascii_digits(b, i, 2, 2) {
            Some(x) => x,
            None => return Ok(u.clone().unbind()),
        };
        hh = h;
        i = ni;
        if i >= n || b[i] != b':' {
            return Ok(u.clone().unbind());
        }
        i += 1;
        let (m2, ni) = match parse_ascii_digits(b, i, 2, 2) {
            Some(x) => x,
            None => return Ok(u.clone().unbind()),
        };
        mi = m2;
        i = ni;
        if i < n && b[i] == b':' {
            i += 1;
            let (s2, ni) = match parse_ascii_digits(b, i, 2, 2) {
                Some(x) => x,
                None => return Ok(u.clone().unbind()),
            };
            ss = s2;
            i = ni;
            if i < n && b[i] == b'.' {
                i += 1;
                let start = i;
                while i < n && b[i].is_ascii_digit() {
                    i += 1;
                }
                if i == start {
                    return Ok(u.clone().unbind());
                }
                frac = s[start..i].to_string();
            }
        }
    }
    while i < n && (b[i] as char).is_ascii_whitespace() {
        i += 1;
    }
    let mut tz: Option<(i64, bool)> = None; // (offset_minutes, negative)
    if i < n {
        if b[i] == b'Z' {
            tz = Some((0, false));
            i += 1;
        } else if b[i] == b'+' || b[i] == b'-' {
            let neg = b[i] == b'-';
            i += 1;
            let (oh, ni) = match parse_ascii_digits(b, i, 2, 2) {
                Some(x) => x,
                None => return Ok(u.clone().unbind()),
            };
            i = ni;
            if i < n && b[i] == b':' {
                i += 1;
            }
            let (om, ni) = match parse_ascii_digits(b, i, 2, 2) {
                Some(x) => x,
                None => return Ok(u.clone().unbind()),
            };
            i = ni;
            tz = Some((oh * 60 + om, neg));
        } else {
            return Ok(u.clone().unbind());
        }
    }
    if i != n {
        return Ok(u.clone().unbind());
    }

    // datetime(...) 的取值范围校验
    if !(1..=12).contains(&mo) || d < 1 || d > days_in_month(y, mo) {
        return Ok(u.clone().unbind());
    }
    if !(0..=23).contains(&hh) || !(0..=59).contains(&mi) || !(0..=59).contains(&ss) {
        return Ok(u.clone().unbind());
    }
    let days = days_from_civil(y, mo, d);
    let mut base = (days * 86_400 + hh * 3600 + mi * 60 + ss) * 1000;
    if let Some((off, neg)) = tz {
        if off != 0 || neg {
            base -= if neg { -off * 60_000 } else { off * 60_000 };
        }
    }
    let mut f = frac.as_str();
    if f.len() > 3 {
        f = &f[..3];
    }
    let padded = ljust0(f, 3);
    let ms_part: i64 = match padded.parse() {
        Ok(x) => x,
        Err(_) => return Ok(u.clone().unbind()),
    };
    Ok(PyInt::new(py, base + ms_part).into_any().unbind())
}

// ================================================================ 英文数字

const ONES_EN: [&str; 20] = [
    "zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
    "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen",
    "nineteen",
];
const TENS_EN: [&str; 10] = [
    "", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety",
];
const SCALES_EN: [&str; 5] = ["", "thousand", "million", "billion", "trillion"];

fn en2(n: i128) -> String {
    if n < 20 {
        return ONES_EN[n as usize].to_string();
    }
    let t = n / 10;
    let r = n % 10;
    if r == 0 {
        TENS_EN[t as usize].to_string()
    } else {
        format!("{}-{}", TENS_EN[t as usize], ONES_EN[r as usize])
    }
}

fn en3(n: i128) -> String {
    let h = n / 100;
    let r = n % 100;
    let mut out: Vec<String> = Vec::new();
    if h > 0 {
        out.push(format!("{} hundred", ONES_EN[h as usize]));
    }
    if r > 0 {
        if h > 0 {
            out.push("and".to_string());
        }
        out.push(en2(r));
    }
    out.join(" ")
}

fn en_words(n: i128) -> Option<String> {
    if n == 0 {
        return Some("zero".to_string());
    }
    let neg = n < 0;
    let mut nn = n.unsigned_abs();
    let mut groups: Vec<i128> = Vec::new();
    while nn > 0 {
        groups.push((nn % 1000) as i128);
        nn /= 1000;
    }
    if groups.len() > SCALES_EN.len() {
        return None; // 超出 trillion 的窄域
    }
    let mut parts: Vec<String> = Vec::new();
    for i in (0..groups.len()).rev() {
        if groups[i] == 0 {
            continue;
        }
        let mut seg = en3(groups[i]);
        if i > 0 {
            seg.push(' ');
            seg.push_str(SCALES_EN[i]);
        }
        parts.push(seg);
    }
    let body = parts.join(", ");
    Some(if neg { format!("-{}", body) } else { body })
}

fn en_word_value(tok: &str) -> Option<i64> {
    for (i, w) in ONES_EN.iter().enumerate() {
        if *w == tok {
            return Some(i as i64);
        }
    }
    for (i, w) in TENS_EN.iter().enumerate() {
        if !w.is_empty() && *w == tok {
            return Some(10 * i as i64);
        }
    }
    Some(match tok {
        "hundred" => 100,
        "thousand" => 1_000,
        "million" => 1_000_000,
        "billion" => 1_000_000_000,
        "trillion" => 1_000_000_000_000,
        _ => return None,
    })
}

/// Python `str.title()` 在「全小写字母 + 空格/逗号/连字符」文本上的等价物。
fn title_ascii(s: &str) -> String {
    let mut out = String::with_capacity(s.len());
    let mut prev_alpha = false;
    for ch in s.chars() {
        if ch.is_ascii_alphabetic() {
            if prev_alpha {
                out.push(ch.to_ascii_lowercase());
            } else {
                out.push(ch.to_ascii_uppercase());
            }
            prev_alpha = true;
        } else {
            out.push(ch);
            prev_alpha = false;
        }
    }
    out
}

// ================================================================ $formatInteger

const ROMAN: [(i64, &str); 13] = [
    (1000, "M"),
    (900, "CM"),
    (500, "D"),
    (400, "CD"),
    (100, "C"),
    (90, "XC"),
    (50, "L"),
    (40, "XL"),
    (10, "X"),
    (9, "IX"),
    (5, "V"),
    (4, "IV"),
    (1, "I"),
];

fn alpha_num(mut n: i128, lower: bool) -> String {
    if n <= 0 {
        return String::new();
    }
    let mut out = String::new();
    while n > 0 {
        let r = (n - 1) % 26;
        n = (n - 1) / 26;
        out.insert(0, (b'A' + r as u8) as char);
    }
    if lower {
        out.to_lowercase()
    } else {
        out
    }
}

fn roman_num(mut n: i128) -> String {
    if n <= 0 {
        return String::new();
    }
    let mut out = String::new();
    for (val, sym) in ROMAN.iter() {
        while n >= *val as i128 {
            out.push_str(sym);
            n -= *val as i128;
        }
    }
    out
}

fn roman_parse(s: &str) -> Option<i64> {
    let up = s.to_uppercase();
    let cs: Vec<char> = up.chars().collect();
    let mut total: i64 = 0;
    let mut i = 0usize;
    while i < cs.len() {
        if i + 1 < cs.len() {
            let two: String = [cs[i], cs[i + 1]].iter().collect();
            if let Some((v, _)) = ROMAN.iter().find(|(_, sym)| *sym == two) {
                total += *v;
                i += 2;
                continue;
            }
        }
        let one: String = cs[i].to_string();
        match ROMAN.iter().find(|(_, sym)| *sym == one) {
            Some((v, _)) => {
                total += *v;
                i += 1;
            }
            None => return None,
        }
    }
    Some(total)
}

/// 十进制 picture 的合法性检查 + (width, mand)；非法返回 `None`（Python 侧 UNDEF）。
fn dec_picture(picture: &str, allow_dot: bool) -> Option<(usize, usize, bool)> {
    if picture.is_empty() {
        return None;
    }
    let cs: Vec<char> = picture.chars().collect();
    if cs[0] == ',' || cs[cs.len() - 1] == ',' {
        return None;
    }
    if !allow_dot && picture.contains('.') {
        return None;
    }
    let last = match picture.rsplit_once(',') {
        Some((_, l)) => l,
        None => picture,
    };
    if !last.contains('0') {
        return None;
    }
    if !last.chars().all(|c| c == '0' || c == '#') {
        return None;
    }
    let width = last.chars().filter(|c| *c == '0' || *c == '#').count();
    let mand = last.chars().filter(|c| *c == '0').count();
    Some((width, mand, picture.contains(',')))
}

#[pyfunction]
pub fn o_fgi(
    py: Python<'_>,
    v: &Bound<'_, PyAny>,
    picture: &Bound<'_, PyAny>,
) -> PyResult<Py<PyAny>> {
    let u = undef(py)?;
    if v.is_exact_instance_of::<PyBool>() {
        return Ok(u.clone().unbind());
    }
    let n: i128 = match as_num(v) {
        Some(Num::I(i)) => i,
        Some(Num::F(f)) => {
            if !f.is_finite() || f.abs() >= 1.0e30 {
                return Ok(py.None()); // int(f) 在 Python 里是任意精度，超出窄域
            }
            f.trunc() as i128
        }
        None => return Ok(u.clone().unbind()),
    };
    let pic = match as_str(picture) {
        Some(s) => s,
        None => return Ok(u.clone().unbind()),
    };
    if pic.is_empty() {
        return Ok(u.clone().unbind());
    }
    match pic.as_str() {
        "A" => return Ok(str_py(py, &alpha_num(n, false))),
        "a" => return Ok(str_py(py, &alpha_num(n, true))),
        "I" => return Ok(str_py(py, &roman_num(n))),
        "i" => return Ok(str_py(py, &roman_num(n).to_lowercase())),
        _ => {}
    }
    if pic == "w" || pic == "W" || pic == "Ww" {
        let txt = match en_words(n) {
            Some(t) => t,
            None => return Ok(py.None()),
        };
        let out = if pic == "W" {
            txt.to_uppercase()
        } else if pic == "Ww" {
            title_ascii(&txt)
        } else {
            txt
        };
        return Ok(str_py(py, &out));
    }
    let (width, mand, grouped) = match dec_picture(&pic, false) {
        Some(t) => t,
        None => return Ok(u.clone().unbind()),
    };
    let mut digits = zfill(&n.unsigned_abs().to_string(), mand);
    if grouped {
        let mut parts: Vec<String> = Vec::new();
        while digits.chars().count() > width {
            let cnt = digits.chars().count();
            let split = cnt - width;
            parts.insert(0, digits.chars().skip(split).collect());
            digits = digits.chars().take(split).collect();
        }
        if !digits.is_empty() {
            parts.insert(0, digits);
        }
        digits = parts.join(",");
    }
    let out = if n < 0 {
        format!("-{}", digits)
    } else {
        digits
    };
    Ok(str_py(py, &out))
}

// ================================================================ $parseInteger

#[pyfunction]
#[pyo3(name = "pgi")]
pub fn o_pgi(
    py: Python<'_>,
    v: &Bound<'_, PyAny>,
    picture: &Bound<'_, PyAny>,
) -> PyResult<Py<PyAny>> {
    let u = undef(py)?;
    let s = match as_str(v) {
        Some(s) => s,
        None => return Ok(u.clone().unbind()),
    };
    let pic = match as_str(picture) {
        Some(s) => s,
        None => return Ok(u.clone().unbind()),
    };
    if pic.is_empty() {
        return Ok(u.clone().unbind());
    }
    let s = match py_strip(&s) {
        Some(s) => s,
        None => return Ok(py.None()),
    };
    match pic.as_str() {
        "A" | "a" => {
            if s.is_empty() {
                return Ok(u.clone().unbind()); // Python: "".isalpha() is False
            }
            if !s.chars().all(|c| c.is_ascii_alphabetic()) {
                // Python 的 `str.isalpha()` 是 Unicode 语义（"中" 也返回 True，
                // 然后按 ord(ch)-64 参与 26 进制），这里不复刻，交给回退。
                if s.chars().all(|c| c.is_alphabetic()) {
                    return Ok(py.None());
                }
                return Ok(u.clone().unbind());
            }
            if s.chars().count() > 13 {
                return Ok(py.None()); // 超出 i64 的窄域
            }
            let mut n: i64 = 0;
            for ch in s.to_uppercase().chars() {
                n = n * 26 + (ch as i64 - 64);
            }
            return Ok(PyInt::new(py, n).into_any().unbind());
        }
        "I" | "i" => {
            if !s.is_ascii() {
                return Ok(py.None());
            }
            return match roman_parse(&s) {
                Some(n) => {
                    if n.abs() > i64::MAX / 2 {
                        Ok(py.None())
                    } else {
                        Ok(PyInt::new(py, n).into_any().unbind())
                    }
                }
                None => Ok(u.clone().unbind()),
            };
        }
        "w" | "W" | "Ww" => {
            if !s.is_ascii() {
                return Ok(py.None());
            }
            let mut total: i64 = 0;
            let mut cur: i64 = 0;
            for tok in s
                .to_lowercase()
                .replace(',', " ")
                .replace('-', " ")
                .split_whitespace()
            {
                if tok == "and" {
                    continue;
                }
                let val = match en_word_value(tok) {
                    Some(v) => v,
                    None => return Ok(u.clone().unbind()),
                };
                if val >= 100 {
                    total += (if cur == 0 { 1 } else { cur }) * val;
                    cur = 0;
                } else {
                    cur += val;
                }
                if total < 0 || total > i64::MAX / 2 {
                    return Ok(py.None());
                }
            }
            return Ok(PyInt::new(py, total + cur).into_any().unbind());
        }
        _ => {}
    }
    let _ = dec_picture(&pic, true);
    // 十进制 picture
    if pic.is_empty() || pic.starts_with(',') || pic.ends_with(',') || pic.contains('.') {
        return Ok(u.clone().unbind());
    }
    let last = match pic.rsplit_once(',') {
        Some((_, l)) => l,
        None => pic.as_str(),
    };
    if !last.contains('0') || !last.chars().all(|c| c == '0' || c == '#') {
        return Ok(u.clone().unbind());
    }
    if !s.is_ascii() {
        // `str.isdigit()` 的 Unicode 语义（全角/阿拉伯数字）与 int() 的宽容解析
        return Ok(py.None());
    }
    let neg = s.starts_with('-');
    let body = if neg { &s[1..] } else { s.as_str() };
    let digits: String = body.chars().filter(|c| c.is_ascii_digit()).collect();
    if digits.is_empty() {
        return Ok(u.clone().unbind());
    }
    if digits.len() > 18 {
        return Ok(py.None()); // 超出 i64 的窄域
    }
    let n: i64 = match digits.parse() {
        Ok(v) => v,
        Err(_) => return Ok(py.None()),
    };
    Ok(PyInt::new(py, if neg { -n } else { n }).into_any().unbind())
}

// ================================================================ $formatNumber

fn fnm_split(picture: &str) -> Vec<String> {
    let cs: Vec<char> = picture.chars().collect();
    let n = cs.len();
    let mut parts: Vec<String> = Vec::new();
    let mut cur = String::new();
    let mut i = 0usize;
    while i < n {
        if cs[i] == '\\' && i + 1 < n {
            cur.push(cs[i]);
            cur.push(cs[i + 1]);
            i += 2;
            continue;
        }
        if cs[i] == ';' {
            parts.push(cur.clone());
            cur.clear();
            i += 1;
            continue;
        }
        cur.push(cs[i]);
        i += 1;
    }
    parts.push(cur);
    parts
}

struct SubPic {
    prefix: String,
    ipat: String,
    fpat: String,
    epat: String,
    suffix: String,
    scale: i128,
}

fn fnm_parse(sub: &str) -> SubPic {
    let cs: Vec<char> = sub.chars().collect();
    let n = cs.len();
    let mut i = 0usize;
    while i < n && !is_fnm_digit(cs[i]) && cs[i] != ',' && cs[i] != '.' {
        i += 1;
    }
    let prefix: String = cs[..i].iter().collect();
    let start = i;
    while i < n && (is_fnm_digit(cs[i]) || cs[i] == ',') {
        i += 1;
    }
    let ipat: String = cs[start..i].iter().collect();
    let mut fpat = String::new();
    if i < n && cs[i] == '.' {
        i += 1;
        let j = i;
        while i < n && is_fnm_digit(cs[i]) {
            i += 1;
        }
        fpat = cs[j..i].iter().collect();
    }
    let mut epat = String::new();
    if i < n && (cs[i] == 'e' || cs[i] == 'E') {
        i += 1;
        let j = i;
        while i < n && is_fnm_digit(cs[i]) {
            i += 1;
        }
        epat = cs[j..i].iter().collect();
    }
    let suffix: String = cs[i..].iter().collect();
    let mut scale: i128 = 1;
    if prefix.contains('%') || suffix.contains('%') {
        scale = 100;
    } else if prefix.contains('\u{2030}') || suffix.contains('\u{2030}') {
        scale = 1000;
    }
    SubPic {
        prefix,
        ipat,
        fpat,
        epat,
        suffix,
        scale,
    }
}

fn is_fnm_digit(c: char) -> bool {
    c == '0' || c == '#'
}

fn fnm_group(digits: &str, ipat: &str) -> String {
    if !ipat.contains(',') {
        return digits.to_string();
    }
    let groups: Vec<&str> = ipat.split(',').collect();
    let last_len = groups
        .last()
        .map(|g| g.chars().count())
        .unwrap_or(0);
    let size = if last_len == 0 { 3 } else { last_len };
    let cs: Vec<char> = digits.chars().collect();
    let mut out: Vec<String> = Vec::new();
    let mut hi = cs.len();
    while hi > size {
        out.insert(0, cs[hi - size..hi].iter().collect());
        hi -= size;
    }
    out.insert(0, cs[..hi].iter().collect());
    out.join(",")
}

/// 十进制字符串按 `max_f` 位小数做 ROUND_HALF_EVEN 舍入。
///
/// 输入是「最短往返表示」的十进制数字串（整数部分 + 小数部分），
/// 这正是 Python 侧 `_DEC(repr(abs(val)))` 拿到的值，所以在这里做
/// HALF_EVEN 与 `Decimal.quantize` 逐位等价。
fn round_dec(ip: &str, fp: &str, max_f: usize) -> Option<(String, String)> {
    let mut all: Vec<u8> = Vec::with_capacity(ip.len() + fp.len() + 1);
    for c in ip.bytes() {
        if !c.is_ascii_digit() {
            return None;
        }
        all.push(c - b'0');
    }
    for c in fp.bytes() {
        if !c.is_ascii_digit() {
            return None;
        }
        all.push(c - b'0');
    }
    if all.is_empty() {
        all.push(0);
    }
    let cut = all.len().saturating_sub(fp.len()) + max_f;
    if all.len() <= cut {
        while all.len() < cut {
            all.push(0);
        }
    } else {
        let first = all[cut];
        let up = if first > 5 {
            true
        } else if first == 5 {
            if all[cut + 1..].iter().any(|d| *d != 0) {
                true
            } else {
                let last = if cut > 0 { all[cut - 1] } else { 0 };
                last % 2 == 1
            }
        } else {
            false
        };
        all.truncate(cut);
        if up {
            let mut i = cut;
            loop {
                if i == 0 {
                    all.insert(0, 1);
                    break;
                }
                i -= 1;
                if all[i] == 9 {
                    all[i] = 0;
                } else {
                    all[i] += 1;
                    break;
                }
            }
        }
    }
    let split = all.len() - max_f;
    let ip_s: String = all[..split].iter().map(|d| (b'0' + d) as char).collect();
    let fp_s: String = all[split..].iter().map(|d| (b'0' + d) as char).collect();
    Some((ip_s, fp_s))
}

fn fnm_plain(ip_in: &str, fp_in: &str, mand_i: usize, ipat: &str, max_f: usize, mand_f: usize) -> Option<String> {
    let (ip0, fp0) = round_dec(ip_in, fp_in, max_f)?;
    let ipz = ip0.trim_start_matches('0');
    // Decimal 上下文精度 28：quantize 后系数位数 > 28 时 Python 抛 InvalidOperation
    if ipz.len() + max_f > 28 {
        return None;
    }
    let mut ip = ipz.to_string();
    if mand_i > 0 && ip.chars().count() < mand_i {
        ip = zfill(&ip, mand_i);
    }
    ip = fnm_group(&ip, ipat);
    let mut fp = fp0;
    if !fp.is_empty() {
        fp = fp.trim_end_matches('0').to_string();
        if fp.chars().count() < mand_f {
            fp = ljust0(&fp, mand_f);
        } else if fp.is_empty() && mand_f == 0 {
            fp = "0".to_string();
        }
    } else if max_f > 0 && mand_f == 0 {
        fp = "0".to_string();
    } else if mand_f > 0 {
        fp = "0".repeat(mand_f);
    }
    if ip.is_empty() && fp.is_empty() {
        ip = "0".to_string();
    }
    Some(if fp.is_empty() {
        ip
    } else {
        format!("{}.{}", ip, fp)
    })
}

enum FnmVal {
    I(i128),
    F(f64),
}

fn fnm_num(v: &FnmVal, sub: &SubPic) -> Option<String> {
    let ipat = &sub.ipat;
    let nd = ipat.chars().filter(|c| is_fnm_digit(*c)).count();
    if nd == 0 && sub.fpat.is_empty() && sub.epat.is_empty() {
        return None;
    }
    let mand_i = ipat.chars().filter(|c| *c == '0').count();
    let max_f = sub.fpat.chars().filter(|c| is_fnm_digit(*c)).count();
    let mand_f = sub.fpat.chars().filter(|c| *c == '0').count();
    if !sub.epat.is_empty() {
        // 指数子图依赖 Decimal.adjusted() + 10.0**exp，不在窄域内
        return None;
    }
    let (ip, fp) = match v {
        FnmVal::I(i) => {
            let x = i.checked_mul(sub.scale)?;
            if x.unsigned_abs() >= 10u128.pow(21) {
                // Python 的 `abs(val) >= 1e21` 分支走 `repr(float(val))`
                return None;
            }
            (x.unsigned_abs().to_string(), String::new())
        }
        FnmVal::F(f) => {
            let scaled = f * (sub.scale as f64);
            let a = scaled.abs();
            if !a.is_finite() || a >= 1.0e21 {
                // Python 走 `repr(float(val))` 分支，指数形态不复刻
                return None;
            }
            // Rust 的 `Display` 与 Python 的 `repr` 都取最短往返十进制表示
            let s = format!("{}", a);
            match s.split_once('.') {
                Some((i, fr)) => (i.to_string(), fr.to_string()),
                None => (s, String::new()),
            }
        }
    };
    fnm_plain(&ip, &fp, mand_i, ipat, max_f, mand_f)
}

#[pyfunction]
#[pyo3(signature = (v, picture, options = None))]
#[pyo3(name = "fnm")]
pub fn o_fnm(
    py: Python<'_>,
    v: &Bound<'_, PyAny>,
    picture: &Bound<'_, PyAny>,
    options: Option<&Bound<'_, PyAny>>,
) -> PyResult<Py<PyAny>> {
    let _ = options; // Python 侧同样忽略 options
    let u = undef(py)?;
    if v.is_exact_instance_of::<PyBool>() {
        return Ok(u.clone().unbind());
    }
    let pic = match as_str(picture) {
        Some(s) => s,
        None => return Ok(u.clone().unbind()),
    };
    let (neg, mag) = match as_num(v) {
        Some(Num::I(i)) => (i < 0, FnmVal::I((i as i128).abs())),
        Some(Num::F(f)) => {
            if !f.is_finite() {
                return Ok(py.None());
            }
            (f < 0.0, FnmVal::F(if f < 0.0 { -f } else { f }))
        }
        None => return Ok(u.clone().unbind()),
    };
    // 非 ASCII 字符只会作为字面量出现在前后缀里（`_fnm_parse` 的分段逻辑
    // 只认 ASCII 的 `0#,.eE`），所以这里不必整条回退；
    // 千分号 `\u2030` 也靠这条路径参与 scale 判定。
    let mut subs = fnm_split(&pic);
    if subs.len() == 1 {
        subs.push(String::new());
    }
    let second: Option<&str> = if subs.len() > 1 && !subs[1].is_empty() {
        Some(subs[1].as_str())
    } else {
        None
    };
    let sub_text: String = if neg && second.is_some() {
        second.unwrap().to_string()
    } else {
        subs[0].clone()
    };
    let parsed = fnm_parse(&sub_text);
    let body = match fnm_num(&mag, &parsed) {
        Some(b) => b,
        None => return Ok(py.None()),
    };
    let sign = if neg && second.is_none() { "-" } else { "" };
    let out = format!(
        "{}{}{}{}",
        parsed.prefix.replace('\\', ""),
        sign,
        body,
        parsed.suffix.replace('\\', "")
    );
    Ok(str_py(py, &out))
}

// ---------------------------------------------------------------- 注册

pub fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(o_eu, m)?)?;
    m.add_function(wrap_pyfunction!(o_du, m)?)?;
    m.add_function(wrap_pyfunction!(o_tms, m)?)?;
    m.add_function(wrap_pyfunction!(o_fms, m)?)?;
    m.add_function(wrap_pyfunction!(o_fnm, m)?)?;
    m.add_function(wrap_pyfunction!(o_pgi, m)?)?;
    Ok(())
}
