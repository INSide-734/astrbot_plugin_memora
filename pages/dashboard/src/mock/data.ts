// ================================================================
// Mock data for dashboard testing — realistic Memora samples
// ================================================================
import type {
  DiagnosticEvent,
  DiagnosticHealthResponse,
  EvaluationReport,
  ReviewAction,
  ReviewItem,
  RecallTraceResponse,
} from "@/types/intelligence";
import type {
  InjectionDecisionDetail,
  InjectionOutcome,
  InjectionPresetName,
  InjectionRoutingMode,
} from "@/types/injection";

export const INJECTION_MOCK_NOW_MS = Date.UTC(2026, 6, 15, 8, 0, 0);
const INJECTION_PRESETS: InjectionPresetName[] = [
  "tool_first",
  "low_cost",
  "balanced",
  "quality",
];
const INJECTION_MODES: InjectionRoutingMode[] = ["manual", "auto", "hybrid"];
const INJECTION_OUTCOMES: InjectionOutcome[] = [
  "injected",
  "skipped",
  "empty",
  "fallback",
  "error",
];

export const INJECTION_DECISIONS: InjectionDecisionDetail[] = Array.from(
  { length: 72 },
  (_, index) => {
    const resolved = INJECTION_PRESETS[index % INJECTION_PRESETS.length];
    const outcome = INJECTION_OUTCOMES[index % INJECTION_OUTCOMES.length];
    const fallback = outcome === "fallback" || index % 11 === 0;
    const budget = resolved === "quality"
      ? 2_400
      : resolved === "balanced"
        ? 1_200
        : resolved === "low_cost"
          ? 800
          : 0;
    return {
      decision_id: `00000000-0000-4000-8000-${String(index + 1).padStart(12, "0")}`,
      created_at_ms: INJECTION_MOCK_NOW_MS - index * 30 * 60 * 1_000,
      trace_id: index % 3 === 0
        ? `trace-mock-${String(index + 1).padStart(3, "0")}`
        : null,
      routing_mode: INJECTION_MODES[index % INJECTION_MODES.length],
      configured_preset: INJECTION_PRESETS[(index + 1) % INJECTION_PRESETS.length],
      recommended_preset: INJECTION_PRESETS[(index + 2) % INJECTION_PRESETS.length],
      resolved_preset: resolved,
      preferred_delivery: "extra_user_content",
      resolved_delivery: fallback
        ? "user_message_before"
        : "extra_user_content",
      fallback_applied: fallback,
      outcome,
      error_code: outcome === "error" ? "FORMAT_FAILED" : null,
      primary_reason: fallback
        ? "PROVIDER_DELIVERY_DOWNGRADED"
        : "MANUAL_SELECTED",
      reason_codes: fallback
        ? ["MANUAL_SELECTED", "PROVIDER_DELIVERY_DOWNGRADED"]
        : ["MANUAL_SELECTED"],
      provider_type: index % 2 === 0 ? "openai" : "gemini",
      provider_model: index % 2 === 0 ? "gpt-mock" : "gemini-mock",
      candidate_count: 6,
      selected_count: resolved === "tool_first" ? 0 : Math.min(4, index % 5),
      dropped_count: index % 3,
      truncated_count: index % 2,
      configured_budget_chars: budget,
      effective_budget_chars: budget,
      actual_payload_chars: budget === 0
        ? 0
        : Math.min(budget, 320 + index * 13),
      context_headroom_chars: 8_000 - index * 10,
      decision_ms: 0.4 + (index % 5) * 0.1,
      format_ms: 1.2 + (index % 7) * 0.2,
      inject_ms: 0.3 + (index % 3) * 0.1,
    };
  },
);

export const MEMORIES = Array.from({ length: 42 }, (_, i) => {
  const id = `mem_${String(i + 1).padStart(4, "0")}`;
  const types = ["episodic", "factual", "preference", "relational", "planned"] as const;
  const statuses = ["active", "active", "active", "active", "archived", "active", "active", "deleted"] as const;
  const summaries = [
    "用户喜欢在周末去咖啡馆工作，偏好安静的角落位置",
    "讨论了 Python 异步编程的最佳实践，用户对 asyncio 有深入理解",
    "用户提到下周计划去上海出差，需要准备技术演讲材料",
    "用户和小王是大学同学，两人经常一起讨论技术问题",
    "用户对 React 19 的新特性非常感兴趣，特别是 Server Components",
    "讨论了数据库索引优化策略，用户倾向于使用复合索引",
    "用户最近在学习 Rust 语言，对所有权系统感到困惑",
    "用户喜欢音乐，尤其偏好爵士乐和古典钢琴曲",
    "讨论了微服务架构的优缺点，用户认为过早拆分会增加复杂度",
    "用户提到项目中遇到了内存泄漏问题，正在排查原因",
  ];
  const contents = [
    "咖啡店的环境很适合我集中注意力，尤其是靠窗的位置，光线好也不吵闹。",
    "asyncio 的事件循环机制其实很好理解，关键是要理解协程的调度方式。我一般用 asyncio.gather 来并发执行多个任务。",
    "下周三飞上海，周四上午有个技术分享，主题是分布式系统的可观测性。需要准备一个 Demo。",
    "小王最近在研究大模型应用，我们上周还聊到可以用 RAG 来做知识库检索。他在这方面很有经验。",
    "React Server Components 确实改变了前端开发方式，但我担心迁移成本太高。我们项目还在用 class component。",
    "复合索引在查询条件顺序固定时效果很好，但如果查询条件变化多，可能要考虑覆盖索引或者分区。",
    "Rust 的所有权和借用检查器确实需要时间适应，但一旦理解了就豁然开朗。我正在用 Rust 重写一个内部工具。",
    "最近常听 Bill Evans 的钢琴独奏，尤其是 Waltz for Debby 这张专辑，非常适合编码时听。",
    "微服务不是银弹。如果团队只有 5 个人，维护 20 个微服务就是灾难。应该从单体开始，按需拆分。",
    "排查了半天发现是 WebSocket 连接没有正确关闭，导致 EventEmitter 累积。加上 cleanup 逻辑后问题解决了。",
  ];

  const type = types[i % types.length];
  const status = statuses[i % statuses.length];
  const importance = Math.round((3 + Math.random() * 7) * 10) / 10;
  const dayOffset = Math.floor(Math.random() * 60);
  const d = new Date();
  d.setDate(d.getDate() - dayOffset);

  return {
    id,
    content: contents[i % contents.length],
    summary: summaries[i % summaries.length],
    type,
    importance,
    status,
    created_at: d.toISOString(),
    updated_at: d.toISOString(),
    session_id: `sess_${Math.floor(Math.random() * 8 + 1)}`,
  };
});

