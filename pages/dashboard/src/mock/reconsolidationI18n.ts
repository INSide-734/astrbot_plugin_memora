const RECONSOLIDATION_COPY: Record<string, [string, string, string]> = {
  "intelligence.reconsolidation.title": ["再巩固候选", "Reconsolidation candidates", "Кандидаты реконсолидирования"],
  "intelligence.reconsolidation.subtitle": ["人工审查高影响记忆修订，并保留可回滚的动作历史。", "Review high-impact memory revisions with a rollback-aware action history.", "Проверяйте значимые изменения памяти с историей действий и возможностью отката."],
  "intelligence.reconsolidation.statusLabel": ["候选状态", "Candidate status", "Статус кандидата"],
  "intelligence.reconsolidation.allStatuses": ["全部状态", "All statuses", "Все статусы"],
  "intelligence.reconsolidation.loading": ["正在加载候选", "Loading candidates", "Загрузка кандидатов"],
  "intelligence.reconsolidation.loadingDetail": ["正在加载候选详情", "Loading candidate details", "Загрузка сведений о кандидате"],
  "intelligence.reconsolidation.noMatches": ["暂无再巩固候选", "No reconsolidation candidates", "Нет кандидатов для реконсолидирования"],
  "intelligence.reconsolidation.disabled": ["记忆再巩固功能未启用", "Reconsolidation is not enabled", "Реконсолидация памяти не включена"],
  "intelligence.reconsolidation.selectCandidate": ["选择候选以查看正文对照。", "Select a candidate to compare its content.", "Выберите кандидата для сравнения текста."],
  "intelligence.reconsolidation.items": ["候选列表", "Candidate list", "Список кандидатов"],
  "intelligence.reconsolidation.detail": ["候选详情", "Candidate details", "Сведения о кандидате"],
  "intelligence.reconsolidation.detailMismatch": ["候选详情与当前选择不匹配", "Candidate detail does not match the current selection", "Сведения о кандидате не соответствуют текущему выбору"],
  "intelligence.reconsolidation.oldContent": ["旧正文", "Original content", "Исходный текст"],
  "intelligence.reconsolidation.proposedContent": ["拟议正文", "Proposed content", "Предлагаемый текст"],
  "intelligence.reconsolidation.evidenceLabel": ["证据类型", "Evidence type", "Тип доказательства"],
  "intelligence.reconsolidation.reasonLabel": ["原因码", "Reason code", "Код причины"],
  "intelligence.reconsolidation.actions": ["人工动作", "Review actions", "Действия ревью"],
  "intelligence.reconsolidation.approve": ["批准", "Approve", "Одобрить"],
  "intelligence.reconsolidation.reject": ["拒绝", "Reject", "Отклонить"],
  "intelligence.reconsolidation.rollback": ["回滚", "Rollback", "Откатить"],
  "intelligence.reconsolidation.actionHistory": ["动作历史", "Action history", "История действий"],
  "intelligence.reconsolidation.noActions": ["暂无动作历史", "No action history", "История действий пуста"],
  "intelligence.reconsolidation.confirmTitle": ["确认{0}", "Confirm {0}", "Подтвердить: {0}"],
  "intelligence.reconsolidation.confirmDescription": ["此操作将更新候选 {0} 的复核状态，并可能修改 canonical 记忆。", "This changes candidate {0} and may update canonical memory.", "Действие изменит кандидата {0} и может обновить каноническую память."],
  "intelligence.reconsolidation.confirm": ["确认操作", "Confirm action", "Подтвердить действие"],
  "intelligence.reconsolidation.submitting": ["正在提交…", "Submitting…", "Отправка…"],
  "intelligence.reconsolidation.toastActionSubmitted": ["已提交再巩固操作：{0}", "Reconsolidation action submitted: {0}", "Действие реконсолидирования отправлено: {0}"],
  "intelligence.reconsolidation.status.pending": ["待复核", "Pending", "Ожидает ревью"],
  "intelligence.reconsolidation.status.approved": ["已批准", "Approved", "Одобрено"],
  "intelligence.reconsolidation.status.rejected": ["已拒绝", "Rejected", "Отклонено"],
  "intelligence.reconsolidation.status.failed": ["失败", "Failed", "Ошибка"],
  "intelligence.reconsolidation.status.rolled_back": ["已回滚", "Rolled back", "Откачено"],
  "intelligence.reconsolidation.action.approve": ["批准", "Approve", "Одобрить"],
  "intelligence.reconsolidation.action.reject": ["拒绝", "Reject", "Отклонить"],
  "intelligence.reconsolidation.action.rollback": ["回滚", "Rollback", "Откатить"],
  "intelligence.reconsolidation.action.stage": ["创建候选", "Stage", "Создать кандидата"],
  "intelligence.reconsolidation.action.apply": ["应用候选", "Apply", "Применить"],
  "intelligence.reconsolidation.evidence.llm_revision": ["模型修订证据", "Model revision evidence", "Доказательство пересмотра моделью"],
  "intelligence.reconsolidation.reason.proposed": ["已提出修订", "Revision proposed", "Предложено изменение"],
  "intelligence.reconsolidation.reason.applied": ["修订已应用", "Revision applied", "Изменение применено"],
  "intelligence.reconsolidation.reason.manual_reject": ["人工拒绝", "Manually rejected", "Отклонено вручную"],
  "intelligence.reconsolidation.reason.rolled_back": ["已恢复旧版本", "Previous revision restored", "Предыдущая версия восстановлена"],
  "intelligence.reconsolidation.reason.source_revision_mismatch": ["来源版本已变化", "Source revision changed", "Версия источника изменилась"],
  "intelligence.reconsolidation.reason.candidate_changed": ["候选已变化", "Candidate changed", "Кандидат изменился"],
};

/** 按语言位置生成再巩固复核文案。 */
function reconsolidationLocaleCopy(index: 0 | 1 | 2): Record<string, string> {
  return Object.fromEntries(
    Object.entries(RECONSOLIDATION_COPY).map(([key, values]) => [key, values[index]]),
  );
}

export const RECONSOLIDATION_ZH_MAP = reconsolidationLocaleCopy(0);
export const RECONSOLIDATION_EN_MAP = reconsolidationLocaleCopy(1);
export const RECONSOLIDATION_RU_MAP = reconsolidationLocaleCopy(2);
