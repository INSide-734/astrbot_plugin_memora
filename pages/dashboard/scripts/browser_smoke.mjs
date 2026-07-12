import { chromium } from "playwright";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { pathToFileURL } from "node:url";
import {
  BROWSER_LAUNCH_CANDIDATES,
  ROUTE_LOADING_TEXT,
} from "./browser_smoke_helpers.mjs";

const dashboardRoot = process.cwd();
const htmlPath = path.join(dashboardRoot, "index.html");
const html = await readFile(htmlPath, "utf8");

if (html.includes("/src/main") || html.includes('type="module"')) {
  throw new Error("Dashboard index.html is not a production AstrBot-compatible build");
}

const SCREENSHOT_BASELINES = {
  "graph.png": { width: 1366, height: 900, minBytes: 10_000 },
  "memory.png": { width: 1366, height: 900, minBytes: 10_000 },
  "system.png": { width: 1366, height: 900, minBytes: 10_000 },
  "jargon.png": { width: 1366, height: 900, minBytes: 10_000 },
  "intelligence-evaluation.png": { width: 1366, height: 900, minBytes: 10_000 },
  "intelligence-trace.png": { width: 1366, height: 900, minBytes: 10_000 },
  "intelligence-diagnostics.png": { width: 1366, height: 900, minBytes: 10_000 },
  "intelligence-review.png": { width: 1366, height: 900, minBytes: 10_000 },
  "mobile-system.png": { width: 390, height: 844, minBytes: 10_000 },
  "mobile-jargon.png": { width: 390, height: 844, minBytes: 10_000 },
  "system-confirmation.png": { width: 1366, height: 900, minBytes: 10_000 },
  "dark-system.png": { width: 1366, height: 900, minBytes: 10_000 },
  "preview.png": { width: 1366, height: 900, minBytes: 10_000 },
  "mobile-preview.png": { width: 390, height: 844, minBytes: 10_000 },
  "dark-preview.png": { width: 1366, height: 900, minBytes: 10_000 },
  "wide-preview.png": { width: 2048, height: 1152, minBytes: 10_000 },
  "wide-learning.png": { width: 2048, height: 1152, minBytes: 10_000 },
  "wide-affection.png": { width: 2048, height: 1152, minBytes: 10_000 },
  "wide-social.png": { width: 2048, height: 1152, minBytes: 10_000 },
};

async function launchBrowser() {
  const failures = [];
  for (const candidate of BROWSER_LAUNCH_CANDIDATES) {
    try {
      const options = candidate.channel
        ? { channel: candidate.channel, headless: true }
        : { headless: true };
      const browser = await chromium.launch(options);
      return { browser, label: candidate.label };
    } catch (error) {
      failures.push(`${candidate.label}: ${error?.message ?? String(error)}`);
    }
  }
  throw new Error(
    "No Chromium-compatible browser is available for Dashboard browser smoke.\n"
      + failures.join("\n")
  );
}

