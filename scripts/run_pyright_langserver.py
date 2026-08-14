"""从 uv 虚拟环境启动 Pyright 语言服务器。"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import NoReturn

import pyright


def _resolve_langserver_entrypoint() -> Path:
    """定位当前虚拟环境内 Pyright 自带的语言服务器入口。

    Returns:
        可由 Node 执行的语言服务器入口路径。

    Raises:
        RuntimeError: Pyright 包或其语言服务器入口无法定位。
    """
    package_file = pyright.__file__
    if package_file is None:
        raise RuntimeError("无法定位当前虚拟环境中的 Pyright 包")

    entrypoint = Path(package_file).resolve().parent / "dist" / "langserver.index.js"
    if not entrypoint.is_file():
        raise RuntimeError("当前虚拟环境中的 Pyright 缺少语言服务器入口")
    return entrypoint


def main() -> NoReturn:
    """使用原始 LSP 参数将当前进程替换为 Pyright 语言服务器。

    成功时当前进程会被 Node 替换，因此本函数不会返回。

    Raises:
        OSError: Node 进程无法启动。
        RuntimeError: Pyright 语言服务器入口无法定位。
    """
    entrypoint = _resolve_langserver_entrypoint()
    os.execvp("node", ["node", str(entrypoint), *sys.argv[1:]])


if __name__ == "__main__":
    main()
