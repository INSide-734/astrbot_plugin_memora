import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiRequestError } from "@/types/editing";
import type { JargonCandidate, JargonMeaning } from "@/types";
import { JargonPage } from "./JargonPage";

interface BridgeMock {
  apiGet: ReturnType<typeof vi.fn>;
  apiPost: ReturnType<typeof vi.fn>;
  getLocale: ReturnType<typeof vi.fn>;
  getI18n: ReturnType<typeof vi.fn>;
  t: ReturnType<typeof vi.fn>;
}

function ok<T>(data: T) {
  return { status: "ok", data };
}

const JARGON_SENTINELS: Record<string, string> = {
  "jargon.newJargon": "新建黑话哨兵",
  "jargon.createDescription": "创建黑话说明哨兵",
  "detail.create": "创建动作哨兵",
  "detail.edit": "编辑动作哨兵",
  "common.save": "保存动作哨兵",
  "common.delete": "删除动作哨兵",
  "common.close": "关闭动作哨兵",
  "common.cancel": "取消动作哨兵",
  "jargon.conflictTitle": "黑话冲突哨兵",
  "jargon.conflictDescription": "远端黑话已变更哨兵",
  "config.conflict.loadRemote": "加载远端哨兵",
  "jargon.reapplyLocal": "重用本地哨兵",
  "config.unsaved.title": "未保存黑话哨兵",
  "config.unsaved.description": "丢弃黑话草稿哨兵",
  "config.unsaved.keepEditing": "继续编辑哨兵",
  "config.unsaved.discard": "放弃草稿哨兵",
  "jargon.term": "术语字段哨兵",
  "jargon.groupId": "群组字段哨兵",
  "jargon.meaning": "含义字段哨兵",
  "table.confidence": "置信度字段哨兵",
  "jargon.meaningRequired": "含义必填哨兵",
  "jargon.confidenceRange": "置信度范围哨兵",
};