function bridgePayload(endpoint) {
  const pathOnly = String(endpoint || "").replace(/^page\/?/, "");
  if (pathOnly === "stats") {
    return {
      total_memories: 12,
      active_count: 8,
      archived_count: 3,
      deleted_count: 1,
      graph_nodes: 7,
      graph_edges: 5,
      atom_count: 10,
      graph_entries: 4,
      avg_importance: 0.64,
      status_breakdown: { active: 8, archived: 3, deleted: 1 },
      atom_breakdown: { fact: 6, preference: 5, event: 3, relation: 2, summary: 1, other_type: 1 },
      recent_sessions: [
        { session_id: "group-smoke-primary", message_count: 9 },
        { session_id: "group-smoke-secondary", message_count: 5 },
      ],
      daily_memory_counts: Array.from({ length: 90 }, (_, index) => ({
        date: new Date(Date.UTC(2026, 3, 14 + index)).toISOString().slice(0, 10),
        count: index < 60 ? 0 : index - 59,
      })),
      importance_distribution: {
        "0-1": 0,
        "1-2": 1,
        "2-3": 1,
        "3-4": 2,
        "4-5": 3,
        "5-6": 4,
        "6-7": 4,
        "7-8": 3,
        "8-9": 1,
        "9-10": 1,
      },
      atom_types: { fact: 4, note: 6 },
      sessions: { "group-smoke": { message_count: 3 } },
    };
  }
  if (pathOnly === "metrics/summary") {
    return {
      recall: { sample_count: 7, p50_total_ms: 42.4, p95_total_ms: 123.4 },
      background_tasks: { tracked: 5, active: 2, completed: 3, failed: 1 },
      provider: { status: "ready", attempts: 3, max_attempts: 60 },
      index: {
        validator_available: true,
        last_rebuild_success: true,
        last_rebuild_errors: 0,
        last_rebuild_total: 12,
        last_rebuild_duration_seconds: 1.2,
      },
      write_coordinator: {
        operations_total: 20,
        lock_retries_total: 4,
        failures_total: 1,
        last_error: null,
      },
      prometheus: { available: true, collector_count: 9 },
    };
  }
  if (pathOnly === "backup/list") {
    return {
      backups: [
        { name: "backup-smoke-a", file_count: 2, backup_timestamp: "2026-07-04T05:30:00Z" },
        { name: "backup-smoke-b", file_count: 3, backup_timestamp: "2026-07-04T05:35:00Z" },
      ],
    };
  }
  if (pathOnly === "graph/overview") {
    return { graph_nodes: 7, graph_edges: 5, graph_entries: 4, enabled: true };
  }
  if (pathOnly === "graph/search" || pathOnly === "graph/query") {
    return {
      nodes: [{ id: 1, label: "Alice", type: "person", weight: 1 }],
      edges: [],
      summary: { visible_node_count: 1, visible_edge_count: 0 },
    };
  }
  if (pathOnly === "memories") {
    return {
      memories: [
        { id: 1, text: "浏览器 smoke 记忆", importance: 0.8, metadata: {} },
      ],
      total: 1,
    };
  }
  if (pathOnly === "jargon/stats") return { total_terms: 1, confirmed_terms: 0 };
  if (pathOnly === "jargon/candidates") {
    return { candidates: [{ term: "梗", score: 0.9, occurrences: 3 }] };
  }
  if (pathOnly === "jargon/meanings") return { meanings: [] };
  if (pathOnly === "groups") return { groups: [{ group_id: "group-smoke" }] };
  if (pathOnly === "profiles") return { profiles: [{ user_id: "user-smoke" }], total: 1 };
  if (pathOnly === "knowledge") return { items: [{ id: 1, title: "Smoke knowledge" }], total: 1 };
  if (pathOnly === "notes") return { notes: [{ id: 1, title: "Smoke note" }], total: 1, active_count: 1 };
  if (pathOnly === "learning/status") {
    return {
      hit_rate: 0.83,
      avg_quality: 0.812,
      total_trials: 18,
      total_corrections: 4,
      parameters: { retrieval_weight: 0.8, style_bias: 0.35 },
      history: [
        { timestamp: "2026-07-12T08:30:00Z", action: "adjusted", detail: "Raised retrieval weight" },
        { timestamp: "2026-07-12T08:00:00Z", action: "reviewed", detail: "Validated style preference" },
      ],
    };
  }
  if (pathOnly === "expression/patterns") {
    return {
      patterns: [
        {
          pattern_id: 1,
          group_id: "group-smoke",
          situation: "Greeting",
          expression: "Formal greeting",
          weight: 0.8,
          usage_count: 6,
        },
      ],
    };
  }
  if (pathOnly === "affection/status") {
    return {
      group_id: "group-smoke",
      total_affection: 48,
      max_total_affection: 100,
      user_count: 2,
      current_mood: {
        mood_type: "HAPPY",
        intensity: 0.72,
        description: "群聊今天的氛围很积极。",
        is_active: true,
      },
      top_users: [
        { user_id: "alice", affection_score: 42, level_name: "友好", interaction_count: 8 },
        { user_id: "bob", affection_score: 6, level_name: "中立", interaction_count: 3 },
      ],
    };
  }
  if (pathOnly === "social/relations") {
    return {
      relations: [
        {
          from_user: "alice",
          to_user: "bob",
          relation_type: "friend",
          strength: 0.76,
          frequency: 9,
          group_id: "group-smoke",
          tags: ["pair", "project"],
          category: "emotional",
        },
      ],
    };
  }
  if (pathOnly === "evaluation/datasets") {
    return {
      datasets: [
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
      ],
    };
  }
  if (pathOnly === "evaluation/reports") {
    return {
      reports: [
        {
          report_id: "eval-smoke-latest",
          created_at: 1_782_000_000,
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
          },
          deltas: {},
          cases: [],
        },
      ],
    };
  }
  if (pathOnly === "evaluation/reports/detail" || pathOnly === "evaluation/report") {
    return {
      report: {
        report_id: "eval-smoke-latest",
        created_at: 1_782_000_000,
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
        variants: {},
        deltas: {},
        cases: [],
      },
    };
  }
  if (pathOnly === "evaluation/run") {
    return {
      report_id: "eval-smoke-run",
      created_at: 1_782_000_100,
      baseline: "baseline",
      datasets: ["private_basic"],
      summary: {
        total_cases: 20,
        k: 5,
        recall_at_k: 0.91,
        mrr: 0.75,
        ndcg_at_k: 0.79,
        p95_latency_ms: 40.1,
      },
      variants: {},
      deltas: {},
      cases: [],
    };
  }
  if (pathOnly === "recall/trace" || pathOnly === "recall/traces" || pathOnly === "recall/trace/detail") {
    return {
      trace_id: "trace-smoke-coffee",
      query: "用户喜欢喝什么咖啡",
      total_ms: 84.2,
      stages: [
        { name: "bm25", duration_ms: 12.5, candidate_count: 7, metadata: { index: "atom_bm25" } },
        { name: "vector", duration_ms: 24.8, candidate_count: 8, metadata: { provider: "mock_embedding" } },
      ],
      results: [
        {
          doc_id: "mem-coffee",
          rank: 1,
          initial_score: 0.71,
          final_score: 0.93,
          score_contributions: [
            { source: "bm25", score: 0.62, weight: 0.35, explanation: "Matched coffee preference." },
          ],
          graph_paths: [],
          metadata: { type: "preference", provenance: "browser_smoke" },
        },
      ],
      filtered: [],
      created_at: 1_782_000_000,
      metadata: { provider: "mock", chat_type: "private" },
    };
  }
  if (pathOnly === "diagnostics/health") {
    return {
      score: 82,
      level: "watch",
      domains: [
        { name: "provider", score: 90, status: "healthy", message: "LLM and embedding providers are ready." },
        { name: "recall", score: 78, status: "watch", message: "Recall p95 is above the preferred band." },
        { name: "write", score: 96, status: "healthy", message: "Write coordinator is accepting operations." },
        { name: "scheduler", score: 74, status: "watch", message: "Backfill scheduler has recent retry history." },
        { name: "index", score: 66, status: "degraded", message: "Index validator recommends a rebuild." },
        { name: "prometheus", score: 100, status: "healthy", message: "Prometheus collectors are registered." },
      ],
      recommended_actions: ["Review index drift before peak traffic."],
    };
  }
  if (pathOnly === "diagnostics/events") {
    return {
      events: [
        {
          event_id: "diag-index-drift",
          created_at: "2026-07-05T00:00:00Z",
          domain: "index",
          severity: "warning",
          title: "Index drift detected",
          message: "Document and vector index counts differ by 2 entries.",
          source: "index_validator",
          payload: { expected: 128, actual: 126 },
          resolved_at: null,
        },
      ],
      total: 1,
    };
  }
  if (pathOnly === "diagnostics/actions/run") return { success: true };
  if (pathOnly === "review/items") {
    return {
      items: [
        {
          item_id: "review-smoke-duplicate",
          memory_id: "mem-smoke-duplicate",
          reasons: ["duplicate"],
          severity: "medium",
          status: "open",
          content_preview: "重复记忆：用户周末喜欢在安静咖啡馆工作，偏好靠窗位置。",
          metadata: {
            provenance: "atom_store",
            session_id: "sess-smoke",
            source: "quality_scorer",
          },
          created_at: 1_782_000_000,
          updated_at: 1_782_000_100,
        },
      ],
      total: 1,
    };
  }
  if (pathOnly === "review/items/detail") {
    return {
      item: {
        item_id: "review-smoke-duplicate",
        memory_id: "mem-smoke-duplicate",
        reasons: ["duplicate"],
        severity: "medium",
        status: "open",
        content_preview: "重复记忆：用户周末喜欢在安静咖啡馆工作，偏好靠窗位置。",
        metadata: {
          provenance: "atom_store",
          session_id: "sess-smoke",
          source: "quality_scorer",
        },
        created_at: 1_782_000_000,
        updated_at: 1_782_000_100,
      },
      actions: [
        {
          action_id: "review-action-smoke",
          item_id: "review-smoke-duplicate",
          action: "flagged",
          actor_id: "system",
          payload: { reason: "duplicate" },
          created_at: 1_782_000_000,
        },
      ],
    };
  }
  if (pathOnly === "review/refresh" || pathOnly === "review/action") return { success: true };
  return {};
}