export const GRAPH_NODES = [
  { id: "n1", label: "Python", type: "topic", weight: 8, memory_count: 12, degree: 7, entry_count: 3 },
  { id: "n2", label: "React", type: "topic", weight: 6, memory_count: 8, degree: 5, entry_count: 2 },
  { id: "n3", label: "Rust", type: "topic", weight: 4, memory_count: 5, degree: 4, entry_count: 1 },
  { id: "n4", label: "微服务", type: "topic", weight: 5, memory_count: 6, degree: 4, entry_count: 2 },
  { id: "n5", label: "数据库", type: "topic", weight: 7, memory_count: 9, degree: 6, entry_count: 2 },
  { id: "n6", label: "小王", type: "person", weight: 5, memory_count: 7, degree: 4, entry_count: 2 },
  { id: "n7", label: "用户", type: "person", weight: 10, memory_count: 20, degree: 12, entry_count: 5 },
  { id: "n8", label: "上海出差", type: "fact", weight: 3, memory_count: 2, degree: 2, entry_count: 1 },
  { id: "n9", label: "咖啡馆工作偏好", type: "fact", weight: 4, memory_count: 3, degree: 2, entry_count: 1 },
  { id: "n10", label: "爵士乐", type: "summary", weight: 3, memory_count: 2, degree: 2, entry_count: 1 },
  { id: "n11", label: "异步编程", type: "topic", weight: 6, memory_count: 7, degree: 5, entry_count: 2 },
  { id: "n12", label: "大模型应用", type: "topic", weight: 5, memory_count: 5, degree: 4, entry_count: 1 },
  { id: "n13", label: "Docker", type: "topic", weight: 4, memory_count: 4, degree: 3, entry_count: 1 },
  { id: "n14", label: "容器化", type: "topic", weight: 3, memory_count: 3, degree: 2, entry_count: 1 },
  { id: "n15", label: "Kubernetes", type: "topic", weight: 5, memory_count: 5, degree: 3, entry_count: 1 },
  { id: "n16", label: "项目启动", type: "fact", weight: 4, memory_count: 2, degree: 2, entry_count: 1 },
  { id: "n17", label: "技术选型", type: "summary", weight: 5, memory_count: 3, degree: 3, entry_count: 1 },
  { id: "n18", label: "性能测试", type: "fact", weight: 3, memory_count: 2, degree: 2, entry_count: 1 },
];

export const GRAPH_EDGES = [
  // 时序边 (dashed) — before / after / during
  { source: "n3", target: "n1", type: "before", weight: 4 },
  { source: "n1", target: "n2", type: "after", weight: 3 },
  { source: "n4", target: "n5", type: "during", weight: 5 },
  { source: "n16", target: "n17", type: "before", weight: 2 },
  { source: "n17", target: "n18", type: "after", weight: 3 },
  { source: "n9", target: "n12", type: "during", weight: 4 },
  // 因果边 (solid + labeled) — results_in / caused_by
  { source: "n1", target: "n4", type: "results_in", weight: 6 },
  { source: "n2", target: "n6", type: "caused_by", weight: 5 },
  { source: "n7", target: "n9", type: "results_in", weight: 4 },
  { source: "n10", target: "n11", type: "caused_by", weight: 3 },
  // 普通关系边
  { source: "n1", target: "n11", type: "related", weight: 5 },
  { source: "n5", target: "n4", type: "related", weight: 4 },
  { source: "n7", target: "n6", type: "knows", weight: 5 },
  { source: "n7", target: "n8", type: "planned", weight: 3 },
  { source: "n7", target: "n10", type: "preference", weight: 3 },
  { source: "n1", target: "n3", type: "related", weight: 3 },
  { source: "n7", target: "n1", type: "interested", weight: 5 },
  { source: "n7", target: "n2", type: "interested", weight: 4 },
  { source: "n7", target: "n5", type: "interested", weight: 4 },
  { source: "n6", target: "n12", type: "interested", weight: 4 },
  { source: "n7", target: "n3", type: "learning", weight: 4 },
  { source: "n7", target: "n12", type: "discussed", weight: 3 },
  // 结构边
  { source: "n13", target: "n14", type: "is_a", weight: 5 },
  { source: "n14", target: "n15", type: "is_a", weight: 4 },
  { source: "n18", target: "n12", type: "prevents", weight: 5 },
  { source: "n8", target: "n15", type: "co_occurs_with", weight: 2 },
  { source: "n5", target: "n9", type: "describes", weight: 3 },
  { source: "n2", target: "n13", type: "mentioned_in", weight: 2 },
];

