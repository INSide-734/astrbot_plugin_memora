"""记忆再巩固候选的领域异常。"""


class ReconsolidationCandidateNotFoundError(LookupError):
    """目标再巩固候选不存在。"""


class ReconsolidationCandidateConflictError(RuntimeError):
    """候选状态或 revision 已变化，调用方必须重新读取。"""


__all__ = [
    "ReconsolidationCandidateConflictError",
    "ReconsolidationCandidateNotFoundError",
]