function ok(data) {
  return { status: "ok", data };
}

function assertText(text, expected, route) {
  const values = Array.isArray(expected) ? expected : [expected];
  for (const value of values) {
    if (!text.includes(value)) {
      throw new Error(`Dashboard route ${route} did not render expected text: ${value}`);
    }
  }
}

async function waitForRootText(page, expected, route) {
  const values = Array.isArray(expected) ? expected : [expected];
  try {
    await page.waitForFunction(
      ({ items, loadingText }) => {
        const text = document.querySelector("#root")?.innerText ?? "";
        return (
          items.every((item) => text.includes(item))
          && loadingText.every((item) => !text.includes(item))
        );
      },
      { items: values, loadingText: ROUTE_LOADING_TEXT },
      { timeout: 5_000 }
    );
  } catch (error) {
    const rootText = await page.locator("#root").innerText();
    const missing = values.filter((item) => !rootText.includes(item));
    const lingeringLoading = ROUTE_LOADING_TEXT.filter((item) => rootText.includes(item));
    throw new Error(
      `Dashboard route ${route} did not become ready. Missing: ${missing.join(", ") || "none"}; `
        + `loading text: ${lingeringLoading.join(", ") || "none"}; root text: ${rootText}`,
      { cause: error }
    );
  }
  assertText(await page.locator("#root").innerText(), values, route);
}

