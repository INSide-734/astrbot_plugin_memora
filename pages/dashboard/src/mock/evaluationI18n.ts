const EVALUATION_COPY: Record<string, [string, string, string]> = {
  "intelligence.evaluation.importDataset": ["导入数据集", "Import dataset", "Импортировать набор"],
  "intelligence.evaluation.importingDataset": ["正在导入", "Importing", "Импорт"],
  "intelligence.evaluation.noDatasets": ["暂无测评数据集", "No evaluation datasets", "Нет наборов данных для оценки"],
  "intelligence.evaluation.datasetImported": ["数据集 {0} 已导入", "Dataset {0} imported", "Набор {0} импортирован"],
  "intelligence.evaluation.currentMemories": ["当前记忆", "Current memories", "Текущие воспоминания"],
  "intelligence.evaluation.currentMemoriesDescription": ["使用此安装中的临时记忆样本", "Uses a temporary sample from this installation", "Используется временная выборка из этой установки"],
  "intelligence.evaluation.intent.self_retrieval": ["自身召回", "Self retrieval", "Самопоиск"],
  "intelligence.evaluation.variant.baseline": ["基线", "Baseline", "Базовый"],
  "intelligence.evaluation.variant.a": ["演化关闭", "Evolution disabled", "Эволюция отключена"],
  "intelligence.evaluation.variant.b": ["演化只读", "Evolution readonly", "Эволюция только для чтения"],
  "intelligence.evaluation.variant.c": ["演化激活", "Evolution active", "Эволюция активна"],
  "intelligence.evaluation.variant.graph_expansion_off": ["关闭图扩展", "Graph off", "Без графа"],
  "intelligence.evaluation.variant.topic_expansion_off": ["关闭话题扩展", "Topic off", "Без тем"],
  "intelligence.evaluation.variant.final_reranker_off": ["关闭最终重排", "Final reranker off", "Без финального переранжирования"],
  "intelligence.evaluation.variant.final_reranker_mmr": ["MMR 最终重排", "MMR final reranker", "Финальное MMR-переранжирование"],
  "intelligence.evaluation.variant.final_reranker_embedding_similarity": ["嵌入相似度最终重排", "Embedding similarity reranker", "Переранжирование по сходству эмбеддингов"],
  "intelligence.evaluation.variant.graph_neighbors_off": ["关闭图邻居", "Graph neighbors off", "Без соседей графа"],
  "intelligence.evaluation.variant.graph_neighbors_1_hop": ["一跳图邻居", "Graph neighbors: 1 hop", "Соседи графа: 1 переход"],
  "intelligence.evaluation.variant.graph_neighbors_2_hops": ["两跳图邻居", "Graph neighbors: 2 hops", "Соседи графа: 2 перехода"],
  "intelligence.evaluation.reason.equivalent_to_baseline": ["与当前基线等价", "Equivalent to current baseline", "Эквивалентно текущей базовой линии"],
  "intelligence.evaluation.reason.missing_engine": ["评测引擎不可用", "Evaluation engine unavailable", "Движок оценки недоступен"],
  "intelligence.evaluation.reason.missing_engine_config": ["引擎配置不可用", "Engine configuration unavailable", "Конфигурация движка недоступна"],
  "intelligence.evaluation.reason.missing_dual_route": ["双路检索不可用", "Dual-route retrieval unavailable", "Двухмаршрутный поиск недоступен"],
  "intelligence.evaluation.reason.missing_derived_reader": ["派生读取器不可用", "Derived reader unavailable", "Чтение производных данных недоступно"],
  "intelligence.evaluation.reason.missing_graph_retriever": ["图检索不可用", "Graph retrieval unavailable", "Поиск по графу недоступен"],
  "intelligence.evaluation.reason.missing_document_vector_access": ["文档向量不可用", "Document vectors unavailable", "Векторы документов недоступны"],
  "intelligence.evaluation.reason.readonly_snapshot_cannot_activate_worker": ["只读快照无法启动演化 Worker", "Readonly snapshot cannot activate the evolution worker", "Снимок только для чтения не может запустить worker эволюции"],
  "intelligence.evaluation.reason.variant_prepare_failed": ["变体准备失败", "Variant preparation failed", "Не удалось подготовить вариант"],
  "intelligence.evaluation.reason.variant_execution_failed": ["变体执行失败", "Variant execution failed", "Ошибка выполнения варианта"],
  "intelligence.evaluation.reason.variant_not_exercised": ["变体未实际触发", "Variant was not exercised", "Вариант не был фактически задействован"],
  "intelligence.evaluation.reason.embedding_query_failed": ["查询向量计算失败", "Query embedding failed", "Не удалось вычислить эмбеддинг запроса"],
  "intelligence.evaluation.reason.unknown_variant": ["未知变体", "Unknown variant", "Неизвестный вариант"],
  "intelligence.evaluation.metric.observedP95": ["实测 p95", "Observed p95", "Измеренный p95"],
  "intelligence.evaluation.metric.annotatedP95": ["标注 p95", "Annotated p95", "Аннотированный p95"],
  "intelligence.evaluation.metric.reportedP95": ["外部报告 p95", "Reported p95", "Заявленный p95"],
  "intelligence.evaluation.metric.annotatedFaithfulness": ["标注忠实度", "Annotated faithfulness", "Аннотированная достоверность"],
  "intelligence.evaluation.metric.judgedFaithfulness": ["Judge 忠实度", "Judged faithfulness", "Judge-достоверность"],
  "intelligence.evaluation.metric.observedProviderCalls": ["实测 Provider 调用", "Observed provider calls", "Измеренные вызовы провайдера"],
  "intelligence.evaluation.metric.observedTokenCost": ["实测 token 成本", "Observed token cost", "Измеренная стоимость токенов"],
  "intelligence.evaluation.table.observedLatency": ["实测延迟", "Observed latency", "Измеренная задержка"],
  "intelligence.evaluation.table.annotatedLatency": ["标注延迟", "Annotated latency", "Аннотированная задержка"],
  "intelligence.evaluation.table.reportedLatency": ["外部报告延迟", "Reported latency", "Заявленная задержка"],
  "intelligence.evaluation.observedP95Delta": ["实测 p95 差异", "Observed p95 delta", "Разница измеренного p95"],
};

/** 按语言位置生成评测变体和稳定原因码文案。 */
function evaluationLocaleCopy(index: 0 | 1 | 2): Record<string, string> {
  return Object.fromEntries(
    Object.entries(EVALUATION_COPY).map(([key, values]) => [key, values[index]]),
  );
}

export const EVALUATION_ZH_MAP = evaluationLocaleCopy(0);
export const EVALUATION_EN_MAP = evaluationLocaleCopy(1);
export const EVALUATION_RU_MAP = evaluationLocaleCopy(2);