export type MockProfileTagCategory = "interest" | "personality" | "habit" | "relation" | "knowledge" | "preference" | "custom";
export interface MockProfileTag { category: MockProfileTagCategory; value: string; confidence: number }
export interface MockProfilePreferences { reply_style?: string; preferred_topics?: string[]; avoided_topics?: string[]; active_hours?: number[] }
export interface MockProfile {
  user_id: string;
  display_name: string;
  tags: MockProfileTag[];
  preferences: MockProfilePreferences;
  message_count: number;
  last_active: string;
  revision?: string;
}

export const PROFILES: MockProfile[] = [
  {
    user_id: "user_001", display_name: "张三",
    tags: [
      { value: "Python 开发者", category: "knowledge", confidence: 0.95 },
      { value: "后端工程师", category: "custom", confidence: 0.9 },
      { value: "咖啡爱好者", category: "preference", confidence: 0.85 },
    ],
    preferences: { reply_style: "concise", preferred_topics: ["Python", "后端开发"], avoided_topics: [], active_hours: [9, 17] },
    message_count: 342,
    last_active: new Date(Date.now() - 2 * 3600 * 1000).toISOString(),
  },
  {
    user_id: "user_002", display_name: "李四",
    tags: [
      { value: "React 专家", category: "knowledge", confidence: 0.88 },
      { value: "开源贡献者", category: "custom", confidence: 0.7 },
    ],
    preferences: { reply_style: "detailed", preferred_topics: ["React", "测试"], avoided_topics: [], active_hours: [10, 18] },
    message_count: 156,
    last_active: new Date(Date.now() - 8 * 3600 * 1000).toISOString(),
  },
  {
    user_id: "user_003", display_name: "王五",
    tags: [
      { value: "全栈工程师", category: "custom", confidence: 0.92 },
      { value: "Rust 学习者", category: "interest", confidence: 0.6 },
    ],
    preferences: { reply_style: "casual", preferred_topics: ["Rust", "架构"], avoided_topics: [], active_hours: [8, 20] },
    message_count: 89,
    last_active: new Date(Date.now() - 24 * 3600 * 1000).toISOString(),
  },
];

export const KNOWLEDGE_ENTRIES = [
  { entry_id: "k1", title: "Python asyncio 事件循环原理", content: "asyncio 事件循环是单线程协程调度器，通过 epoll/kqueue 等系统调用实现 I/O 多路复用。", category: "concept", confidence: 0.95, access_count: 23, updated_at: "2026-06-10T08:00:00Z" },
  { entry_id: "k2", title: "React Server Components 工作机制", content: "RSC 在服务端渲染组件树，只发送序列化的 React 元素树到客户端，不包含组件代码。", category: "concept", confidence: 0.9, access_count: 15, updated_at: "2026-06-08T14:30:00Z" },
  { entry_id: "k3", title: "PostgreSQL 复合索引最佳实践", content: "复合索引列顺序应与查询条件顺序一致。最常用的过滤条件放在前面。使用 EXPLAIN ANALYZE 验证。", category: "rule", confidence: 0.92, access_count: 18, updated_at: "2026-06-05T10:00:00Z" },
  { entry_id: "k4", title: "Rust 所有权规则速查", content: "1. 每个值有且仅有一个所有者。2. 值离开作用域时被 drop。3. 借用分为共享引用(&T)和可变引用(&mut T)。", category: "fact", confidence: 0.98, access_count: 31, updated_at: "2026-06-12T09:00:00Z" },
  { entry_id: "k5", title: "微服务拆分决策框架", content: "按业务边界拆分，而不是按技术层。判断标准：独立部署频率、数据耦合度、团队对齐。", category: "procedure", confidence: 0.85, access_count: 12, updated_at: "2026-06-01T16:00:00Z" },
  { entry_id: "k6", title: "WebSocket 连接管理最佳实践", content: "必须实现心跳检测、自动重连（指数退避）、连接池限制、优雅关闭。", category: "rule", confidence: 0.9, access_count: 20, updated_at: "2026-06-11T11:00:00Z" },
];

