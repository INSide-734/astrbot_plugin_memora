"""备份应用服务的旧路径兼容导出。"""

from ..features.backup.application import manager as _feature_manager

BackupManager = _feature_manager.BackupManager
PLUGIN_VERSION = _feature_manager.PLUGIN_VERSION
_VERSION_FILE = _feature_manager._VERSION_FILE
_BACKUP_INFO_FILE = _feature_manager._BACKUP_INFO_FILE
_BACKUP_NAME_RE = _feature_manager._BACKUP_NAME_RE
_BACKUP_FILE_SPECS = _feature_manager._BACKUP_FILE_SPECS
_BACKUP_PATTERNS = _feature_manager._BACKUP_PATTERNS

__all__ = ["BackupManager", "PLUGIN_VERSION"]