function assertScreenshotLooksNonEmpty(buffer, label) {
  if (!Buffer.isBuffer(buffer) || buffer.length < 10_000) {
    throw new Error(`Dashboard browser smoke screenshot is unexpectedly small for ${label}`);
  }
}

function readPngDimensions(buffer) {
  const pngSignature = "89504e470d0a1a0a";
  if (!Buffer.isBuffer(buffer) || buffer.subarray(0, 8).toString("hex") !== pngSignature) {
    throw new Error("Dashboard browser smoke screenshot is not a PNG");
  }
  return {
    width: buffer.readUInt32BE(16),
    height: buffer.readUInt32BE(20),
  };
}

function assertScreenshotMatchesBaseline(buffer, filename, label) {
  assertScreenshotLooksNonEmpty(buffer, label);
  const baseline = SCREENSHOT_BASELINES[filename];
  if (!baseline) {
    throw new Error(`Dashboard browser smoke has no screenshot baseline for ${filename}`);
  }
  const dimensions = readPngDimensions(buffer);
  if (dimensions.width !== baseline.width || dimensions.height !== baseline.height) {
    throw new Error(
      `Dashboard browser smoke screenshot ${filename} size changed: `
        + `${dimensions.width}x${dimensions.height}, expected ${baseline.width}x${baseline.height}`
    );
  }
  if (buffer.length < baseline.minBytes) {
    throw new Error(
      `Dashboard browser smoke screenshot ${filename} is below baseline bytes: `
        + `${buffer.length}, expected at least ${baseline.minBytes}`
    );
  }
  return { filename, label, bytes: buffer.length, ...dimensions, minBytes: baseline.minBytes };
}

async function waitForVisualStability(page) {
  await page.waitForFunction(() => {
    const root = document.querySelector("#root");
    let element = document.querySelector('[data-slot="page-frame"]');
    if (!root || !element) return false;
    while (element && element !== root) {
      if (Number.parseFloat(getComputedStyle(element).opacity) < 0.99) return false;
      element = element.parentElement;
    }
    return true;
  }, undefined, { timeout: 3_000 });
  await page.evaluate(async () => {
    const finiteAnimations = document.getAnimations().filter((animation) => {
      const duration = animation.effect?.getTiming().duration;
      return animation.playState === "running"
        && typeof duration === "number"
        && Number.isFinite(duration)
        && duration <= 2_000;
    });
    await Promise.all(finiteAnimations.map((animation) => animation.finished.catch(() => undefined)));
    await new Promise((resolve) => {
      requestAnimationFrame(() => requestAnimationFrame(resolve));
    });
  });
}