export const NOTES = [
  { note_id: "note_001", title: "Python 协程深入理解笔记", content: "# Python 协程\n\n## 核心概念\n- 协程是可以在中途暂停和恢复的函数\n- async/await 是语法糖\n\n## 事件循环\n事件循环负责调度协程的执行。每个线程只能有一个事件循环。\n\n## 常见陷阱\n1. 不要在协程中使用 time.sleep()，应该用 asyncio.sleep()\n2. 阻塞 IO 会阻塞整个事件循环", tags: ["python", "asyncio"], status: "active", version: 3, updated_at: "2026-06-10T15:00:00Z", created_at: "2026-05-20T10:00:00Z" },
  { note_id: "note_002", title: "React 19 迁移计划", content: "# React 19 迁移\n\n## 新特性\n- Server Components（稳定）\n- Actions（表单处理简化）\n- use() hook\n\n## 迁移步骤\n1. 升级到 React 18.3 并修复所有废弃警告\n2. 安装 React 19 + @types/react\n3. 逐个组件迁移到 RSC", tags: ["react", "migration"], status: "active", version: 2, updated_at: "2026-06-08T09:00:00Z", created_at: "2026-06-01T14:00:00Z" },
  { note_id: "note_003", title: "数据库查询优化清单", content: "# 查询优化 Checklist\n\n- [ ] 使用 EXPLAIN ANALYZE 分析查询计划\n- [ ] 检查索引使用（Seq Scan vs Index Scan）\n- [ ] 避免 SELECT *\n- [ ] 大表 JOIN 考虑物化视图\n- [ ] 使用连接池（pgbouncer）\n- [ ] 定期 VACUUM 和 ANALYZE", tags: ["database", "postgresql"], status: "active", version: 1, updated_at: "2026-06-05T12:00:00Z", created_at: "2026-06-05T12:00:00Z" },
  { note_id: "note_004", title: "Rust 学习路线", content: "# Rust 学习路线\n\n## 基础\n- 所有权和借用\n- 结构体和枚举\n- 模式匹配\n\n## 进阶\n- trait 和泛型\n- 生命周期标注\n- 闭包和迭代器\n\n## 实战\n- 异步编程（tokio）\n- Web 框架（axum/actix）", tags: ["rust", "learning"], status: "active", version: 1, updated_at: "2026-06-12T08:00:00Z", created_at: "2026-06-12T08:00:00Z" },
  { note_id: "note_005", title: "微服务 vs 单体决策矩阵（已归档）", content: "此文档已过时，参见知识库中的'微服务拆分决策框架'", tags: ["architecture"], status: "archived", version: 1, updated_at: "2026-05-01T10:00:00Z", created_at: "2026-04-15T10:00:00Z" },
];

// ================================================================
// v1.0.0+ Mock data for new subsystems
// ================================================================

export const JARGON_CANDIDATES = [
  { term: "摸鱼", group_id: "group_001", score: 0.94, frequency: 87, unique_users: 23, idf_score: 0.62, burst_score: 0.88, concentration_score: 0.91, first_seen: Date.now() / 1000 - 86400 * 30, context_examples: ["今天又在摸鱼写代码", "别摸鱼了快来开会", "摸鱼是程序员的基本素养"] },
  { term: "画饼", group_id: "group_001", score: 0.89, frequency: 64, unique_users: 18, idf_score: 0.55, burst_score: 0.82, concentration_score: 0.85, first_seen: Date.now() / 1000 - 86400 * 45, context_examples: ["老板又在画饼了", "这个需求就是画饼", "别画饼了，说点实际的"] },
  { term: "打螺丝", group_id: "group_001", score: 0.85, frequency: 52, unique_users: 15, idf_score: 0.71, burst_score: 0.75, concentration_score: 0.78, first_seen: Date.now() / 1000 - 86400 * 20, context_examples: ["今天又是打螺丝的一天", "我在给前端打螺丝", "这个 bug 就是螺丝没打紧"] },
  { term: "赛博", group_id: "group_001", score: 0.81, frequency: 43, unique_users: 12, idf_score: 0.48, burst_score: 0.69, concentration_score: 0.88, first_seen: Date.now() / 1000 - 86400 * 60, context_examples: ["赛博祈祷一下编译通过", "赛博养生中", "这就是赛博朋克生活"] },
  { term: "特种兵", group_id: "group_001", score: 0.78, frequency: 38, unique_users: 10, idf_score: 0.52, burst_score: 0.73, concentration_score: 0.65, first_seen: Date.now() / 1000 - 86400 * 14, context_examples: ["特种兵式 Code Review", "昨天特种兵加班到三点", "这也太特种兵了"] },
  { term: "已读乱回", group_id: "group_002", score: 0.92, frequency: 71, unique_users: 20, idf_score: 0.58, burst_score: 0.91, concentration_score: 0.87, first_seen: Date.now() / 1000 - 86400 * 25, context_examples: ["他又已读乱回了", "不要已读乱回好不好", "产品经理经典已读乱回"] },
  { term: "DDL战神", group_id: "group_002", score: 0.87, frequency: 55, unique_users: 16, idf_score: 0.65, burst_score: 0.79, concentration_score: 0.82, first_seen: Date.now() / 1000 - 86400 * 35, context_examples: ["DDL战神模式启动", "我们组全是 DDL 战神", "明天 DDL 今天开始写"] },
];

export const JARGON_MEANINGS = [
  { term: "摸鱼", group_id: "group_001", meaning: "指在工作时间做与工作无关的事情，在技术群中常用于自嘲式地描述写个人项目或学习新技术", confidence: 0.92, is_jargon: true, is_confirmed: true, is_global: true, is_complete: true, count: 87, last_inference_count: 87, created_at: Date.now() / 1000 - 86400 * 30, updated_at: Date.now() / 1000 - 86400 * 2 },
  { term: "画饼", group_id: "group_001", meaning: "指做出不切实际的承诺或描述过于美好的愿景，常用于吐槽管理层或产品需求", confidence: 0.88, is_jargon: true, is_confirmed: true, is_global: true, is_complete: true, count: 64, last_inference_count: 64, created_at: Date.now() / 1000 - 86400 * 45, updated_at: Date.now() / 1000 - 86400 * 5 },
  { term: "打螺丝", group_id: "group_001", meaning: "比喻做重复性、低技术含量的编码工作，如写 CRUD、修简单 bug 等", confidence: 0.85, is_jargon: true, is_confirmed: true, is_global: false, is_complete: true, count: 52, last_inference_count: 52, created_at: Date.now() / 1000 - 86400 * 20, updated_at: Date.now() / 1000 - 86400 * 3 },
  { term: "已读乱回", group_id: "group_002", meaning: "指看到消息后故意回复无关内容或敷衍了事，源自社交平台'已读'功能", confidence: 0.91, is_jargon: true, is_confirmed: true, is_global: true, is_complete: true, count: 71, last_inference_count: 71, created_at: Date.now() / 1000 - 86400 * 25, updated_at: Date.now() / 1000 - 86400 * 1 },
  { term: "DDL战神", group_id: "group_002", meaning: "指在截止日期前极短时间内完成大量工作的人，带有敬佩和自嘲的双重意味", confidence: 0.86, is_jargon: true, is_confirmed: true, is_global: false, is_complete: true, count: 55, last_inference_count: 55, created_at: Date.now() / 1000 - 86400 * 35, updated_at: Date.now() / 1000 - 86400 * 4 },
  { term: "赛博", group_id: "group_001", meaning: "网络用语前缀，用于将传统概念数字化/网络化，如赛博祈福、赛博养生", confidence: 0.79, is_jargon: true, is_confirmed: true, is_global: true, is_complete: false, count: 43, last_inference_count: 30, created_at: Date.now() / 1000 - 86400 * 60, updated_at: Date.now() / 1000 - 86400 * 7 },
];

