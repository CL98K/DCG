"""构建 `dcg/_accel` 的可选 Rust 加速后端，并把产物放进包目录。

产物名（按平台，**由 `artifact_name()` 唯一决定**，加载器按同一张表选文件）：
  * Windows  `dcg/_accel/_accel.pyd`
  * Linux    `dcg/_accel/_accel.abi3.so`
  * macOS    `dcg/_accel/_accel-macos-arm64.abi3.so`
             `dcg/_accel/_accel-macos-x64.abi3.so`

macOS 必须带架构后缀：一个目录放不下两个都叫 `_accel.abi3.so` 的 Mach-O，而
arm64 与 x64 两个平台的产物都要随 wheel 分发。以前这里统一产出 `_accel.abi3.so`，
加载器却只认带后缀的名字 —— 在 macOS 上重建后 `copy2` 不删旧文件，运行期就会
静默加载**上一次**的二进制（CI 验新的、用户跑旧的）。现在两侧共用 `artifact_name()`。

`abi3-py38` 让**一个**二进制覆盖 CPython 3.8–3.15，所以不需要按版本分目录。
产物被 `.gitignore` 忽略（`*.pyd`/`*.so` 全局忽略 + `dcg/_accel/` 下的反排除
只放行已编译文件），随 wheel 分发靠 `pyproject.toml` 的 package-data。

用法：
    python build_accel.py            # 用 PATH 上的 cargo
    python build_accel.py --proxy    # 走本机回环代理（Windows schannel 不可用时）

不安装任何东西到系统：默认只找 `_tmp/rust_poc/cargo`（仓库内隔离工具链），
其次 PATH。

`--proxy` 在本文件内实现：Python urllib/OpenSSL 取官方 crates.io 稀疏索引与
static.crates.io 归档，Cargo 仍按索引里的校验和验证，所以它只是本机
Windows schannel 不可用时的开发期绕行手段，不是构建依赖。
"""

import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
from contextlib import contextmanager, nullcontext
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from urllib.error import HTTPError
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parent
PROJECT = ROOT / "dcg" / "_accel"
SANDBOX = ROOT / "_tmp" / "rust_poc"

#: 架构别名 → 产物名里的架构段。x86_64/AMD64 在 `platform.machine()` 上有多种写法。
_ARCH_ALIASES = {"arm64": "arm64", "aarch64": "arm64",
                 "x86_64": "x64", "amd64": "x64", "x64": "x64"}


def artifact_name(platform_name=None, machine=None):
    """该平台的产物文件名。

    加载器 `dcg/_accel/__init__.py` 按同一张表选文件，`tests/test_accel_gate.py`
    断言两侧一致 —— 以前这里产出 `_accel.abi3.so` 而加载器只找带后缀的名字，
    macOS 上重建后运行期会静默加载旧二进制。
    """
    platform_name = sys.platform if platform_name is None else platform_name
    if platform_name == "win32":
        return "_accel.pyd"
    if platform_name == "darwin":
        machine = platform.machine() if machine is None else machine
        arch = _ARCH_ALIASES.get(str(machine).lower())
        if arch is None:
            raise RuntimeError(
                "不认识的 macOS 架构 %r：产物名由架构决定，"
                "请补进 _ARCH_ALIASES（已知 %s）"
                % (machine, sorted(_ARCH_ALIASES)))
        return "_accel-macos-{}.abi3.so".format(arch)
    return "_accel.abi3.so"


def _mingw_bin(environment):
    """MinGW 的 bin 目录：`DCG_MINGW_BIN` 优先，其次按 `gcc` 反查，都没有返回 None。

    早先这里硬编码了作者本机的 `D:/LenovoSoftstore/MinGW64/bin`：别的机器上只是
    一个不存在的目录（静默跳过），发版脚本却把一个人的开发环境当成了默认值。
    """
    configured = environment.get("DCG_MINGW_BIN")
    if configured:
        path = Path(configured)
        return path if path.is_dir() else None
    if os.name != "nt":
        return None
    found = shutil.which("gcc", path=environment.get("PATH"))
    return Path(found).parent if found else None