async function captureBaselineScreenshot(page, screenshotPath, label) {
  await waitForVisualStability(page);
  const pageContent = page.locator('[data-slot="page-content"]').last();
  if (await pageContent.count()) {
    const box = await pageContent.boundingBox();
    const viewport = page.viewportSize();
    if (
      !box
      || !viewport
      || box.width <= 0
      || box.height <= 0
      || box.x >= viewport.width
      || box.x + box.width <= 0
      || box.y >= viewport.height
      || box.y + box.height <= 0
    ) {
      throw new Error(
        `Dashboard page content is outside the viewport for ${label}: `
          + `box=${JSON.stringify(box)}, viewport=${JSON.stringify(viewport)}`
      );
    }
    const firstChild = pageContent.locator(":scope > *").first();
    if (await firstChild.count()) {
      const childBox = await firstChild.boundingBox();
      if (
        !childBox
        || childBox.width <= 0
        || childBox.height <= 0
        || childBox.x >= box.x + box.width
        || childBox.x + childBox.width <= box.x
        || childBox.y >= box.y + box.height
        || childBox.y + childBox.height <= box.y
      ) {
        throw new Error(
          `Dashboard page content has no visible first child for ${label}: `
            + `contentBox=${JSON.stringify(box)}, childBox=${JSON.stringify(childBox)}`
        );
      }
    }
  }
  const screenshot = await page.screenshot({ path: screenshotPath, fullPage: false });
  return assertScreenshotMatchesBaseline(screenshot, path.basename(screenshotPath), label);
}

async function clickSidebarNav(page, label, expectedHash, expectedText, screenshotPath) {
  await page.getByRole("button", { name: label }).click();
  await page.waitForFunction(
    (nextHash) => window.location.hash === nextHash,
    expectedHash,
    { timeout: 5_000 }
  );
  await waitForRootText(page, expectedText, expectedHash);
  return await captureBaselineScreenshot(page, screenshotPath, label);
}

async function captureRoute(page, hash, expectedText, screenshotPath, label) {
  await page.evaluate((nextHash) => {
    window.location.hash = nextHash;
  }, hash);
  await page.waitForFunction(
    (nextHash) => window.location.hash === nextHash,
    hash,
    { timeout: 5_000 }
  );
  await waitForRootText(page, expectedText, hash);
  return await captureBaselineScreenshot(page, screenshotPath, label);
}

async function selectIntelligenceTab(page, label, tabId, expectedText) {
  await page.getByRole("tab", { name: label }).click();
  await page.waitForFunction(
    (id) => document.getElementById(`intelligence-tab-${id}`)?.getAttribute("aria-selected") === "true",
    tabId,
    { timeout: 5_000 }
  );
  await page.waitForFunction(
    (nextHash) => window.location.hash === nextHash,
    "#/intelligence",
    { timeout: 5_000 }
  );
  await waitForRootText(page, expectedText, `#/intelligence:${tabId}`);
}

async function clickIntelligenceTab(page, label, tabId, expectedText, screenshotPath) {
  await selectIntelligenceTab(page, label, tabId, expectedText);
  return await captureBaselineScreenshot(page, screenshotPath, `Intelligence ${label}`);
}

async function runRecallTraceSmoke(page, screenshotPath) {
  await selectIntelligenceTab(
    page,
    "召回链路",
    "recallTrace",
    ["召回链路", "查询", "运行一次链路追踪，查看阶段耗时、排序证据、贡献项与过滤候选。"]
  );
  await page.getByPlaceholder("输入要追踪的查询...").fill("用户喜欢喝什么咖啡");
  await page.getByRole("button", { name: "追踪", exact: true }).click();
  await waitForRootText(
    page,
    ["trace-smoke-coffee", "mem-coffee", "bm25", "mock_embedding"],
    "#/intelligence:recallTrace"
  );
  return await captureBaselineScreenshot(page, screenshotPath, "Intelligence 召回链路");
}

async function clickMobileNav(page, label, expectedHash, expectedText, screenshotPath) {
  await page.getByRole("button", { name: "Open menu" }).click();
  await page.getByRole("button", { name: label }).click();
  await page.waitForFunction(
    (nextHash) => window.location.hash === nextHash,
    expectedHash,
    { timeout: 5_000 }
  );
  await waitForRootText(page, expectedText, expectedHash);
  if (expectedHash === "#/preview") {
    await assertMobilePreviewLayout(page);
  }
  return await captureBaselineScreenshot(page, screenshotPath, `mobile-${label}`);
}