export const AFFECTION_DATA: Record<string, {
  group_id: string;
  total_affection: number;
  max_total_affection: number;
  user_count: number;
  top_users: Array<{ user_id: string; group_id: string; affection_score: number; affection_level: string; level_name: string; interaction_count: number; last_interaction: number }>;
  current_mood: { mood_type: string; intensity: number; duration_hours: number; description: string; start_time: number; is_active: boolean };
}> = {
  "group_001": {
    group_id: "group_001",
    total_affection: 342,
    max_total_affection: 800,
    user_count: 45,
    top_users: [
      { user_id: "user_001", group_id: "group_001", affection_score: 85, affection_level: "CLOSE", level_name: "亲密", interaction_count: 156, last_interaction: Date.now() / 1000 - 3600 },
      { user_id: "user_002", group_id: "group_001", affection_score: 62, affection_level: "FRIENDLY", level_name: "友好", interaction_count: 98, last_interaction: Date.now() / 1000 - 7200 },
      { user_id: "user_003", group_id: "group_001", affection_score: 45, affection_level: "WARM", level_name: "温暖", interaction_count: 67, last_interaction: Date.now() / 1000 - 18000 },
      { user_id: "user_004", group_id: "group_001", affection_score: 28, affection_level: "WARM", level_name: "温暖", interaction_count: 42, last_interaction: Date.now() / 1000 - 86400 },
      { user_id: "user_005", group_id: "group_001", affection_score: 12, affection_level: "NEUTRAL", level_name: "中立", interaction_count: 28, last_interaction: Date.now() / 1000 - 172800 },
    ],
    current_mood: { mood_type: "PLAYFUL", intensity: 0.72, duration_hours: 8, description: "感受到群友的活跃互动，Bot 心情愉快且调皮", start_time: Date.now() / 1000 - 3600, is_active: true },
  },
  "group_002": {
    group_id: "group_002",
    total_affection: 198,
    max_total_affection: 600,
    user_count: 30,
    top_users: [
      { user_id: "user_006", group_id: "group_002", affection_score: 70, affection_level: "FRIENDLY", level_name: "友好", interaction_count: 112, last_interaction: Date.now() / 1000 - 1800 },
      { user_id: "user_007", group_id: "group_002", affection_score: 38, affection_level: "WARM", level_name: "温暖", interaction_count: 55, last_interaction: Date.now() / 1000 - 14400 },
      { user_id: "user_008", group_id: "group_002", affection_score: 15, affection_level: "NEUTRAL", level_name: "中立", interaction_count: 32, last_interaction: Date.now() / 1000 - 43200 },
    ],
    current_mood: { mood_type: "CURIOUS", intensity: 0.55, duration_hours: 6, description: "对新话题产生兴趣，正在积极了解群友讨论的内容", start_time: Date.now() / 1000 - 1800, is_active: true },
  },
};

// Re-exported from constants so mock consumers don't break.
// New code should import directly from @/lib/constants.
export { MOOD_TYPES } from "@/lib/constants";

export const SOCIAL_RELATIONS = [
  { from_user: "user_001", to_user: "user_002", relation_type: "colleague", strength: 0.85, frequency: 72, last_interaction: Date.now() / 1000 - 3600, group_id: "group_001", tags: ["技术讨论", "代码审查"], category: "career" },
  { from_user: "user_001", to_user: "user_003", relation_type: "mentor_mentee", strength: 0.72, frequency: 45, last_interaction: Date.now() / 1000 - 7200, group_id: "group_001", tags: ["指导", "Python"], category: "career" },
  { from_user: "user_002", to_user: "user_004", relation_type: "best_friend", strength: 0.91, frequency: 98, last_interaction: Date.now() / 1000 - 1800, group_id: "group_001", tags: ["日常聊天", "周末聚会"], category: "emotional" },
  { from_user: "user_002", to_user: "user_005", relation_type: "classmate", strength: 0.55, frequency: 23, last_interaction: Date.now() / 1000 - 86400, group_id: "group_001", tags: ["大学"], category: "career" },
  { from_user: "user_006", to_user: "user_007", relation_type: "gaming_teammate", strength: 0.78, frequency: 56, last_interaction: Date.now() / 1000 - 5400, group_id: "group_002", tags: ["Valorant", "周末开黑"], category: "interest" },
  { from_user: "user_006", to_user: "user_008", relation_type: "colleague", strength: 0.63, frequency: 34, last_interaction: Date.now() / 1000 - 14400, group_id: "group_002", tags: ["项目协作"], category: "career" },
  { from_user: "user_001", to_user: "user_006", relation_type: "fellow_town", strength: 0.42, frequency: 15, last_interaction: Date.now() / 1000 - 172800, group_id: "group_001", tags: ["上海"], category: "geographic" },
  { from_user: "user_003", to_user: "user_005", relation_type: "rival", strength: 0.35, frequency: 18, last_interaction: Date.now() / 1000 - 43200, group_id: "group_001", tags: ["技术争论"], category: "emotional" },
  { from_user: "user_007", to_user: "user_008", relation_type: "board_game_friend", strength: 0.68, frequency: 28, last_interaction: Date.now() / 1000 - 259200, group_id: "group_002", tags: ["桌游", "狼人杀"], category: "interest" },
];

