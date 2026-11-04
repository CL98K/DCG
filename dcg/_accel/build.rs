//! macOS 上手工构建 cdylib 扩展模块必须补的链接参数。
//!
//! `extension-module` 只是不去链接 libpython；`_Py_*` 等符号仍然无处解析，
//! macOS 的 `ld` 会当场判它们未定义。这里补 `-undefined dynamic_lookup`，
//! 把符号留到 import 时由宿主解释器解析。Linux / Windows 上该调用是空操作。

fn main() {
    pyo3_build_config::add_extension_module_link_args();
}