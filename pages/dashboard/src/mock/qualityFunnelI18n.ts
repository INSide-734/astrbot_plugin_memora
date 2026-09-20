/**
 * 质量 funnel 面板文案，按 zh/en/ru 独立导出。
 *
 * 键集合必须三语言一致，并与生产字典 `.astrbot-plugin/i18n/*.json` 的
 * `dashboard.qualityFunnel` 子树逐键一致（`production-i18n.test.ts` 强校验）。
 * 计数/标量/比率标签使用 `qualityFunnel.count|value|rate.<stage>.<key>`
 * 动态键，闭集与后端 stage 白名单一致。
 */
const QUALITY_FUNNEL_COPY: Record<string, [string, string, string]> = {
  "qualityFunnel.title": ["质量 funnel", "Quality funnel", "Воронка качества"],
  "qualityFunnel.description": [
    "按 UTC 日聚合候选生成、事实准入、去重与注入四阶段的只读观测（不承诺逐请求关联）。",
    "Read-only UTC-day aggregation of candidate generation, fact admission, dedup, and injection (no per-request correlation).",
    "Только чтение: агрегация по UTC-дням для генерации кандидатов, приёма фактов, дедупликации и внедрения (без связи отдельных запросов).",
  ],
  "qualityFunnel.windowLabel": ["聚合窗口", "Aggregation window", "Окно агрегации"],
  "qualityFunnel.refresh": ["刷新", "Refresh", "Обновить"],
  "qualityFunnel.advisory": [
    "仅供观测参考：计数按 UTC 日聚合，不含阈值判定或自动发布结论。",
    "Observation only: counts are aggregated per UTC day, with no threshold verdict or automatic rollout.",
    "Только наблюдение: счётчики агрегируются по UTC-дням без пороговых решений и авторазвёртывания.",
  ],
  "qualityFunnel.reasonLabel": ["原因码", "Reason code", "Код причины"],
  "qualityFunnel.countsUnavailable": [
    "该阶段当前不可用，计数不返回（不使用零值伪装）。",
    "This stage is unavailable; counts are withheld instead of showing zeros.",
    "Этап недоступен: счётчики не возвращаются вместо ложных нулей.",
  ],
  "qualityFunnel.trendTitle": ["UTC 日趋势", "UTC day trend", "Динамика по UTC-дням"],
  "qualityFunnel.trendEmpty": [
    "当前窗口没有可用的日历聚合。",
    "No calendar aggregation is available for this window.",
    "Для этого окна нет доступной календарной агрегации.",
  ],
  "qualityFunnel.trend.day": ["日期", "Day", "День"],
  "qualityFunnel.trend.candidates": ["候选", "Candidates", "Кандидаты"],
  "qualityFunnel.trend.canonical": ["新增 canonical", "New canonical", "Новые canonical"],
  "qualityFunnel.trend.merged": ["并入既有", "Merged", "Объединено"],
  "qualityFunnel.trend.facts_rejected": ["事实拒绝", "Facts rejected", "Отклонённые факты"],
  "qualityFunnel.trend.dedup_checked": ["去重检查", "Dedup checked", "Проверки дублей"],
  "qualityFunnel.trend.dedup_hit": ["去重命中", "Dedup hits", "Совпадения дублей"],
  "qualityFunnel.trend.decisions": ["注入决策", "Injection decisions", "Решения о внедрении"],
  "qualityFunnel.trend.selected": ["注入选中", "Selected", "Выбрано"],
  "qualityFunnel.window.1h": ["最近 1 小时", "Last hour", "Последний час"],
  "qualityFunnel.window.24h": ["最近 24 小时", "Last 24 hours", "Последние 24 часа"],
  "qualityFunnel.window.7d": ["最近 7 天", "Last 7 days", "Последние 7 дней"],
  "qualityFunnel.window.30d": ["最近 30 天", "Last 30 days", "Последние 30 дней"],
  "qualityFunnel.stage.candidates": ["候选生成", "Candidates", "Генерация кандидатов"],
  "qualityFunnel.stage.facts": ["事实准入", "Fact admission", "Приём фактов"],
  "qualityFunnel.stage.dedup": ["跨窗口去重", "Cross-window dedup", "Дедупликация"],
  "qualityFunnel.stage.injection": ["注入结果", "Injection outcome", "Результат внедрения"],
  "qualityFunnel.state.available": ["可用", "Available", "Доступно"],
  "qualityFunnel.state.degraded": ["降级", "Degraded", "Понижено"],
  "qualityFunnel.state.unavailable": ["不可用", "Unavailable", "Недоступно"],
  "qualityFunnel.state.loading": ["正在加载质量 funnel", "Loading quality funnel", "Загрузка воронки качества"],
  "qualityFunnel.state.empty": ["当前窗口没有 funnel 数据", "No funnel data in this window", "В этом окне нет данных воронки"],
  "qualityFunnel.state.emptyHint": [
    "插件尚未产生可聚合的窗口终态，请稍后刷新。",
    "No terminal windows are available to aggregate yet; refresh later.",
    "Пока нет завершённых окон для агрегации; обновите позже.",
  ],
  "qualityFunnel.state.error": ["无法加载质量 funnel", "Could not load quality funnel", "Не удалось загрузить воронку качества"],
  "qualityFunnel.count.candidates.candidates": ["候选数", "Candidates", "Кандидаты"],
  "qualityFunnel.count.candidates.exact_reuse": ["精确复用", "Exact reuse", "Точное переиспользование"],
  "qualityFunnel.count.candidates.identity_drops": ["身份丢弃", "Identity drops", "Отброшено по личности"],
  "qualityFunnel.count.candidates.budget_exceeded": ["预算超限", "Budget exceeded", "Превышен бюджет"],
  "qualityFunnel.count.candidates.catalog_degraded": ["目录降级", "Catalog degraded", "Каталог понижен"],
  "qualityFunnel.count.facts.canonical": ["新增 canonical", "New canonical", "Новые canonical"],
  "qualityFunnel.count.facts.merged": ["并入既有", "Merged into owner", "Объединено с владельцем"],
  "qualityFunnel.count.facts.quarantined": ["隔离", "Quarantined", "Карантин"],
  "qualityFunnel.count.facts.discarded": ["丢弃", "Discarded", "Отброшено"],
  "qualityFunnel.count.facts.facts_rejected": ["拒绝事实", "Rejected facts", "Отклонённые факты"],
  "qualityFunnel.count.dedup.checked": ["检查数", "Checked", "Проверено"],
  "qualityFunnel.count.dedup.hit": ["命中数", "Hits", "Совпадения"],
  "qualityFunnel.count.dedup.merged": ["合并数", "Merged", "Объединено"],
  "qualityFunnel.count.dedup.fact_mismatch": ["事实冲突", "Fact mismatch", "Конфликт фактов"],
  "qualityFunnel.count.dedup.fact_overlap": ["事实重叠", "Fact overlap", "Перекрытие фактов"],
  "qualityFunnel.count.injection.decisions": ["决策数", "Decisions", "Решения"],
  "qualityFunnel.count.injection.selected": ["选中条目", "Selected items", "Выбранные элементы"],
  "qualityFunnel.count.injection.dropped": ["丢弃条目", "Dropped items", "Отброшенные элементы"],
  "qualityFunnel.count.injection.memory_present": ["有记忆候选", "Memory present", "Есть кандидаты памяти"],
  "qualityFunnel.count.injection.payload_injected": ["载荷已注入", "Payload injected", "Полезная нагрузка внедрена"],
  "qualityFunnel.value.injection.budget_utilization": ["预算利用率", "Budget utilization", "Использование бюджета"],
  "qualityFunnel.rate.candidates.reuse_rate": ["复用率", "Reuse rate", "Доля переиспользования"],
  "qualityFunnel.rate.candidates.degraded_rate": ["目录降级率", "Catalog degraded rate", "Доля понижений каталога"],
  "qualityFunnel.rate.facts.merge_rate": ["合并率", "Merge rate", "Доля объединений"],
  "qualityFunnel.rate.facts.discard_rate": ["丢弃率", "Discard rate", "Доля отбросов"],
  "qualityFunnel.rate.dedup.hit_rate": ["命中率", "Hit rate", "Доля совпадений"],
  "qualityFunnel.rate.dedup.guard_rate": ["护栏率", "Guard rate", "Доля защиты"],
  "qualityFunnel.rate.dedup.overlap_rate": ["重叠率", "Overlap rate", "Доля перекрытий"],
  "qualityFunnel.rate.dedup.failure_rate": ["失败率", "Failure rate", "Доля ошибок"],
  "qualityFunnel.rate.injection.memory_present_rate": ["候选存在率", "Memory present rate", "Доля наличия кандидатов"],
  "qualityFunnel.rate.injection.payload_injected_rate": ["载荷注入率", "Payload injected rate", "Доля внедрения нагрузки"],
};

/** 按语言位置生成质量 funnel 文案。 */
function qualityFunnelLocaleCopy(index: 0 | 1 | 2): Record<string, string> {
  return Object.fromEntries(
    Object.entries(QUALITY_FUNNEL_COPY).map(([key, values]) => [key, values[index]])
  );
}

export const QUALITY_FUNNEL_ZH_MAP = qualityFunnelLocaleCopy(0);
export const QUALITY_FUNNEL_EN_MAP = qualityFunnelLocaleCopy(1);
export const QUALITY_FUNNEL_RU_MAP = qualityFunnelLocaleCopy(2);