// Re-exported from constants so mock consumers don't break.
// New code should import directly from @/lib/constants.
export { RELATION_CATEGORIES } from "@/lib/constants";

export const QUALITY_SCORES = [
  { atom_id: 1042, overall: 0.87, consistency: 0.91, coherence: 0.85, relevance: 0.88, freshness: 0.92, accuracy: 0.79, timestamp: Date.now() / 1000 - 300 },
  { atom_id: 1041, overall: 0.76, consistency: 0.72, coherence: 0.80, relevance: 0.74, freshness: 0.85, accuracy: 0.69, timestamp: Date.now() / 1000 - 900 },
  { atom_id: 1040, overall: 0.92, consistency: 0.95, coherence: 0.91, relevance: 0.93, freshness: 0.88, accuracy: 0.93, timestamp: Date.now() / 1000 - 1500 },
  { atom_id: 1039, overall: 0.81, consistency: 0.83, coherence: 0.79, relevance: 0.85, freshness: 0.77, accuracy: 0.81, timestamp: Date.now() / 1000 - 2200 },
  { atom_id: 1038, overall: 0.64, consistency: 0.61, coherence: 0.58, relevance: 0.72, freshness: 0.66, accuracy: 0.63, timestamp: Date.now() / 1000 - 3000 },
  { atom_id: 1037, overall: 0.89, consistency: 0.88, coherence: 0.92, relevance: 0.87, freshness: 0.84, accuracy: 0.94, timestamp: Date.now() / 1000 - 3800 },
  { atom_id: 1036, overall: 0.73, consistency: 0.76, coherence: 0.71, relevance: 0.69, freshness: 0.80, accuracy: 0.69, timestamp: Date.now() / 1000 - 4500 },
  { atom_id: 1035, overall: 0.94, consistency: 0.96, coherence: 0.93, relevance: 0.95, freshness: 0.91, accuracy: 0.95, timestamp: Date.now() / 1000 - 5200 },
  { atom_id: 1034, overall: 0.68, consistency: 0.70, coherence: 0.65, relevance: 0.73, freshness: 0.60, accuracy: 0.72, timestamp: Date.now() / 1000 - 6000 },
  { atom_id: 1033, overall: 0.85, consistency: 0.87, coherence: 0.83, relevance: 0.86, freshness: 0.82, accuracy: 0.87, timestamp: Date.now() / 1000 - 6800 },
];

export const QUALITY_ALERTS = [
  { id: 1, level: "high", dimension: "accuracy", score: 0.58, threshold: 0.60, message: "准确度持续低于阈值，可能存在幻觉记忆", suggestion: "检查最近 10 条记忆的 LLM 抽取质量，考虑调整 prompt 或降低 temperature", timestamp: Date.now() / 1000 - 1800 },
  { id: 2, level: "medium", dimension: "coherence", score: 0.55, threshold: 0.60, message: "连贯性评分偏低，记忆之间关联不足", suggestion: "增加图记忆边的权重，或启用话题分割策略 C", timestamp: Date.now() / 1000 - 3600 },
  { id: 3, level: "info", dimension: "freshness", score: 0.65, threshold: 0.70, message: "记忆新鲜度略低，可能有过多旧记忆被召回", suggestion: "提高衰减率或降低旧记忆的初始召回权重", timestamp: Date.now() / 1000 - 7200 },
  { id: 4, level: "critical", dimension: "overall", score: 0.42, threshold: 0.50, message: "综合质量急剧下降，质量评分已自动暂停", suggestion: "立即检查 LLM Provider 状态和 prompt 配置，问题解决后手动重置监控", timestamp: Date.now() / 1000 - 900 },
];

export const DELEGATION_STATUS = {
  self_learning_active: true,
  self_learning_label: "astrbot_plugin_self_learning v1.2.0",
  chatplus_active: false,
  chatplus_label: "",
  delegated_jargon: false,
  delegated_expression: true,
  delegated_affection: true,
  delegated_reply: false,
};

