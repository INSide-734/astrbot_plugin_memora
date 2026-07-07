"""备份 API"""

from collections.abc import Mapping

from ..managers.backup_manager import BackupManager


def _safe_backup_list(backups):
    if isinstance(backups, list):
        return backups
    if backups is None or isinstance(backups, (str, bytes, bytearray, Mapping)):
        return []
    try:
        return list(backups)
    except TypeError:
        return []


class BackupApiMixin:
    """混入类：备份列表"""

    async def list_backups(self):
        initializer = getattr(self.plugin, "initializer", None)
        data_dir = getattr(initializer, "data_dir", "") if initializer else ""
        if not data_dir:
            return self._ok({"backups": [], "total": 0})
        backups = BackupManager.list_backups(data_dir)
        backups = _safe_backup_list(backups)
        return self._ok({"backups": backups, "total": len(backups)})