async function assertMobilePreviewLayout(page) {
  const slots = [
    "preview-metrics",
    "growth-panel",
    "composition-panel",
    "module-assets-panel",
    "active-sessions-panel",
  ];
  await page.locator('[data-slot="active-sessions-panel"]').waitFor({ state: "visible" });
  const positions = await Promise.all(slots.map(async (slot) => {
    const box = await page.locator(`[data-slot="${slot}"]`).boundingBox();
    return { slot, top: box?.y ?? Number.NaN };
  }));
  const overflow = await page.locator('[data-slot="page-content"]').last().evaluate(
    (content) => content.scrollWidth - content.clientWidth
  );
  const result = { positions, overflow };
  const validOrder = result.positions.every((item, index) => (
    Number.isFinite(item.top)
    && (index === 0 || item.top > result.positions[index - 1].top)
  ));
  if (!validOrder || !Number.isFinite(result.overflow) || result.overflow > 1) {
    throw new Error(`Mobile preview layout is invalid: ${JSON.stringify(result)}`);
  }
}

function assertNoPostCall(postCalls, endpoint) {
  if (postCalls.includes(endpoint)) {
    throw new Error(`High-impact action ${endpoint} was called before confirmation`);
  }
}

async function clickButtonByAnyName(page, labels) {
  const attempts = [];
  for (const label of labels) {
    try {
      await page.getByRole("button", { name: label }).first().click({ timeout: 1_000 });
      return label;
    } catch (error) {
      attempts.push(`${label}: ${error?.message ?? String(error)}`);
    }
  }
  throw new Error(`Could not click any button label:\n${attempts.join("\n")}`);
}

async function waitForTextByAny(page, texts, options = {}) {
  const attempts = [];
  for (const text of texts) {
    try {
      await page.getByText(text).first().waitFor({ timeout: 1_000, ...options });
      return text;
    } catch (error) {
      attempts.push(`${text}: ${error?.message ?? String(error)}`);
    }
  }
  throw new Error(`Could not find any expected text:\n${attempts.join("\n")}`);
}

function backupRow(page, backupName) {
  return page
    .getByText(backupName)
    .locator("xpath=ancestor::div[contains(@class, 'justify-between')][1]");
}

function confirmationBar(page, confirmText) {
  return page
    .getByText(confirmText)
    .first()
    .locator("xpath=ancestor::div[contains(@class, 'justify-between')][1]");
}

async function assertConfirmationCancelsWithoutPost(page, trigger, confirmTexts, endpoint) {
  await trigger();
  const confirmText = await waitForTextByAny(page, confirmTexts, { timeout: 5_000 });
  assertNoPostCall(await getPostCalls(page), endpoint);
  await clickButtonByAnyName(confirmationBar(page, confirmText), ["Cancel", "取消", "Отмена"]);
  await page.getByText(confirmText).first().waitFor({
    state: "detached",
    timeout: 5_000,
  });
  assertNoPostCall(await getPostCalls(page), endpoint);
}

async function assertBackupDestructiveConfirmations(page) {
  await page.getByText("backup-smoke-a").waitFor({ timeout: 5_000 });
  await page.getByText("backup-smoke-b").waitFor({ timeout: 5_000 });

  await assertConfirmationCancelsWithoutPost(
    page,
    async () => {
      await clickButtonByAnyName(backupRow(page, "backup-smoke-a"), [
        "Restore Backup",
        "恢复备份",
        "Восстановить",
      ]);
    },
    [
      "Restore data from backup-smoke-a? This will overwrite current data.",
      "确定要从 backup-smoke-a 恢复数据吗？这将覆盖当前数据。",
      "Восстановить данные из backup-smoke-a? Текущие данные будут перезаписаны.",
    ],
    "backup/restore"
  );

  await assertConfirmationCancelsWithoutPost(
    page,
    async () => {
      await backupRow(page, "backup-smoke-a").getByRole("button").nth(1).click();
    },
    [
      "Delete backup backup-smoke-a? This cannot be undone.",
      "确定要删除备份 backup-smoke-a 吗？此操作不可撤销。",
      "Удалить резервную копию backup-smoke-a? Это действие необратимо.",
    ],
    "backup/delete"
  );

  await clickButtonByAnyName(page, ["Select all", "全选", "Все"]);
  await assertConfirmationCancelsWithoutPost(
    page,
    async () => {
      await clickButtonByAnyName(page, ["Delete Selected (2)", "删除选中 (2)", "Удалить (2)"]);
    },
    [
      "Delete 2 backups? This cannot be undone.",
      "确定要删除 2 个备份吗？此操作不可撤销。",
      "Удалить 2 резервных копий? Это действие необратимо.",
    ],
    "backup/batch-delete"
  );
}

