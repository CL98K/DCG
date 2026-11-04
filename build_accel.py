"""构建 `dcg/_accel` 的可选 Rust 加速后端，并把产物放进包目录。

产物名（按平台）：
  * Windows  `dcg/_accel/_accel.pyd`
  * Linux    `dcg/_accel/_accel.abi3.so`
  * macOS    `dcg/_accel/_accel.abi3.so`

`abi3-py38` 让**一个**二进制覆盖 CPython 3.8–3.13，所以不需要按版本分目录。
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
    mingw = Path("D:/LenovoSoftstore/MinGW64/bin")
    if os.name == "nt" and mingw.is_dir():
        environment["PATH"] = str(mingw) + os.pathsep + environment["PATH"]

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
        source, name = release / "_accel.dll", "_accel.pyd"
    elif sys.platform == "darwin":
        source, name = release / "lib_accel.dylib", "_accel.abi3.so"
    else:
        source, name = release / "lib_accel.so", "_accel.abi3.so"
    if not source.is_file():
        raise RuntimeError(f"构建产物不存在：{source}")
    destination = PROJECT / name
    shutil.copy2(source, destination)
    print(f"Built {destination} ({destination.stat().st_size} bytes) for "
          f"CPython {sys.version.split()[0]}")


if __name__ == "__main__":
    main()
