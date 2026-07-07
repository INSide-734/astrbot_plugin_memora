"""FAISS 运行时兼容性检查"""

import os
import subprocess
import sys
import time
from typing import Any

from astrbot.api import logger

FaissVecDB: Any = None


class FaissChecker:
    """FAISS 运行时检查 + 动态加载 + 维度不匹配修复"""

    @staticmethod
    def check_runtime():
        try:
            # 安全 — sys.executable 是受信任的 Python 解释器路径;
            # 参数为固定列表，无用户可控输入。
            result = subprocess.run(
                [sys.executable, "-c", "import faiss"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            from ..base.exceptions import InitializationError

            raise InitializationError(
                "FAISS 运行时检查失败，无法安全初始化向量数据库。"
                "请确认 faiss-cpu 已正确安装，或改用兼容当前 CPU 的 FAISS 包。"
            ) from exc

        if result.returncode != 0:
            from ..base.exceptions import InitializationError

            details = (result.stderr or result.stdout or "").strip()
            if result.returncode < 0:
                details = f"进程被信号 {-result.returncode} 终止。{details}".strip()
            raise InitializationError(
                "FAISS 初始化失败，当前 CPU 或运行环境可能不兼容 faiss-cpu。"
                "无 AVX2 的 CPU 上可能触发 Illegal instruction；"
                "请使用支持 AVX2 的 CPU、安装兼容版本 FAISS，或更换运行环境。"
                f"{' 原始错误: ' + details if details else ''}"
            )

    def load_vec_db_class(self):
        global FaissVecDB
        if FaissVecDB is not None:
            return FaissVecDB

        self.check_runtime()
        try:
            from astrbot.core.db.vec_db.faiss_impl.vec_db import (
                FaissVecDB as LoadedFaissVecDB,
            )
        except (ImportError, ModuleNotFoundError, SystemError, OSError) as exc:
            from ..base.exceptions import InitializationError

            raise InitializationError(
                "FAISS 初始化失败，无法加载 AstrBot FaissVecDB。"
                "请检查 faiss-cpu 安装状态和 CPU 指令集兼容性。"
            ) from exc

        FaissVecDB = LoadedFaissVecDB
        return LoadedFaissVecDB

    @staticmethod
    async def check_and_fix_dimension_mismatch(
            index_path: str, embedding_provider
    ) -> None:
        if not os.path.exists(index_path):
            return

        try:
            try:
                import faiss
            except (ImportError, ModuleNotFoundError, SystemError, OSError) as exc:
                from ..base.exceptions import InitializationError

                raise InitializationError(
                    "FAISS 初始化失败，无法读取索引文件。"
                    "请检查 faiss-cpu 安装状态和 CPU 指令集兼容性。"
                ) from exc

            old_index = faiss.read_index(index_path)
            old_dim = old_index.d
            new_dim = embedding_provider.get_dim()

            if old_dim != new_dim:
                logger.warning(
                    f"检测到 FAISS 索引维度不匹配: 索引维度={old_dim}, "
                    f"当前 Embedding Provider 维度={new_dim}"
                )
                logger.warning(
                    "这通常由 Embedding 模型切换导致。旧索引将被删除，系统会自动重建索引。"
                )
                os.remove(index_path)
                logger.info(f"已删除不兼容的旧索引文件: {index_path}")

        except Exception as e:
            from ..base.exceptions import InitializationError

            if isinstance(e, InitializationError):
                raise
            quarantine_path = f"{index_path}.corrupt_{int(time.time())}"
            try:
                os.replace(index_path, quarantine_path)
                logger.error(
                    f"FAISS 索引文件不可读，已隔离坏文件: {quarantine_path}。"
                    "系统将创建空索引，并在初始化后尝试分批重建。",
                    exc_info=True,
                )
            except Exception:
                logger.error(
                    f"检查索引维度时出错，且隔离坏索引失败: {e}", exc_info=True
                )