async function assertHighImpactConfirmation(page) {
  const actions = [
    {
      endpoint: "dashboard/install",
      buttonLabels: ["Install Dependencies", "安装依赖", "Установить зависимости"],
      confirmTexts: [
        "Install Dashboard dependencies now?",
        "现在安装 Dashboard 依赖吗？",
        "Установить зависимости Dashboard сейчас?",
      ],
    },
    {
      endpoint: "dashboard/build",
      buttonLabels: ["Build Page", "构建页面", "Собрать страницу"],
      confirmTexts: [
        "Build Dashboard production assets now?",
        "现在构建 Dashboard 生产产物吗？",
        "Собрать production-ресурсы Dashboard сейчас?",
      ],
    },
  ];

  for (const action of actions) {
    await assertConfirmationCancelsWithoutPost(
      page,
      async () => clickButtonByAnyName(page, action.buttonLabels),
      action.confirmTexts,
      action.endpoint
    );
  }
}

async function installBridge(page) {
  await page.addInitScript(() => {
    let nextSubscriptionId = 1;
    const timers = new Map();
    window.__memoraPostCalls = [];
    window.AstrBotPluginPage = {
      async apiGet(endpoint) {
        return window.__memoraBridgeOk(await window.__memoraBridgePayload(endpoint));
      },
      async apiPost(endpoint) {
        window.__memoraPostCalls.push(String(endpoint || "").replace(/^page\/?/, ""));
        return window.__memoraBridgeOk(await window.__memoraBridgePayload(endpoint));
      },
      subscribeSSE(_endpoint, handlers) {
        const id = `browser-smoke-${nextSubscriptionId++}`;
        const eventTimer = window.setTimeout(() => {
          handlers?.onMessage?.(JSON.stringify({
            event: "memory_created",
            data: { text: "browser smoke event" },
            ts: Date.now() / 1000,
          }));
        }, 100);
        const errorTimer = window.setTimeout(() => {
          handlers?.onError?.(new Error("browser smoke forced reconnect"));
        }, 250);
        timers.set(id, [eventTimer, errorTimer]);
        return id;
      },
      unsubscribeSSE(id) {
        for (const timer of timers.get(id) || []) window.clearTimeout(timer);
        timers.delete(id);
      },
    };
  });
  await page.exposeFunction("__memoraBridgePayload", bridgePayload);
  await page.exposeFunction("__memoraBridgeOk", ok);
}

async function getPostCalls(page) {
  return await page.evaluate(() => [...(window.__memoraPostCalls || [])]);
}

function collectPageErrors(page, errors) {
  page.on("console", (message) => {
    if (message.type() !== "error") return;
    const text = message.text();
    errors.push(`console.error: ${text}`);
  });
  page.on("pageerror", (error) => {
    errors.push(`pageerror: ${error.message}`);
  });
}

const { browser, label } = await launchBrowser();
const page = await browser.newPage({ viewport: { width: 1366, height: 900 } });
const errors = [];

collectPageErrors(page, errors);
await installBridge(page);