export const EXPRESSION_PATTERNS = [
  { pattern_id: 1, situation: "被夸奖时", expression: "嘿嘿，谢谢夸奖！我会继续努力的~", group_id: "group_001", persona_id: "default", user_id: null, weight: 0.92, usage_count: 45, created_at: Date.now() / 1000 - 86400 * 30, last_used_at: Date.now() / 1000 - 3600 },
  { pattern_id: 2, situation: "回答技术问题", expression: "这个问题很有意思，让我来分析一下...", group_id: "group_001", persona_id: "default", user_id: null, weight: 0.88, usage_count: 78, created_at: Date.now() / 1000 - 86400 * 45, last_used_at: Date.now() / 1000 - 1800 },
  { pattern_id: 3, situation: "被质疑时", expression: "你说得对，我之前的回答确实不够准确。让我重新整理一下信息...", group_id: "group_001", persona_id: "default", user_id: null, weight: 0.85, usage_count: 23, created_at: Date.now() / 1000 - 86400 * 20, last_used_at: Date.now() / 1000 - 7200 },
  { pattern_id: 4, situation: "群友开玩笑时", expression: "哈哈哈，你们够了啊 😂", group_id: "group_002", persona_id: "default", user_id: null, weight: 0.79, usage_count: 56, created_at: Date.now() / 1000 - 86400 * 25, last_used_at: Date.now() / 1000 - 5400 },
  { pattern_id: 5, situation: "打招呼", expression: "早上好呀~今天也是元气满满的一天！☀️", group_id: "group_001", persona_id: "default", user_id: null, weight: 0.75, usage_count: 34, created_at: Date.now() / 1000 - 86400 * 15, last_used_at: Date.now() / 1000 - 14400 },
  { pattern_id: 6, situation: "被求助调试时", expression: "别急，我们一步一步来排查。先看看日志输出是什么？", group_id: "group_001", persona_id: "default", user_id: null, weight: 0.82, usage_count: 41, created_at: Date.now() / 1000 - 86400 * 35, last_used_at: Date.now() / 1000 - 10800 },
];

export const EVALUATION_DATASETS = [
  {
    name: "private_basic",
    case_count: 10,
    path: "tests/fixtures/retrieval/private_basic.jsonl",
    intents: ["preference"],
    chat_types: ["private"],
  },
  {
    name: "group_context",
    case_count: 12,
    path: "tests/fixtures/retrieval/group_context.jsonl",
    intents: ["relation", "fact"],
    chat_types: ["group"],
  },
];

export const EVALUATION_REPORTS: EvaluationReport[] = [
  {
    report_id: "eval-private-basic",
    created_at: Date.now() / 1000 - 1800,
    baseline: "baseline",
    datasets: ["private_basic"],
    summary: {
      total_cases: 20,
      k: 5,
      recall_at_k: 0.9,
      mrr: 0.74,
      ndcg_at_k: 0.78,
      p95_latency_ms: 42.6,
    },
    variants: {
      baseline: {
        name: "baseline",
        status: "completed",
        summary: {
          total_cases: 20,
          k: 5,
          recall_at_k: 0.9,
          mrr: 0.74,
          ndcg_at_k: 0.78,
          p95_latency_ms: 42.6,
        },
      },
      graph_expansion_off: {
        name: "graph_expansion_off",
        status: "completed",
        summary: {
          total_cases: 20,
          k: 5,
          recall_at_k: 0.85,
          mrr: 0.72,
          ndcg_at_k: 0.75,
          p95_latency_ms: 34.2,
        },
      },
      topic_expansion_off: {
        name: "topic_expansion_off",
        status: "completed",
        summary: {
          total_cases: 20,
          k: 5,
          recall_at_k: 0.82,
          mrr: 0.7,
          ndcg_at_k: 0.72,
          p95_latency_ms: 37.5,
        },
      },
    },
    deltas: {
      graph_expansion_off: {
        recall_at_k: -0.05,
        mrr: -0.02,
        ndcg_at_k: -0.03,
        p95_latency_ms: -8.4,
      },
      topic_expansion_off: {
        recall_at_k: -0.08,
        mrr: -0.04,
        ndcg_at_k: -0.06,
        p95_latency_ms: -5.1,
      },
    },
    cases: [
      {
        case_id: "coffee",
        query: "用户喜欢喝什么咖啡",
        ranked_doc_ids: ["mem-coffee", "mem-weekend"],
        recall_at_k: 1,
        reciprocal_rank: 1,
        ndcg_at_k: 1,
        latency_ms: 12.5,
      },
      {
        case_id: "weekend-workplace",
        query: "用户周末在哪里工作",
        ranked_doc_ids: ["mem-other", "mem-react"],
        recall_at_k: 0,
        reciprocal_rank: 0,
        ndcg_at_k: 0,
        latency_ms: 18.2,
      },
    ],
  },
];

