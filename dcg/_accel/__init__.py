"""可选 Rust 加速后端（`dcg._accel._accel`）的宿主包。

`dcg/runtime.py` 用 ``from ._accel import _accel`` 尝试加载本目录下的编译产物：

* 找得到且 ABI 匹配 → helper 内的门控对**可证明逐值等价**的子集走 Rust；
* 找不到（没编译、平台不支持、Python 版本不匹配）→ ``_ACC`` 为 ``None``，
  门控只剩一次 ``is not None`` 判断，行为与纯 Python 完全一致。

本包不含任何 Python 逻辑，`__init__` 只是让 ``dcg._accel`` 成为一个可打包的
子包（``pyproject.toml`` 的 ``packages`` 白名单里必须列它，否则 wheel 里没有
二进制，双活退化成单活）。
"""