@contextmanager
def local_registry():
    """起一个只监听回环的 cargo registry 中继，返回基地址。"""
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/config.json":
                body = json.dumps({
                    "dl": "http://127.0.0.1:{}/crate/{{crate}}/{{version}}/download".format(
                        self.server.server_port),
                    "api": "https://crates.io",
                }).encode("ascii")
                status = 200
            elif re.fullmatch(r"/crate/[A-Za-z0-9_-]+/[A-Za-z0-9.+_-]+/download", self.path):
                _, _, crate, version, _ = self.path.split("/")
                status, body = self._fetch("https://static.crates.io/crates/{}/{}-{}.crate"
                                           .format(crate, crate, version))
            elif re.fullmatch(r"/[A-Za-z0-9_/-]+", self.path) and ".." not in self.path:
                status, body = self._fetch("https://index.crates.io" + self.path)
            else:
                status, body = 400, b"unsupported path"
            self.send_response(status)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

        @staticmethod
        def _fetch(url):
            try:
                with urlopen(url, timeout=90) as response:
                    return response.status, response.read()
            except HTTPError as exc:
                return exc.code, exc.read()
            except Exception as exc:  # noqa: BLE001 - 中继层错误直接回给 cargo
                return 502, str(exc).encode("utf-8")

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield "http://127.0.0.1:{}/".format(server.server_port)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def _cargo():
    """返回 cargo 可执行文件路径；隔离工具链优先，其次 PATH。"""
    environment = os.environ.copy()
    isolated_bin = SANDBOX / "cargo" / "bin"
    name = "cargo.exe" if os.name == "nt" else "cargo"
    local = isolated_bin / name
    if local.is_file() and "CARGO_HOME" not in environment:
        environment["CARGO_HOME"] = str(SANDBOX / "cargo")
        environment.setdefault("RUSTUP_HOME", str(SANDBOX / "rustup"))
    home = Path(environment["CARGO_HOME"]) if "CARGO_HOME" in environment else None
    configured = (home / "bin" / name) if home else None
    found = next((str(p) for p in (configured, local)
                  if p is not None and p.is_file()), None) or shutil.which("cargo")
    if not found:
        raise RuntimeError("找不到 cargo：请安装 Rust 工具链，或设置 CARGO_HOME")
    return found, environment


def main():
    cargo, environment = _cargo()
    environment.setdefault("CARGO_TARGET_DIR", str(SANDBOX / "target_accel"))
    # 链接配置全部在 Rust 侧：`extension-module` feature（别链接 libpython）写在
    # Cargo.toml，macOS 的 `-undefined dynamic_lookup`（把未定义符号留到 import 时
    # 解析）由 dcg/_accel/build.rs 调 pyo3_build_config::add_extension_module_link_args()。
    # pyo3 不会替我们加后者，maturin/setuptools-rust 才会自动加。
    environment["PYO3_PYTHON"] = sys.executable
    environment["PATH"] = str(Path(cargo).parent) + os.pathsep + environment.get("PATH", "")
    mingw = _mingw_bin(environment)
    if mingw is not None:
        environment["PATH"] = str(mingw) + os.pathsep + environment["PATH"]
    else:
        print("未找到 MinGW（DCG_MINGW_BIN / PATH 上的 gcc 都没有），按系统默认链接器构建")

    proxy = nullcontext(None)
    if "--proxy" in sys.argv:
        proxy = local_registry()

    with proxy as base:
        cmd = [cargo]
        if base:
            cmd += ["--config", 'source.crates-io.replace-with="poc-loopback"',
                    "--config", 'source.poc-loopback.registry="sparse+' + base + '"']
        cmd += ["build", "--release", "--manifest-path", str(PROJECT / "Cargo.toml")]
        subprocess.run(cmd, check=True, env=environment)

    release = Path(environment["CARGO_TARGET_DIR"]) / "release"
    if sys.platform == "win32":
        source = release / "_accel.dll"
    elif sys.platform == "darwin":
        source = release / "lib_accel.dylib"
    else:
        source = release / "lib_accel.so"
    name = artifact_name()
    if not source.is_file():
        raise RuntimeError(f"构建产物不存在：{source}")
    destination = PROJECT / name
    shutil.copy2(source, destination)
    print(f"Built {destination} ({destination.stat().st_size} bytes) for "
          f"CPython {sys.version.split()[0]}")

    # 旧版本在 macOS 上产出 `_accel.abi3.so`，与加载器期望的名字不符；它留在目录里
    # 不会把构建结果盖掉（copy2 只写目标），但会让人误以为产物就在那个名字上。
    legacy = PROJECT / "_accel.abi3.so"
    if sys.platform == "darwin" and legacy.is_file():
        print(f"提示：{legacy} 是 1.3 之前版本的产物名，加载器不认它；"
              f"确认无用后可删除，避免误判当前构建结果")


if __name__ == "__main__":
    main()