export const RECALL_TRACE_SAMPLE: RecallTraceResponse = {
  trace_id: "trace-mock-coffee",
  query: "用户喜欢喝什么咖啡",
  total_ms: 84.2,
  stages: [
    {
      name: "query_parse",
      duration_ms: 4.1,
      candidate_count: 0,
      metadata: { tokenizer: "jieba", normalized: true },
    },
    {
      name: "bm25",
      duration_ms: 12.5,
      candidate_count: 7,
      metadata: { index: "atom_bm25", top_k: 20 },
    },
    {
      name: "vector",
      duration_ms: 24.8,
      candidate_count: 8,
      metadata: { provider: "mock_embedding", dimension: 768 },
    },
    {
      name: "rerank",
      duration_ms: 42.8,
      candidate_count: 5,
      metadata: { model: "mock_reranker", personalized: true },
    },
  ],
  results: [
    {
      doc_id: "mem-coffee",
      rank: 1,
      initial_score: 0.71,
      final_score: 0.93,
      score_contributions: [
        {
          source: "bm25",
          score: 0.62,
          weight: 0.35,
          explanation: "Query terms matched coffee preference memory.",
          metadata: { field: "summary" },
        },
        {
          source: "vector",
          score: 0.81,
          weight: 0.35,
          explanation: "Semantic match for coffee preference.",
          metadata: { route: "document" },
        },
        {
          source: "emotion_boost",
          score: 0.21,
          weight: 0.2,
          explanation: "情绪偏好提升：用户近期对咖啡话题互动积极。",
          metadata: { mood: "curious", affinity: 0.72 },
        },
      ],
      graph_paths: [
        {
          nodes: ["用户", "咖啡馆工作偏好", "咖啡"],
          edges: ["preference", "topic"],
          score: 0.72,
          metadata: { hop_count: 2, route: "graph" },
        },
      ],
      metadata: { type: "preference", session_id: "sess_1", provenance: "atom_store" },
    },
    {
      doc_id: "mem-weekend",
      rank: 2,
      initial_score: 0.54,
      final_score: 0.66,
      score_contributions: [
        {
          source: "bm25",
          score: 0.4,
          weight: 0.35,
          explanation: "Matched weekend workplace context.",
        },
        {
          source: "graph",
          score: 0.68,
          weight: 0.25,
          explanation: "Connected through workplace preference node.",
          metadata: { path_count: 1 },
        },
      ],
      graph_paths: [
        {
          nodes: ["用户", "周末工作", "咖啡馆"],
          edges: ["habit", "location"],
          score: 0.61,
          metadata: { hop_count: 2 },
        },
      ],
      metadata: { type: "episodic", session_id: "sess_2", provenance: "graph_store" },
    },
  ],
  filtered: [
    {
      doc_id: "mem-stale",
      reason: "low_score",
      stage: "rerank",
      score: 0.12,
      metadata: { threshold: 0.2 },
    },
    {
      doc_id: "mem-react",
      reason: "topic_mismatch",
      stage: "bm25",
      score: 0.18,
      metadata: { matched_terms: 1 },
    },
  ],
  created_at: Date.now() / 1000,
  metadata: { provider: "mock", chat_type: "private", provenance: "mock_server" },
};

export const DIAGNOSTIC_HEALTH: DiagnosticHealthResponse = {
  score: 82,
  level: "watch",
  domains: [
    { name: "provider", score: 90, status: "healthy", message: "LLM and embedding providers are ready." },
    { name: "recall", score: 78, status: "watch", message: "Recall p95 is above the preferred operating band." },
    { name: "write", score: 96, status: "healthy", message: "Write coordinator is accepting operations." },
    { name: "scheduler", score: 74, status: "watch", message: "Backfill scheduler has recent retry history." },
    { name: "index", score: 66, status: "degraded", message: "Index validator recommends a rebuild." },
    { name: "prometheus", score: 100, status: "healthy", message: "Prometheus collectors are registered." },
  ],
  recommended_actions: [
    "Review index drift before peak traffic.",
    "Refresh metrics after provider recovery.",
  ],
};

export const DIAGNOSTIC_EVENTS: DiagnosticEvent[] = [
  {
    event_id: "diag-index-drift",
    created_at: new Date(Date.now() - 12 * 60 * 1000).toISOString(),
    domain: "index",
    severity: "warning",
    title: "Index drift detected",
    message: "Document and vector index counts differ by 2 entries.",
    source: "index_validator",
    payload: { expected: 128, actual: 126 },
    resolved_at: null,
  },
  {
    event_id: "diag-provider-recovered",
    created_at: new Date(Date.now() - 50 * 60 * 1000).toISOString(),
    domain: "provider",
    severity: "info",
    title: "Provider recovered",
    message: "Embedding provider became ready after retry.",
    source: "provider_waiter",
    payload: { attempts: 3 },
    resolved_at: new Date(Date.now() - 48 * 60 * 1000).toISOString(),
  },
];

export const REVIEW_ITEMS: ReviewItem[] = [
  {
    item_id: "review-duplicate-1",
    memory_id: "mem-duplicate-1",
    reasons: ["duplicate"],
    severity: "medium",
    status: "open",
    content_preview: "重复记忆：用户周末喜欢在安静咖啡馆工作，偏好靠窗位置。",
    metadata: {
      provenance: "atom_store",
      session_id: "sess_1",
      source: "quality_scorer",
      candidate_memory_id: "mem_0001",
    },
    created_at: Date.now() / 1000 - 5400,
    updated_at: Date.now() / 1000 - 1200,
  },
  {
    item_id: "review-stale-1",
    memory_id: "mem-stale-1",
    reasons: ["stale"],
    severity: "low",
    status: "approved",
    content_preview: "旧偏好：用户曾经偏好 React class component，可能已被后续 RSC 讨论覆盖。",
    metadata: {
      provenance: "decay_scheduler",
      session_id: "sess_4",
      source: "freshness_check",
    },
    created_at: Date.now() / 1000 - 86400,
    updated_at: Date.now() / 1000 - 3600,
  },
];

export const REVIEW_ACTIONS: Record<string, ReviewAction[]> = {
  "review-duplicate-1": [
    {
      action_id: "review-action-1",
      item_id: "review-duplicate-1",
      action: "flagged",
      actor_id: "system",
      payload: { reason: "duplicate", candidate_memory_id: "mem_0001" },
      created_at: Date.now() / 1000 - 5400,
    },
  ],
  "review-stale-1": [
    {
      action_id: "review-action-2",
      item_id: "review-stale-1",
      action: "approve",
      actor_id: "operator",
      payload: { note: "kept as historical preference" },
      created_at: Date.now() / 1000 - 3600,
    },
  ],
};
