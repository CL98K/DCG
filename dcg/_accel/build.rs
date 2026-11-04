//! macOS 上手工构建 cdylib 扩展模块必须补的链接参数。
//!
//! pyo3 的 `extension-module` 只做一件事：**不去链接** libpython（abi3 下本来也
//! 没有能匹配的那份库）。可链接器还剩下另一半个问题——`_Py_*` / `__Py_NoneStruct`
//! 这些符号此时既没有定义、也没有被 libpython 提供，`ld` 会当场判它们未定义：
//!
//! ```text
//! ld: symbol(s) not found for architecture arm64
//!   "_Py_IncRef", referenced from: ...
//! ```
//!
//! 扩展模块的正确语义是「符号留到 import 时由宿主解释器解析」，对应 macOS 的
//! `-undefined dynamic_lookup`。这一条 pyo3 **不会**替你加——它只提供函数，
//! 由项目自己的 build script 调用（见 pyo3 guide 的 building-and-distribution
//! “macOS” 一节：`pyo3_build_config::add_extension_module_link_args()`）。
//! maturin / setuptools-rust 会自动加，`build_accel.py` 这种手工 `cargo build`
//! 不会，所以必须在这里显式调用一次。
//!
//! Linux 与 Windows 上该函数是空操作（Linux 的 `.so` 允许未定义符号拖到运行期，
//! Windows 走 raw-dylib / import library），因此可以无条件调用。

fn main() {
    pyo3_build_config::add_extension_module_link_args();
}