try {
  await page.goto(pathToFileURL(htmlPath).href, { waitUntil: "load" });
  await page.waitForSelector("#root > *", { timeout: 10_000 });
  const screenshotsDir = path.join(os.tmpdir(), "memora-dashboard-browser-smoke-screenshots");
  await mkdir(screenshotsDir, { recursive: true });
  const baselineResults = [];

  const routes = [
    ["数据预览", "#/preview", ["数据预览", "记忆增长", "模块资产", "活跃会话"], "preview.png"],
    ["知识图谱", "#/graph", "知识图谱", "graph.png"],
    ["记忆管理", "#/memory", "记忆管理", "memory.png"],
    ["系统概览", "#/system", ["系统概览", "运行观测", "Provider 状态"], "system.png"],
    ["黑话发现", "#/jargon", "黑话", "jargon.png"],
  ];

  for (const [navLabel, hash, expectedText, filename] of routes) {
    baselineResults.push(
      await clickSidebarNav(
        page,
        navLabel,
        hash,
        expectedText,
        path.join(screenshotsDir, filename)
      )
    );
  }

  baselineResults.push(
    await clickSidebarNav(
      page,
      "智能控制",
      "#/intelligence",
      ["智能控制台", "页面壳已就绪", "评测工作台", "private_basic", "group_context", "eval-smoke-latest"],
      path.join(screenshotsDir, "intelligence-evaluation.png")
    )
  );

  baselineResults.push(
    await runRecallTraceSmoke(
      page,
      path.join(screenshotsDir, "intelligence-trace.png")
    )
  );

  const intelligenceTabs = [
    [
      "诊断",
      "diagnostics",
      ["诊断", "82", "Index drift detected", "Index validator recommends a rebuild."],
      "intelligence-diagnostics.png",
    ],
    [
      "复核队列",
      "reviewQueue",
      ["复核队列", "mem-smoke-duplicate", "duplicate", "重复记忆"],
      "intelligence-review.png",
    ],
  ];

  for (const [tabLabel, tabId, expectedText, filename] of intelligenceTabs) {
    baselineResults.push(
      await clickIntelligenceTab(
        page,
        tabLabel,
        tabId,
        expectedText,
        path.join(screenshotsDir, filename)
      )
    );
  }

  const mobilePage = await browser.newPage({ viewport: { width: 390, height: 844 } });
  collectPageErrors(mobilePage, errors);
  await installBridge(mobilePage);
  await mobilePage.goto(pathToFileURL(htmlPath).href, { waitUntil: "load" });
  await mobilePage.waitForSelector("#root > *", { timeout: 10_000 });

  const mobileRoutes = [
    ["数据预览", "#/preview", ["数据预览", "记忆增长", "模块资产"], "mobile-preview.png"],
    ["系统概览", "#/system", ["系统概览", "运行观测", "Provider 状态"], "mobile-system.png"],
    ["黑话发现", "#/jargon", "黑话", "mobile-jargon.png"],
  ];

  for (const [navLabel, hash, expectedText, filename] of mobileRoutes) {
    baselineResults.push(
      await clickMobileNav(
        mobilePage,
        navLabel,
        hash,
        expectedText,
        path.join(screenshotsDir, filename)
      )
    );
  }

  await mobilePage.close();

  const widePage = await browser.newPage({ viewport: { width: 2048, height: 1152 } });
  collectPageErrors(widePage, errors);
  await installBridge(widePage);
  await widePage.goto(pathToFileURL(htmlPath).href, { waitUntil: "load" });
  await widePage.waitForSelector("#root > *", { timeout: 10_000 });

  const wideRoutes = [
    ["#/preview", ["数据预览", "记忆增长", "记忆构成", "模块资产", "group-smoke-primary"], "wide-preview.png", "wide-preview"],
    ["#/learning", ["自主学习", "83.0%", "retrieval_weight", "Formal greeting"], "wide-learning.png", "wide-learning"],
    ["#/affection", ["好感度与情绪", "群聊今天的氛围很积极。", "alice"], "wide-affection.png", "wide-affection"],
    ["#/social", ["社交关系", "alice", "bob", "pair", "project"], "wide-social.png", "wide-social"],
  ];

  for (const [hash, expectedText, filename, routeLabel] of wideRoutes) {
    baselineResults.push(
      await captureRoute(
        widePage,
        hash,
        expectedText,
        path.join(screenshotsDir, filename),
        routeLabel
      )
    );
  }

  await widePage.close();

  baselineResults.push(
    await clickSidebarNav(
      page,
      "系统概览",
      "#/system",
      ["系统概览", "运行观测", "Provider 状态"],
      path.join(screenshotsDir, "system-confirmation.png")
    )
  );
  await assertBackupDestructiveConfirmations(page);
  await assertHighImpactConfirmation(page);

  await page.getByRole("button", { name: "切换主题" }).click();
  await page.waitForFunction(
    () => document.documentElement.getAttribute("data-theme") === "dark",
    undefined,
    { timeout: 5_000 }
  );
  await page.locator('[data-slot="page-content"]').last().evaluate((element) => {
    element.scrollTo({ top: 0, left: 0 });
  });
  baselineResults.push(
    await captureBaselineScreenshot(
      page,
      path.join(screenshotsDir, "dark-system.png"),
      "dark-system"
    )
  );

  baselineResults.push(
    await captureRoute(
      page,
      "#/preview",
      ["数据预览", "记忆增长", "记忆构成", "模块资产"],
      path.join(screenshotsDir, "dark-preview.png"),
      "dark-preview"
    )
  );

  await writeFile(
    path.join(screenshotsDir, "screenshot-baseline-manifest.json"),
    JSON.stringify(
      {
        generatedAt: new Date().toISOString(),
        browser: label,
        baselines: SCREENSHOT_BASELINES,
        screenshots: baselineResults,
      },
      null,
      2
    ),
    "utf8"
  );

  if (errors.length > 0) {
    throw new Error(`Dashboard browser smoke reported errors:\n${errors.join("\n")}`);
  }

  console.log(`Dashboard browser smoke passed with ${label}.`);
} finally {
  await browser.close();
}
