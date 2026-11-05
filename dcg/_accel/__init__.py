"""可选 Rust 加速后端（`dcg._accel._accel`）的宿主包。

Linux 与 Windows 的产物用标准名字（`_accel.abi3.so` / `_accel.pyd`），直接导入即可命中。
macOS 的产物只能由 CI 构建，且同一目录里放不下两个都叫 `_accel.abi3.so` 的 Mach-O，
所以文件名带架构后缀，由下面的 `_load()` 按机器架构手动加载。
"""

import importlib.machinery
import importlib.util
import os
import platform
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))


def _load(filename):
    """按文件路径加载扩展模块；文件不存在或架构不匹配时返回 None。"""
    path = os.path.join(_HERE, filename)
    if not os.path.isfile(path):
        return None
    name = __name__ + "._accel"
    loader = importlib.machinery.ExtensionFileLoader(name, path)
    spec = importlib.util.spec_from_file_location(name, path, loader=loader)
    module = importlib.util.module_from_spec(spec)
    try:
        loader.exec_module(module)
    except ImportError:
        return None
    return module


if sys.platform == "darwin":
    _accel = _load("_accel-macos-arm64.abi3.so" if platform.machine() == "arm64"
                   else "_accel-macos-x64.abi3.so")
else:
    from . import _accel  # noqa: F401  (标准导入：Linux → _accel.abi3.so，Windows → _accel.pyd)