describe("JargonPage", () => {
  let bridge: BridgeMock;
  let showToast: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    bridge = {
      apiGet: vi.fn(),
      apiPost: vi.fn(),
      getLocale: vi.fn().mockReturnValue("en-US"),
      getI18n: vi.fn().mockReturnValue({}),
      t: vi.fn((key: string) => key),
    };
    showToast = vi.fn();

    Object.defineProperty(window, "AstrBotPluginPage", {
      configurable: true,
      value: bridge,
    });
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    Object.defineProperty(window, "AstrBotPluginPage", {
      configurable: true,
      value: undefined,
    });
  });

  it("loads stats and candidate rows, then confirms a candidate", async () => {
    bridge.apiGet.mockImplementation((path: string, params: Record<string, string>) => {
      if (path === "page/groups") {
        return Promise.resolve(ok({
          groups: [{ group_id: "group-1", message_count: 12 }],
        }));
      }
      if (path === "page/jargon/stats") {
        expect(params).toEqual({ group_id: "group-1" });
        return Promise.resolve(ok({
          total_terms: 3,
          candidate_count: 1,
          store_confirmed: 2,
        }));
      }
      if (path === "page/jargon/candidates") {
        expect(params).toEqual({ group_id: "group-1", limit: "50" });
        return Promise.resolve(ok({
          candidates: [
            {
              term: "$& $$",
              group_id: "group-1",
              score: 0.82,
              frequency: 7,
              unique_users: 3,
              idf_score: 0.1,
              burst_score: 0.2,
              concentration_score: 0.3,
              first_seen: 1,
              context_examples: ["$& $$ means a deployment shortcut"],
            },
          ],
        }));
      }
      if (path === "page/jargon/meanings") {
        return Promise.resolve(ok({ meanings: [] }));
      }
      return Promise.resolve(ok({}));
    });
    bridge.apiPost.mockResolvedValue(ok({}));

    render(<JargonPage showToast={showToast} />);

    expect(screen.getByRole("region").getAttribute("data-layout")).toBe("dense");

    expect(await screen.findByText("$& $$")).toBeTruthy();
    expect(screen.getByText("82%")).toBeTruthy();
    expect(screen.getByText("$& $$ means a deployment shortcut")).toBeTruthy();
    expect(screen.getAllByText("3").length).toBeGreaterThanOrEqual(1);
    const tabs = screen.getByRole("tablist", { name: "Jargon views" });
    expect(tabs).toBeTruthy();
    expect(screen.getByRole("tab", { name: "Candidates" }).getAttribute("aria-selected")).toBe("true");

    fireEvent.click(screen.getByTitle("Confirm"));

    await waitFor(() => {
      expect(bridge.apiPost).toHaveBeenCalledWith("page/jargon/confirm", {
        term: "$& $$",
        group_id: "group-1",
        confirmed: true,
      });
    });
    expect(showToast).toHaveBeenCalledWith("Confirmed '$& $$' as jargon");
  });

  it("switches to meanings and renders confirmed meaning rows", async () => {
    bridge.apiGet.mockImplementation((path: string, params: Record<string, string>) => {
      if (path === "page/groups") {
        return Promise.resolve(ok({
          groups: [{ group_id: "group-1", message_count: 12 }],
        }));
      }
      if (path === "page/jargon/stats") {
        return Promise.resolve(ok({ total_terms: 1, candidate_count: 0, store_confirmed: 1 }));
      }
      if (path === "page/jargon/candidates") {
        return Promise.resolve(ok({ candidates: [] }));
      }
      if (path === "page/jargon/meanings") {
        expect(params).toEqual({ group_id: "group-1", confirmed_only: "false" });
        return Promise.resolve(ok({
          meanings: [
            {
              term: "灰度",
              group_id: "group-1",
              meaning: "Gradual rollout",
              confidence: 0.91,
              is_jargon: true,
              is_confirmed: true,
              is_global: true,
              is_complete: true,
              count: 3,
              last_inference_count: 2,
              created_at: 1,
              updated_at: 2,
            },
          ],
        }));
      }
      return Promise.resolve(ok({}));
    });

    render(<JargonPage showToast={showToast} />);

    expect(await screen.findByText("No candidates found")).toBeTruthy();
    const candidatesTab = screen.getByRole("tab", { name: "Candidates" });
    const meaningsTab = screen.getByRole("tab", { name: "Confirmed" });
    const candidatesPanel = screen.getByRole("tabpanel", { name: "Candidates" });
    const meaningsPanel = document.getElementById("jargon-meanings-panel") as HTMLElement;

    expect(candidatesTab.id).toBe("jargon-candidates-tab");
    expect(meaningsTab.id).toBe("jargon-meanings-tab");
    expect(candidatesTab.getAttribute("aria-controls")).toBe(candidatesPanel.id);
    expect(candidatesPanel.getAttribute("aria-labelledby")).toBe(candidatesTab.id);
    expect(meaningsPanel.getAttribute("aria-labelledby")).toBe(meaningsTab.id);
    expect(candidatesTab.tabIndex).toBe(0);
    expect(meaningsTab.tabIndex).toBe(-1);
    expect(meaningsPanel.hidden).toBe(true);

    candidatesTab.focus();
    fireEvent.keyDown(candidatesTab, { key: "ArrowRight" });

    expect(document.activeElement).toBe(meaningsTab);
    expect(meaningsTab.getAttribute("aria-selected")).toBe("true");
    expect(candidatesPanel.hidden).toBe(true);
    expect(meaningsPanel.hidden).toBe(false);

    expect(await screen.findByText("灰度")).toBeTruthy();
    expect(screen.getByText("Gradual rollout")).toBeTruthy();
    expect(screen.getByText("91%")).toBeTruthy();
  });

  it("starts mining and reports API failures through the toast handler", async () => {
    bridge.apiGet.mockImplementation((path: string) => {
      if (path === "page/groups") {
        return Promise.resolve(ok({
          groups: [{ group_id: "group-1", message_count: 12 }],
        }));
      }
      if (path === "page/jargon/stats") {
        return Promise.resolve(ok({ total_terms: 0, candidate_count: 0, store_confirmed: 0 }));
      }
      if (path === "page/jargon/candidates") {
        return Promise.resolve(ok({ candidates: [] }));
      }
      return Promise.resolve(ok({}));
    });
    bridge.apiPost.mockRejectedValue(new Error("mining failed"));

    render(<JargonPage showToast={showToast} />);

    expect(await screen.findByText("No candidates found")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Discover" }));

    await waitFor(() => {
      expect(bridge.apiPost).toHaveBeenCalledWith("page/jargon/mine", {
        group_id: "group-1",
        limit: 5,
      });
      expect(showToast).toHaveBeenCalledWith("Error: mining failed", true);
    });
  });
  const meaning = (term: string, revision = "r1") => ({ term, group_id: "g1", meaning: term + " meaning", confidence: 0.5, is_jargon: true, is_confirmed: true, is_global: false, is_complete: true, count: 3, last_inference_count: 2, created_at: 10, updated_at: 11, context_examples: [term + " context"], revision });
  const setup = (meanings: JargonMeaning[] = [meaning("term")]) => { bridge.apiGet.mockImplementation((path: string) => { if (path === "page/groups") return Promise.resolve(ok({ groups: [{ group_id: "g1" }] })); if (path === "page/jargon/meanings") return Promise.resolve(ok({ meanings })); if (path === "page/jargon/candidates") return Promise.resolve(ok({ candidates: [] })); return Promise.resolve(ok({ total_terms: meanings.length, candidate_count: 2, store_confirmed: meanings.length })); }); };
  const showMeanings = async () => { render(<JargonPage showToast={showToast} />); fireEvent.click(await screen.findByRole("tab", { name: "Confirmed" })); await waitFor(() => expect(document.getElementById("jargon-meanings-panel")).toBeTruthy()); };

  it("New Jargon appears only in meanings context", async () => { setup(); render(<JargonPage showToast={showToast} />); await screen.findByText("No candidates found"); expect(screen.queryByRole("button", { name: /new jargon/i })).toBeNull(); fireEvent.click(screen.getByRole("tab", { name: "Confirmed" })); expect(await screen.findByRole("button", { name: /new jargon/i })).toBeTruthy(); });
  it("candidate confirm uses exact body and refetches all resources", async () => { setup(); bridge.apiGet.mockImplementation((path: string) => path === "page/groups" ? Promise.resolve(ok({ groups: [{ group_id: "g1" }] })) : path === "page/jargon/candidates" ? Promise.resolve(ok({ candidates: [{ term: "candidate", group_id: "g1", score: .8, frequency: 2, unique_users: 1, context_examples: [] }] })) : Promise.resolve(ok({ total_terms: 1, candidate_count: 1, store_confirmed: 0 }))); bridge.apiPost.mockResolvedValue(ok({})); render(<JargonPage showToast={showToast} />); await screen.findByText("candidate"); fireEvent.click(screen.getByTitle("Confirm")); await waitFor(() => expect(bridge.apiPost).toHaveBeenCalledWith("page/jargon/confirm", { term: "candidate", group_id: "g1", confirmed: true })); await waitFor(() => expect(bridge.apiGet.mock.calls.filter(([p]) => ["page/jargon/candidates", "page/jargon/meanings", "page/jargon/stats"].includes(p)).length).toBeGreaterThan(3)); });
  it("candidate reject uses exact false body", async () => { setup(); bridge.apiGet.mockImplementation((path: string) => path === "page/groups" ? Promise.resolve(ok({ groups: [{ group_id: "g1" }] })) : path === "page/jargon/candidates" ? Promise.resolve(ok({ candidates: [{ term: "reject-me", group_id: "g1", score: .4, frequency: 1, unique_users: 1, context_examples: [] }] })) : Promise.resolve(ok({ total_terms: 1, candidate_count: 1, store_confirmed: 0 }))); bridge.apiPost.mockResolvedValue(ok({})); render(<JargonPage showToast={showToast} />); await screen.findByText("reject-me"); fireEvent.click(screen.getByTitle("Reject")); await waitFor(() => expect(bridge.apiPost).toHaveBeenCalledWith("page/jargon/confirm", { term: "reject-me", group_id: "g1", confirmed: false })); });
  it("stored detail opens with context and remains view-only", async () => { setup(); await showMeanings(); fireEvent.click(screen.getByRole("button", { name: "view term" })); expect(await screen.findByText("term context")).toBeTruthy(); expect(screen.queryByRole("textbox", { name: "Meaning" })).toBeNull(); });
  it("create sends full draft defaults and returned entity revision", async () => { setup([]); bridge.apiPost.mockResolvedValue(ok({ entity: meaning("new", "r2"), revision: "r2" })); await showMeanings(); fireEvent.click(screen.getByRole("button", { name: /new jargon/i })); expect(screen.getByRole("switch", { name: "Is jargon" }).getAttribute("aria-checked")).toBe("true"); expect(screen.getByRole("switch", { name: "Is confirmed" }).getAttribute("aria-checked")).toBe("true"); expect(screen.getByRole("switch", { name: "Is global" }).getAttribute("aria-checked")).toBe("false"); fireEvent.change(screen.getByLabelText("Term"), { target: { value: "new" } }); fireEvent.change(screen.getByLabelText("Meaning"), { target: { value: "new meaning" } }); fireEvent.click(screen.getByRole("button", { name: "Create" })); await waitFor(() => expect(bridge.apiPost).toHaveBeenCalledWith("page/jargon/create", { term: "new", group_id: "g1", meaning: "new meaning", confidence: 0, is_jargon: true, is_confirmed: true, is_global: false })); });
  it("create failure retains full draft and visible error", async () => { setup([]); bridge.apiPost.mockRejectedValue(new Error("offline")); await showMeanings(); fireEvent.click(screen.getByRole("button", { name: /new jargon/i })); fireEvent.change(screen.getByLabelText("Term"), { target: { value: "draft" } }); fireEvent.change(screen.getByLabelText("Meaning"), { target: { value: "draft meaning" } }); fireEvent.click(screen.getByRole("button", { name: "Create" })); await waitFor(() => expect(screen.getByRole("alert").textContent).toContain("offline")); expect(screen.getByDisplayValue("draft")).toBeTruthy(); });
  it("edit identity is disabled and update body is exact", async () => { setup(); await showMeanings(); fireEvent.click(screen.getByRole("button", { name: "view term" })); fireEvent.click(screen.getByRole("button", { name: /edit/i })); expect((screen.getByLabelText("Term") as HTMLInputElement).disabled).toBe(true); expect((screen.getByLabelText("Group ID") as HTMLInputElement).disabled).toBe(true); fireEvent.change(screen.getByLabelText("Meaning"), { target: { value: "edited" } }); bridge.apiPost.mockResolvedValue(ok({ entity: meaning("term", "r2"), revision: "r2" })); fireEvent.click(screen.getByRole("button", { name: /save/i })); await waitFor(() => expect(bridge.apiPost).toHaveBeenCalledWith("page/jargon/update", { identity: { term: "term", group_id: "g1" }, changes: { meaning: "edited", confidence: .5, is_jargon: true, is_confirmed: true, is_global: false }, expected_revision: "r1" })); });
  it("update failure retains sheet draft and visible error", async () => { setup(); bridge.apiPost.mockRejectedValue(new Error("update offline")); await showMeanings(); fireEvent.click(screen.getByRole("button", { name: "view term" })); fireEvent.click(screen.getByRole("button", { name: /edit/i })); fireEvent.change(screen.getByLabelText("Meaning"), { target: { value: "kept" } }); fireEvent.click(screen.getByRole("button", { name: /save/i })); await waitFor(() => expect(screen.getByRole("alert").textContent).toContain("update offline")); expect(screen.getByDisplayValue("kept")).toBeTruthy(); });
  it("requires delete confirmation and exact identity/revision", async () => { setup(); await showMeanings(); fireEvent.click(screen.getByRole("button", { name: "view term" })); fireEvent.click(screen.getByRole("button", { name: /delete/i })); expect(bridge.apiPost).not.toHaveBeenCalledWith("page/jargon/delete", expect.anything()); fireEvent.click(screen.getByRole("button", { name: /^delete$/i })); await waitFor(() => expect(bridge.apiPost).toHaveBeenCalledWith("page/jargon/delete", { identity: { term: "term", group_id: "g1" }, expected_revision: "r1" })); });
  it("clears selection on tab change", async () => { setup(); await showMeanings(); fireEvent.click(screen.getByLabelText("select term")); fireEvent.click(screen.getByRole("tab", { name: "Candidates" })); expect(screen.queryByText("1 selected")).toBeNull(); });
  it("reports dirty ownership", async () => { setup(); const dirty = vi.fn(); render(<JargonPage showToast={showToast} onDirtyChange={dirty} />); fireEvent.click(await screen.findByRole("tab", { name: "Confirmed" })); fireEvent.click(screen.getByRole("button", { name: /new jargon/i })); fireEvent.change(screen.getByLabelText("Term"), { target: { value: "draft" } }); expect(dirty).toHaveBeenLastCalledWith(true); });
  it("preserves mining error toast behavior", async () => { bridge.apiGet.mockImplementation((path: string) => path === "page/groups" ? Promise.resolve(ok({ groups: [{ group_id: "g1" }] })) : Promise.resolve(ok({ candidates: [] }))); bridge.apiPost.mockRejectedValue(new Error("mining failed")); render(<JargonPage showToast={showToast} />); await screen.findByText("No candidates found"); fireEvent.click(screen.getByRole("button", { name: "Discover" })); await waitFor(() => expect(showToast).toHaveBeenCalledWith("Error: mining failed", true)); });

  it("ignores a late candidate response after switching to meanings", async () => {
    const oldCandidates = deferred<ReturnType<typeof ok>>();
    const newCandidates = deferred<ReturnType<typeof ok>>();
    const meanings = deferred<ReturnType<typeof ok>>();
    let candidateCalls = 0;
    bridge.apiGet.mockImplementation((path: string) => {
      if (path === "page/groups") return Promise.resolve(ok({ groups: [{ group_id: "g1" }] }));
      if (path === "page/jargon/candidates") return candidateCalls++ === 0 ? oldCandidates.promise : newCandidates.promise;
      if (path === "page/jargon/meanings") return meanings.promise;
      return Promise.resolve(ok({ total_terms: 0, candidate_count: 0, store_confirmed: 0 }));
    });
    render(<JargonPage showToast={showToast} />);
    await screen.findByRole("tab", { name: "Confirmed" });
    fireEvent.click(screen.getByRole("tab", { name: "Confirmed" }));
    meanings.resolve(ok({ meanings: [] }));
    expect(screen.getByRole("tabpanel", { name: "Confirmed" })).toBeTruthy();
    fireEvent.click(screen.getByRole("tab", { name: "Candidates" }));
    newCandidates.resolve(ok({ candidates: [{ term: "new", group_id: "g1", score: 0.8, frequency: 1, unique_users: 1, context_examples: [] }] }));
    await waitFor(() => expect(screen.getByText("new")).toBeTruthy());
    oldCandidates.resolve(ok({ candidates: [{ term: "old", group_id: "g1", score: 0.8, frequency: 1, unique_users: 1, context_examples: [] }] }));
    await waitFor(() => expect(document.getElementById("jargon-candidates-panel")?.textContent).toContain("new"));
    expect(document.getElementById("jargon-candidates-panel")?.textContent).not.toContain("old");
  });

  it("guards candidate confirm and reject writes synchronously and allows retry after failure", async () => {
    setup();
    bridge.apiGet.mockImplementation((path: string) => path === "page/groups"
      ? Promise.resolve(ok({ groups: [{ group_id: "g1" }] }))
      : path === "page/jargon/candidates"
        ? Promise.resolve(ok({ candidates: [{ term: "candidate", group_id: "g1", score: 0.8, frequency: 1, unique_users: 1, context_examples: [] }] }))
        : Promise.resolve(ok({ total_terms: 0, candidate_count: 1, store_confirmed: 0 })));
    const request = deferred<ReturnType<typeof ok>>();
    bridge.apiPost.mockReturnValueOnce(request.promise).mockRejectedValueOnce(new Error("confirm offline"));
    render(<JargonPage showToast={showToast} />);
    await screen.findByText("candidate");
    fireEvent.click(screen.getByTitle("Confirm"));
    fireEvent.click(screen.getByTitle("Reject"));
    await waitFor(() => expect(bridge.apiPost).toHaveBeenCalledTimes(1));
    expect((screen.getByTitle("Confirm") as HTMLButtonElement).disabled).toBe(true);
    request.resolve(ok({}));
    await waitFor(() => expect(showToast).toHaveBeenCalled());
    fireEvent.click(screen.getByTitle("Confirm"));
    await waitFor(() => expect(bridge.apiPost).toHaveBeenCalledTimes(2));
  });

  const fullMeaning = (term: string, revision = "rev-1", groupId = "g1") => ({
    term,
    group_id: groupId,
    meaning: `${term} meaning`,
    confidence: 0.75,
    is_jargon: true,
    is_confirmed: true,
    is_global: false,
    is_complete: true,
    count: 4,
    last_inference_count: 3,
    created_at: 100,
    updated_at: 200,
    context_examples: [`${term} in a complete context`],
    revision,
  });

  const fullCandidate = (term: string, groupId = "g1") => ({
    term,
    group_id: groupId,
    score: 0.88,
    frequency: 9,
    unique_users: 4,
    idf_score: 0.12,
    burst_score: 0.34,
    concentration_score: 0.56,
    first_seen: 100,
    context_examples: [`${term} candidate context`],
  });

  const configureCrudData = (nextMeanings: JargonMeaning[] = [fullMeaning("term")], nextCandidates: JargonCandidate[] = []) => {
    bridge.apiGet.mockImplementation((path: string) => {
      if (path === "page/groups") {
        return Promise.resolve(ok({ groups: [{ group_id: "g1", message_count: 12 }, { group_id: "g2", message_count: 8 }] }));
      }
      if (path === "page/jargon/meanings") return Promise.resolve(ok({ meanings: nextMeanings }));
      if (path === "page/jargon/candidates") return Promise.resolve(ok({ candidates: nextCandidates }));
      if (path === "page/jargon/stats") return Promise.resolve(ok({ total_terms: nextMeanings.length, candidate_count: nextCandidates.length, store_confirmed: nextMeanings.length }));
      return Promise.resolve(ok({}));
    });
  };

  const openMeaningsContext = async () => {
    render(<JargonPage showToast={showToast} />);
    fireEvent.click(await screen.findByRole("tab", { name: "Confirmed" }));
    await waitFor(() => expect(screen.getByRole("tabpanel", { name: "Confirmed" })).toBeTruthy());
  };

  const deferred = <T,>() => {
    let resolve!: (value: T) => void;
    let reject!: (reason?: unknown) => void;
    const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej; });
    return { promise, resolve, reject };
  };

  it("keeps both jargon tabs keyboard-activatable with linked ARIA panels", async () => {
    configureCrudData([], [fullCandidate("candidate")]);
    render(<JargonPage showToast={showToast} />);

    const candidates = await screen.findByRole("tab", { name: "Candidates" });
    const confirmed = screen.getByRole("tab", { name: "Confirmed" });
    const candidatePanel = document.getElementById("jargon-candidates-panel") as HTMLElement;
    const confirmedPanel = document.getElementById("jargon-meanings-panel") as HTMLElement;
    expect(candidatePanel).toBeTruthy();
    expect(confirmedPanel).toBeTruthy();

    candidates.focus();
    fireEvent.keyDown(candidates, { key: "ArrowRight" });
    expect(document.activeElement).toBe(confirmed);
    expect(confirmed.getAttribute("aria-selected")).toBe("true");
    expect(confirmedPanel.hidden).toBe(false);
    expect(candidatePanel.hidden).toBe(true);

    fireEvent.keyDown(confirmed, { key: "ArrowLeft" });
    expect(document.activeElement).toBe(candidates);
    expect(candidates.getAttribute("aria-selected")).toBe("true");
    expect(candidatePanel.hidden).toBe(false);
    expect(confirmedPanel.hidden).toBe(true);
    expect(candidates.getAttribute("aria-controls")).toBe(candidatePanel.id);
    expect(confirmed.getAttribute("aria-controls")).toBe(confirmedPanel.id);
    expect(candidatePanel.getAttribute("aria-labelledby")).toBe(candidates.id);
    expect(confirmedPanel.getAttribute("aria-labelledby")).toBe(confirmed.id);
  });

  it("creates a jargon entity, stays in meanings, and opens fresh returned data in view mode", async () => {
    configureCrudData([]);
    const created = fullMeaning("fresh-term", "rev-created");
    bridge.apiPost.mockResolvedValue(ok({ entity: created, revision: "rev-created" }));
    await openMeaningsContext();

    fireEvent.click(screen.getByRole("button", { name: /new jargon/i }));
    fireEvent.change(screen.getByLabelText("Term"), { target: { value: "fresh-term" } });
    fireEvent.change(screen.getByLabelText("Meaning"), { target: { value: "fresh meaning" } });
    fireEvent.click(screen.getByRole("button", { name: "Create" }));

    await waitFor(() => expect(bridge.apiPost).toHaveBeenCalledWith("page/jargon/create", {
      term: "fresh-term",
      group_id: "g1",
      meaning: "fresh meaning",
      confidence: 0,
      is_jargon: true,
      is_confirmed: true,
      is_global: false,
    }));
    expect(screen.getByRole("tab", { name: "Confirmed" }).getAttribute("aria-selected")).toBe("true");
    expect(await screen.findByText("fresh-term in a complete context")).toBeTruthy();
    expect(screen.queryByLabelText("Meaning")).toBeNull();
    expect(screen.getByText("rev-created")).toBeTruthy();
  });

  it("returns the entity editor to view mode with the update response revision", async () => {
    configureCrudData([fullMeaning("term")]);
    bridge.apiPost.mockResolvedValue(ok({ entity: fullMeaning("term", "rev-updated"), revision: "rev-updated" }));
    await openMeaningsContext();

    fireEvent.click(screen.getByRole("button", { name: "view term" }));
    fireEvent.click(screen.getByRole("button", { name: /edit/i }));
    fireEvent.change(screen.getByLabelText("Meaning"), { target: { value: "updated meaning" } });
    fireEvent.click(screen.getByRole("button", { name: /save/i }));

    await waitFor(() => expect(bridge.apiPost).toHaveBeenCalledWith("page/jargon/update", {
      identity: { term: "term", group_id: "g1" },
      changes: { meaning: "updated meaning", confidence: 0.75, is_jargon: true, is_confirmed: true, is_global: false },
      expected_revision: "rev-1",
    }));
    expect(await screen.findByText("rev-updated")).toBeTruthy();
    expect(screen.queryByLabelText("Meaning")).toBeNull();
  });

  it("retains the local draft on edit conflict and retries exactly against the latest revision", async () => {
    configureCrudData([fullMeaning("term")]);
    const remote = fullMeaning("term", "rev-remote");
    bridge.apiPost.mockRejectedValueOnce(new ApiRequestError("stale", "edit_conflict", {}, { current_entity: remote, current_revision: "rev-remote" }));
    bridge.apiPost.mockResolvedValueOnce(ok({ entity: fullMeaning("term", "rev-retried"), revision: "rev-retried" }));
    await openMeaningsContext();

    fireEvent.click(screen.getByRole("button", { name: "view term" }));
    fireEvent.click(screen.getByRole("button", { name: /edit/i }));
    fireEvent.change(screen.getByLabelText("Meaning"), { target: { value: "local draft" } });
    fireEvent.click(screen.getByRole("button", { name: /save/i }));

    const conflictDialog = await screen.findByRole("dialog", { name: /edit conflict/i });
    expect(within(conflictDialog).getByText("The entity was updated remotely.")).toBeTruthy();
    expect(screen.getByDisplayValue("local draft")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /reapply|retry/i }));

    await waitFor(() => expect(bridge.apiPost).toHaveBeenLastCalledWith("page/jargon/update", {
      identity: { term: "term", group_id: "g1" },
      changes: { meaning: "local draft", confidence: 0.75, is_jargon: true, is_confirmed: true, is_global: false },
      expected_revision: "rev-remote",
    }));
    expect(await screen.findByText("rev-retried")).toBeTruthy();
  });

  it.each([
    ["confirm", /^confirm selected$/i],
    ["unconfirm", /^unconfirm selected$/i],
    ["set_global", /^set global$/i],
    ["unset_global", /^unset global$/i],
    ["delete", /delete selected/i],
  ] as const)("sends the exact %s batch body with revisioned items and no params", async (action, buttonName) => {
    const items = [fullMeaning("one"), fullMeaning("two", "rev-2")];
    configureCrudData(items);
    bridge.apiPost.mockResolvedValue(ok({ total: 2, succeeded_count: 2, failed_count: 0, succeeded_ids: [{ term: "one", group_id: "g1" }, { term: "two", group_id: "g1" }], failures: [] }));
    await openMeaningsContext();
    fireEvent.click(screen.getByLabelText("select one"));
    fireEvent.click(screen.getByLabelText("select two"));
    fireEvent.click(screen.getByRole("button", { name: buttonName }));
    if (action === "delete") fireEvent.click(screen.getByRole("button", { name: /^delete$/i }));

    await waitFor(() => expect(bridge.apiPost).toHaveBeenCalledWith("page/jargon/batch", {
      action,
      items: [
        { identity: { term: "one", group_id: "g1" }, expected_revision: "rev-1" },
        { identity: { term: "two", group_id: "g1" }, expected_revision: "rev-2" },
      ],
    }));
  });

  it("retains only failed batch selections after a partial response", async () => {
    configureCrudData([fullMeaning("one"), fullMeaning("two", "rev-2")]);
    bridge.apiPost.mockResolvedValue(ok({
      total: 2,
      succeeded_count: 1,
      failed_count: 1,
      succeeded_ids: [{ term: "one", group_id: "g1" }],
      failures: [{
        identity: { term: "two", group_id: "g1" },
        code: "edit_conflict",
        message: "stale",
        current_entity: fullMeaning("two", "rev-remote"),
        current_revision: "rev-remote",
      }],
    }));
    await openMeaningsContext();
    fireEvent.click(screen.getByLabelText("select one"));
    fireEvent.click(screen.getByLabelText("select two"));
    fireEvent.click(screen.getByRole("button", { name: /^confirm selected$/i }));

    await waitFor(() => expect(screen.getByText("1 selected")).toBeTruthy());
    expect(screen.getByLabelText("select one").getAttribute("aria-checked")).toBe("false");
    expect(screen.getByLabelText("select two").getAttribute("aria-checked")).toBe("true");
  });

  it("rejects a malformed non-delete batch envelope without clearing selection", async () => {
    configureCrudData([fullMeaning("one")]);
    bridge.apiPost.mockResolvedValue(ok({}));
    await openMeaningsContext();
    fireEvent.click(screen.getByLabelText("select one"));
    fireEvent.click(screen.getByRole("button", { name: /^confirm selected$/i }));
    await waitFor(() => expect(showToast).toHaveBeenCalledWith(expect.stringMatching(/invalid|batch/i), true));
    expect(screen.getByText("1 selected")).toBeTruthy();
  });

  it("retains the create draft and reports a readable error for a malformed success envelope", async () => {
    configureCrudData([]);
    bridge.apiPost.mockResolvedValue(ok({ entity: null, revision: "" }));
    await openMeaningsContext();
    fireEvent.click(screen.getByRole("button", { name: /new jargon/i }));
    fireEvent.change(screen.getByLabelText("Term"), { target: { value: "draft" } });
    fireEvent.change(screen.getByLabelText("Meaning"), { target: { value: "draft meaning" } });
    fireEvent.click(screen.getByRole("button", { name: "Create" }));
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toMatch(/invalid|entity|revision/i);
    expect(screen.getByDisplayValue("draft")).toBeTruthy();
  });

  it("retains the edit draft and does not expose conflict actions for an invalid conflict envelope", async () => {
    configureCrudData([fullMeaning("term")]);
    bridge.apiPost.mockRejectedValue(new ApiRequestError("stale", "edit_conflict", {}, {
      current_entity: null,
      current_revision: "",
    }));
    await openMeaningsContext();
    fireEvent.click(screen.getByRole("button", { name: "view term" }));
    fireEvent.click(screen.getByRole("button", { name: /edit/i }));
    fireEvent.change(screen.getByLabelText("Meaning"), { target: { value: "local draft" } });
    fireEvent.click(screen.getByRole("button", { name: /save/i }));
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toMatch(/stale|conflict|invalid/i);
    expect(screen.getByDisplayValue("local draft")).toBeTruthy();
    expect(screen.queryByRole("button", { name: /load latest|reapply/i })).toBeNull();
  });

  it("retains the edit draft and reports a readable error for a malformed update success envelope", async () => {
    configureCrudData([fullMeaning("term")]);
    bridge.apiPost.mockResolvedValue(ok({ entity: null, revision: "" }));
    await openMeaningsContext();
    fireEvent.click(screen.getByRole("button", { name: "view term" }));
    fireEvent.click(screen.getByRole("button", { name: /edit/i }));
    fireEvent.change(screen.getByLabelText("Meaning"), { target: { value: "kept edit" } });
    fireEvent.click(screen.getByRole("button", { name: /save/i }));
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toMatch(/invalid|entity|revision/i);
    expect(screen.getByDisplayValue("kept edit")).toBeTruthy();
  });

  it("sends only one single-delete request when confirmation is double-clicked while pending", async () => {
    configureCrudData([fullMeaning("term")]);
    const request = deferred<ReturnType<typeof ok>>();
    bridge.apiPost.mockReturnValue(request.promise);
    await openMeaningsContext();
    fireEvent.click(screen.getByRole("button", { name: "view term" }));
    fireEvent.click(screen.getByRole("button", { name: /delete/i }));
    const confirm = await screen.findByRole("dialog", { name: /delete/i });
    const remove = within(confirm).getByRole("button", { name: /^delete$/i });
    fireEvent.click(remove);
    fireEvent.click(remove);
    await waitFor(() => expect(bridge.apiPost).toHaveBeenCalledTimes(1));
    request.resolve(ok({ deleted: true, identity: { term: "term", group_id: "g1" } }));
  });

  it("keeps jargon single-delete context after malformed success", async () => {
    configureCrudData([fullMeaning("term")]);
    bridge.apiPost.mockResolvedValue(ok({}));
    await openMeaningsContext();
    fireEvent.click(screen.getByRole("button", { name: "view term" }));
    fireEvent.click(screen.getByRole("button", { name: /delete/i }));
    const confirm = await screen.findByRole("dialog", { name: /delete jargon/i });
    fireEvent.click(within(confirm).getByRole("button", { name: /^delete$/i }));
    await waitFor(() => expect(showToast).toHaveBeenCalledWith(expect.stringMatching(/invalid|delete/i), true));
    expect(screen.getByRole("dialog", { name: /delete jargon/i })).toBeTruthy();
    expect(screen.getAllByText("term meaning").length).toBeGreaterThan(0);
  });

  it("keeps jargon batch-delete context and selection after malformed success", async () => {
    configureCrudData([fullMeaning("one"), fullMeaning("two", "rev-2")]);
    bridge.apiPost.mockResolvedValue(ok({ total: 2, failures: [] }));
    await openMeaningsContext();
    fireEvent.click(screen.getByLabelText("select one"));
    fireEvent.click(screen.getByLabelText("select two"));
    fireEvent.click(screen.getByRole("button", { name: /delete selected/i }));
    const confirm = await screen.findByRole("dialog", { name: /delete jargon/i });
    fireEvent.click(within(confirm).getByRole("button", { name: /^delete$/i }));
    await waitFor(() => expect(showToast).toHaveBeenCalledWith(expect.stringMatching(/invalid|batch/i), true));
    expect(screen.getByRole("dialog", { name: /delete jargon/i })).toBeTruthy();
    expect(screen.getByText("2 selected")).toBeTruthy();
  });

  it("freezes the selected jargon delete snapshot before confirmation", async () => {
    const items = [fullMeaning("one", "rev-one"), fullMeaning("two", "rev-two")];
    configureCrudData(items);
    bridge.apiPost.mockResolvedValue(ok({ failures: [] }));
    await openMeaningsContext();
    fireEvent.click(screen.getByLabelText("select one"));
    fireEvent.click(screen.getByLabelText("select two"));
    fireEvent.click(screen.getByRole("button", { name: /delete selected/i }));
    const confirm = await screen.findByRole("dialog", { name: /delete jargon/i });

    items[0].revision = "rev-mutated";
    fireEvent.click(screen.getByLabelText("select two"));
    fireEvent.click(within(confirm).getByRole("button", { name: /^delete$/i }));

    await waitFor(() => expect(bridge.apiPost).toHaveBeenCalledWith("page/jargon/batch", {
      action: "delete",
      items: [
        { identity: { term: "one", group_id: "g1" }, expected_revision: "rev-one" },
        { identity: { term: "two", group_id: "g1" }, expected_revision: "rev-two" },
      ],
    }));
  });

  it("removes a jargon row by identity when refresh replaces its object during delete", async () => {
    const original = fullMeaning("term", "rev-one");
    const replacement = { ...fullMeaning("term", "rev-two"), meaning: "replacement meaning" };
    configureCrudData([original]);
    const request = deferred<ReturnType<typeof ok>>();
    bridge.apiPost.mockReturnValue(request.promise);
    await openMeaningsContext();
    fireEvent.click(screen.getByRole("button", { name: "view term" }));
    fireEvent.click(screen.getByRole("button", { name: /delete/i }));
    const confirm = await screen.findByRole("dialog", { name: /delete jargon/i });
    fireEvent.click(within(confirm).getByRole("button", { name: /^delete$/i }));

    bridge.apiGet.mockImplementation((path: string) => path === "page/jargon/meanings"
      ? Promise.resolve(ok({ meanings: [replacement] }))
      : Promise.resolve(ok({ total_terms: 1, candidate_count: 0, store_confirmed: 1 })));
    const refreshButton = Array.from(document.querySelectorAll("button")).find((button) => /refresh/i.test(button.textContent ?? ""));
    fireEvent.click(refreshButton as HTMLElement);
    await screen.findByText("replacement meaning");
    request.resolve(ok({ deleted: true, identity: { term: "term", group_id: "g1" } }));

    await waitFor(() => expect(screen.queryByText("replacement meaning")).toBeNull());
  });

  it("clears stored-meaning selections when the group changes", async () => {
    configureCrudData([fullMeaning("term")]);
    await openMeaningsContext();
    fireEvent.click(screen.getByLabelText("select term"));
    expect(screen.getByText("1 selected")).toBeTruthy();

    fireEvent.click(screen.getByRole("combobox"));
    fireEvent.click(await screen.findByRole("option", { name: /g2/ }));
    await waitFor(() => expect(screen.queryByText("1 selected")).toBeNull());
  });

  it("reports create and edit dirty ownership independently and clears it when discarded", async () => {
    configureCrudData([]);
    const dirty = vi.fn();
    render(<JargonPage showToast={showToast} onDirtyChange={dirty} />);
    fireEvent.click(await screen.findByRole("tab", { name: "Confirmed" }));
    fireEvent.click(await screen.findByRole("button", { name: /new jargon/i }));
    fireEvent.change(screen.getByLabelText("Meaning"), { target: { value: "create draft" } });
    expect(dirty).toHaveBeenLastCalledWith(true);
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(dirty).toHaveBeenLastCalledWith(false);

    configureCrudData([fullMeaning("term")]);
    fireEvent.click(screen.getByRole("button", { name: /refresh/i }));
    fireEvent.click(await screen.findByRole("button", { name: "view term" }));
    fireEvent.click(screen.getByRole("button", { name: /edit/i }));
    fireEvent.change(screen.getByLabelText("Meaning"), { target: { value: "edit draft" } });
    expect(dirty).toHaveBeenLastCalledWith(true);
    fireEvent.click(screen.getByRole("button", { name: /cancel/i }));
    expect(dirty).toHaveBeenLastCalledWith(false);
  });

  it("prevents duplicate create requests and disables create dialog actions while pending", async () => {
    configureCrudData([]);
    const request = deferred<ReturnType<typeof ok>>();
    bridge.apiPost.mockReturnValue(request.promise);
    await openMeaningsContext();
    fireEvent.click(screen.getByRole("button", { name: /new jargon/i }));
    fireEvent.change(screen.getByLabelText("Term"), { target: { value: "pending" } });
    fireEvent.change(screen.getByLabelText("Meaning"), { target: { value: "pending meaning" } });
    const createButton = screen.getByRole("button", { name: "Create" });
    fireEvent.click(createButton);
    fireEvent.click(createButton);
    await waitFor(() => expect(bridge.apiPost).toHaveBeenCalledTimes(1));
    expect((createButton as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByRole("button", { name: "Close" }) as HTMLButtonElement).disabled).toBe(true);
    request.resolve(ok({ entity: fullMeaning("pending", "rev-pending"), revision: "rev-pending" }));
  });

  it("prevents duplicate update requests and disables edit close/cancel while pending", async () => {
    configureCrudData([fullMeaning("term")]);
    const request = deferred<ReturnType<typeof ok>>();
    bridge.apiPost.mockReturnValue(request.promise);
    await openMeaningsContext();
    fireEvent.click(screen.getByRole("button", { name: "view term" }));
    fireEvent.click(screen.getByRole("button", { name: /edit/i }));
    fireEvent.change(screen.getByLabelText("Meaning"), { target: { value: "pending edit" } });
    const saveButton = screen.getByRole("button", { name: /save/i });
    fireEvent.click(saveButton);
    fireEvent.click(saveButton);
    await waitFor(() => expect(bridge.apiPost).toHaveBeenCalledTimes(1));
    expect((saveButton as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByRole("button", { name: "Cancel" }) as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByRole("button", { name: "Close" }) as HTMLButtonElement).disabled).toBe(true);
    request.resolve(ok({ entity: fullMeaning("term", "rev-saved"), revision: "rev-saved" }));
  });

  it("prevents duplicate batch requests and disables the active batch action while pending", async () => {
    configureCrudData([fullMeaning("term")]);
    const request = deferred<ReturnType<typeof ok>>();
    bridge.apiPost.mockReturnValue(request.promise);
    await openMeaningsContext();
    fireEvent.click(screen.getByLabelText("select term"));
    fireEvent.click(screen.getByRole("button", { name: /^confirm selected$/i }));
    fireEvent.click(screen.getByRole("button", { name: /^confirm selected$/i }));
    expect(bridge.apiPost).toHaveBeenCalledTimes(1);
    expect((screen.getByRole("button", { name: /^confirm selected$/i }) as HTMLButtonElement).disabled).toBe(true);
    request.resolve(ok({ results: [{ identity: { term: "term", group_id: "g1" }, ok: true }] }));
  });

  it("consumes non-English translations in the jargon create form and unsaved decision", async () => {
    configureCrudData([fullMeaning("term")]);
    bridge.t.mockImplementation((key: string) => JARGON_SENTINELS[key] ?? key);
    await openMeaningsContext();

    fireEvent.click(await screen.findByRole("button", { name: JARGON_SENTINELS["jargon.newJargon"] }));
    const createDialog = screen.getByRole("dialog", { name: JARGON_SENTINELS["jargon.newJargon"] });
    expect(within(createDialog).getByText(JARGON_SENTINELS["jargon.createDescription"])).toBeTruthy();
    expect(within(createDialog).getByRole("button", { name: JARGON_SENTINELS["detail.create"] })).toBeTruthy();

    const termField = within(createDialog).getByLabelText(JARGON_SENTINELS["jargon.term"]);
    expect(within(createDialog).getByLabelText(JARGON_SENTINELS["jargon.groupId"])).toBeTruthy();
    const meaningField = within(createDialog).getByLabelText(JARGON_SENTINELS["jargon.meaning"]);
    const confidenceField = within(createDialog).getByLabelText(JARGON_SENTINELS["table.confidence"]);
    fireEvent.change(termField, { target: { value: "草稿" } });
    fireEvent.change(meaningField, { target: { value: "临时含义" } });
    fireEvent.change(meaningField, { target: { value: "" } });
    expect(within(createDialog).getAllByText(JARGON_SENTINELS["jargon.meaningRequired"]).length).toBeGreaterThan(0);
    fireEvent.change(confidenceField, { target: { value: "2" } });
    expect(within(createDialog).getAllByText(JARGON_SENTINELS["jargon.confidenceRange"]).length).toBeGreaterThan(0);

    fireEvent.click(within(createDialog).getByRole("button", { name: JARGON_SENTINELS["common.close"] }));
    const unsaved = await screen.findByRole("dialog", { name: JARGON_SENTINELS["config.unsaved.title"] });
    expect(within(unsaved).getByText(JARGON_SENTINELS["config.unsaved.description"])).toBeTruthy();
    expect(within(unsaved).getByRole("button", { name: JARGON_SENTINELS["config.unsaved.keepEditing"] })).toBeTruthy();
    expect(within(unsaved).getByRole("button", { name: JARGON_SENTINELS["config.unsaved.discard"] })).toBeTruthy();
  });

  it("consumes non-English translations for jargon edit, conflict, and delete actions", async () => {
    configureCrudData([fullMeaning("term")]);
    bridge.t.mockImplementation((key: string) => JARGON_SENTINELS[key] ?? key);
    bridge.apiPost.mockRejectedValueOnce(new ApiRequestError("stale", "edit_conflict", {}, {
      current_entity: fullMeaning("term", "rev-remote"),
      current_revision: "rev-remote",
    }));
    await openMeaningsContext();

    fireEvent.click(screen.getByRole("button", { name: "view term" }));
    fireEvent.click(screen.getByRole("button", { name: JARGON_SENTINELS["detail.edit"] }));
    fireEvent.change(screen.getByLabelText(JARGON_SENTINELS["jargon.meaning"]), { target: { value: "本地草稿" } });
    fireEvent.click(screen.getByRole("button", { name: JARGON_SENTINELS["common.save"] }));

    const conflict = await screen.findByRole("dialog", { name: JARGON_SENTINELS["jargon.conflictTitle"] });
    expect(within(conflict).getByText(JARGON_SENTINELS["jargon.conflictDescription"])).toBeTruthy();
    expect(within(conflict).getByRole("button", { name: JARGON_SENTINELS["config.conflict.loadRemote"] })).toBeTruthy();
    expect(within(conflict).getByRole("button", { name: JARGON_SENTINELS["jargon.reapplyLocal"] })).toBeTruthy();
    fireEvent.click(within(conflict).getByRole("button", { name: JARGON_SENTINELS["config.conflict.loadRemote"] }));
    fireEvent.click(screen.getByRole("button", { name: JARGON_SENTINELS["common.delete"] }));
    expect(await screen.findByRole("dialog", { name: JARGON_SENTINELS["common.delete"] })).toBeTruthy();
  });
});
