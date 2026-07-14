import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { ConfigSchemaData } from "@/types/config";
import { EN_MAP, RU_MAP } from "../../mock";
import { SearchBar } from "./SearchBar";

interface BridgeMock {
  apiGet: ReturnType<typeof vi.fn>;
  apiPost: ReturnType<typeof vi.fn>;
  getLocale?: ReturnType<typeof vi.fn>;
  getI18n?: ReturnType<typeof vi.fn>;
  t?: ReturnType<typeof vi.fn>;
}

interface Deferred<T> {
  promise: Promise<T>;
  resolve: (value: T) => void;
  reject: (reason?: unknown) => void;
}

const SEARCH_SCHEMA: ConfigSchemaData = {
  plugin_name: "astrbot_plugin_memora",
  schema: {
    provider_settings: {
      type: "object",
      description: "Provider settings",
      items: {
        llm_provider_id: {
          type: "string",
          description: "LLM provider",
          hint: "Provider used for memory extraction",
        },
        embedding_provider_id: {
          type: "string",
          description: "Embedding provider",
          hint: "Provider used for vector search",
        },
      },
    },
  },
  provider_options: { llm: [], embedding: [] },
  capabilities: { hot_reload: true },
};

function deferred<T>(): Deferred<T> {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

function schemaSuccess(): ApiResponse {
  return { status: "ok", data: SEARCH_SCHEMA };
}

function memoryResponse(
  content: string,
  id: string | number = 1,
  total = 1,
): ApiResponse {
  return {
    status: "ok",
    data: { items: [{ id, content, importance: 0.7 }], total },
  };
}

function emptyRemoteResponse(endpoint: string): ApiResponse {
  if (endpoint === "page/memories") {
    return { status: "ok", data: { items: [], total: 0 } };
  }
  if (endpoint === "page/knowledge/search") {
    return { status: "ok", data: { entries: [], total: 0 } };
  }
  if (endpoint === "page/notes/search") {
    return { status: "ok", data: { notes: [], total: 0 } };
  }
  throw new Error(`Unexpected remote search endpoint: ${endpoint}`);
}

function openSearch(): void {
  fireEvent.click(screen.getByRole("button", { name: "Search..." }));
}

function changeSearch(value: string): void {
  fireEvent.change(screen.getByRole("combobox", { name: "Global search" }), {
    target: { value },
  });
}

function remoteSearchCalls(apiGet: ReturnType<typeof vi.fn>): unknown[][] {
  return apiGet.mock.calls.filter(
    (call) => call[0] !== "page/config/schema",
  );
}

async function flushPromises(): Promise<void> {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
}

async function advanceSearchDebounce(): Promise<void> {
  await act(async () => {
    vi.advanceTimersByTime(300);
    await Promise.resolve();
    await Promise.resolve();
  });
}

async function waitForSearchUi(assertion: () => void): Promise<void> {
  const pendingAssertion = waitFor(assertion);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(1);
  });
  await pendingAssertion;
}

describe("SearchBar", () => {
  let bridge: BridgeMock;
  let onNavigate: ReturnType<typeof vi.fn>;

  function installSchemaAndEmptyRemoteResponses(): void {
    bridge.apiGet.mockImplementation((endpoint: string) => {
      if (endpoint === "page/config/schema") return Promise.resolve(schemaSuccess());
      return Promise.resolve(emptyRemoteResponse(endpoint));
    });
  }

  function installSchemaAndRemoteResponses(): void {
    bridge.apiGet.mockImplementation((endpoint: string) => {
      if (endpoint === "page/config/schema") return Promise.resolve(schemaSuccess());
      if (endpoint === "page/memories") {
        return Promise.resolve({
          status: "ok",
          data: {
            items: [{
              id: "provider-memory",
              content: "Provider memory <img src=x onerror=alert(1)>",
              importance: 0.8,
            }],
            total: 9,
          },
        });
      }
      if (endpoint === "page/knowledge/search") {
        return Promise.resolve({
          status: "ok",
          data: {
            entries: [{
              entry_id: "provider-knowledge",
              title: "Memory provider knowledge",
            }],
            total: 1,
          },
        });
      }
      if (endpoint === "page/notes/search") {
        return Promise.resolve({
          status: "ok",
          data: {
            notes: [{ note_id: "provider-note", title: "Memory provider note" }],
            total: 1,
          },
        });
      }
      return Promise.resolve(emptyRemoteResponse(endpoint));
    });
  }

  beforeEach(() => {
    vi.useFakeTimers();

    bridge = {
      apiGet: vi.fn((endpoint: string) =>
        Promise.resolve(
          endpoint === "page/config/schema"
            ? schemaSuccess()
            : emptyRemoteResponse(endpoint),
        ),
      ),
      apiPost: vi.fn(),
      getLocale: vi.fn().mockReturnValue("en-US"),
      getI18n: vi.fn().mockReturnValue({}),
      t: vi.fn((key: string) => key),
    };
    onNavigate = vi.fn();

    Object.defineProperty(window, "AstrBotPluginPage", {
      configurable: true,
      value: bridge,
    });
  });

  afterEach(() => {
    cleanup();
    vi.clearAllTimers();
    vi.useRealTimers();
    vi.restoreAllMocks();
    Object.defineProperty(window, "AstrBotPluginPage", {
      configurable: true,
      value: undefined,
    });
  });

  it("opens the search overlay with Ctrl+K and closes it with Escape", async () => {
    render(<SearchBar onNavigate={onNavigate} />);

    fireEvent.keyDown(window, { key: "k", ctrlKey: true });

    expect(screen.getByRole("dialog", { name: "Global search" })).toBeTruthy();
    expect(screen.getByRole("combobox", { name: "Global search" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Close search" })).toBeTruthy();

    fireEvent.keyDown(window, { key: "Escape" });

    expect(screen.queryByRole("combobox", { name: "Global search" })).toBeNull();
  });

  it("toggles search with uppercase Ctrl+K and Meta+K shortcuts", () => {
    render(<SearchBar onNavigate={onNavigate} />);

    fireEvent.keyDown(window, { key: "K", ctrlKey: true });
    expect(screen.getByRole("dialog", { name: "Global search" })).toBeTruthy();

    fireEvent.keyDown(window, { key: "K", ctrlKey: true });
    expect(screen.queryByRole("dialog", { name: "Global search" })).toBeNull();

    fireEvent.keyDown(window, { key: "K", metaKey: true });
    expect(screen.getByRole("dialog", { name: "Global search" })).toBeTruthy();

    fireEvent.keyDown(window, { key: "K", metaKey: true });
    expect(screen.queryByRole("dialog", { name: "Global search" })).toBeNull();
  });

  it("performs debounced search across memories, knowledge, and notes", async () => {
    bridge.apiGet.mockImplementation((endpoint: string) => {
      if (endpoint === "page/config/schema") return Promise.resolve(schemaSuccess());
      if (endpoint === "page/memories") {
        return Promise.resolve(memoryResponse("Python memory", 1, 7));
      }
      if (endpoint === "page/knowledge/search") {
        return Promise.resolve({
          status: "ok",
          data: {
            entries: [
              { entry_id: "k-1", title: "Python knowledge", category: "fact" },
              { entry_id: "k-2", title: "Vendor knowledge", category: "vendor_category" },
            ],
            total: 2,
          },
        });
      }
      return Promise.resolve({
        status: "ok",
        data: {
          notes: [
            { note_id: "n-1", title: "Python note", status: "active" },
            { note_id: "n-2", title: "Vendor note", status: "vendor_status" },
          ],
          total: 2,
        },
      });
    });

    render(<SearchBar onNavigate={onNavigate} />);

    openSearch();
    changeSearch(" python ");

    await advanceSearchDebounce();

    expect(remoteSearchCalls(bridge.apiGet)).toEqual([
      ["page/memories", { keyword: "python", page_size: "5" }],
      ["page/knowledge/search", { query: "python", limit: "5" }],
      ["page/notes/search", { query: "python", limit: "5" }],
    ]);

    expect(screen.getByRole("option", { name: /Python memory/ })).toBeTruthy();
    expect(screen.getByRole("option", { name: /Python knowledge/ })).toBeTruthy();
    expect(screen.getByRole("option", { name: /Python note/ })).toBeTruthy();
    expect(screen.getByText("Showing 1/7")).toBeTruthy();
    expect(screen.getByText(EN_MAP["category.fact"])).toBeTruthy();
    expect(screen.getByText(EN_MAP["status.active"])).toBeTruthy();
    expect(screen.getByText("vendor_category")).toBeTruthy();
    expect(screen.getByText("vendor_status")).toBeTruthy();

    bridge.getLocale?.mockReturnValue("ru-RU");
    await act(async () => {
      window.dispatchEvent(new Event("languagechange"));
      await Promise.resolve();
    });

    expect(screen.getByText(RU_MAP["category.fact"])).toBeTruthy();
    expect(screen.getByText(RU_MAP["status.active"])).toBeTruthy();
    expect(screen.queryByText(EN_MAP["category.fact"])).toBeNull();
  });

  it.each([null, "", false])(
    "falls back to visible item count for invalid total %j",
    async (invalidTotal) => {
      bridge.apiGet.mockImplementation((endpoint: string) => {
        if (endpoint === "page/config/schema") return Promise.resolve(schemaSuccess());
        if (endpoint === "page/memories") {
          return Promise.resolve({
            status: "ok",
            data: {
              items: [{ id: 17, content: "Fallback memory", importance: 0.4 }],
              total: invalidTotal,
            },
          });
        }
        return Promise.resolve(emptyRemoteResponse(endpoint));
      });

      render(<SearchBar onNavigate={onNavigate} />);
      openSearch();
      changeSearch("fallback");

      await advanceSearchDebounce();

      expect(screen.getByRole("option", { name: /Fallback memory/ })).toBeTruthy();
      expect(screen.getByText("(1)")).toBeTruthy();
      expect(screen.queryByText("(0)")).toBeNull();
    },
  );

  it("keeps successful search groups when one endpoint returns an error", async () => {
    bridge.apiGet.mockImplementation((endpoint: string) => {
      if (endpoint === "page/config/schema") return Promise.resolve(schemaSuccess());
      if (endpoint === "page/memories") {
        return Promise.resolve(memoryResponse("Available memory", 7));
      }
      if (endpoint === "page/knowledge/search") {
        return Promise.resolve({ status: "error", message: "knowledge unavailable" });
      }
      return Promise.resolve({
        status: "ok",
        data: {
          notes: [{ note_id: "n-7", title: "Available note", status: "active" }],
          total: 1,
        },
      });
    });

    render(<SearchBar onNavigate={onNavigate} />);

    openSearch();
    changeSearch("available");

    await advanceSearchDebounce();

    expect(screen.getByRole("option", { name: /Available memory/ })).toBeTruthy();
    expect(screen.getByRole("option", { name: /Available note/ })).toBeTruthy();
  });

  it("continues to accept legacy bare-array search responses", async () => {
    bridge.apiGet.mockImplementation((endpoint: string) => {
      if (endpoint === "page/config/schema") return Promise.resolve(schemaSuccess());
      if (endpoint === "page/memories") {
        return Promise.resolve({
          status: "ok",
          data: [{ id: 21, content: "Legacy memory", importance: 0.5 }],
        });
      }
      if (endpoint === "page/knowledge/search") {
        return Promise.resolve({
          status: "ok",
          data: [{ entry_id: "legacy-k", title: "Legacy knowledge" }],
        });
      }
      return Promise.resolve({
        status: "ok",
        data: [{ note_id: "legacy-n", title: "Legacy note" }],
      });
    });

    render(<SearchBar onNavigate={onNavigate} />);
    openSearch();
    changeSearch("legacy");

    await advanceSearchDebounce();

    expect(screen.getByRole("option", { name: /Legacy memory/ })).toBeTruthy();
    expect(screen.getByRole("option", { name: /Legacy knowledge/ })).toBeTruthy();
    expect(screen.getByRole("option", { name: /Legacy note/ })).toBeTruthy();
  });

  it("filters malformed remote members without hiding valid search groups", async () => {
    bridge.apiGet.mockImplementation((endpoint: string) => {
      if (endpoint === "page/config/schema") return Promise.resolve(schemaSuccess());
      if (endpoint === "page/memories") {
        return Promise.resolve({
          status: "ok",
          data: { items: [null, "not-an-item", 42], total: 3 },
        });
      }
      if (endpoint === "page/knowledge/search") {
        return Promise.resolve({
          status: "ok",
          data: {
            entries: [{ entry_id: "valid-k", title: "Valid knowledge" }],
            total: 1,
          },
        });
      }
      return Promise.resolve(emptyRemoteResponse(endpoint));
    });

    render(<SearchBar onNavigate={onNavigate} />);
    openSearch();
    changeSearch("malformed");

    await advanceSearchDebounce();

    expect(screen.getByRole("option", { name: /Valid knowledge/ })).toBeTruthy();
    expect(screen.queryByText("not-an-item")).toBeNull();
  });

  it("navigates to the target page with an exact entity target when a result is clicked", async () => {
    bridge.apiGet.mockImplementation((endpoint: string) => {
      if (endpoint === "page/config/schema") return Promise.resolve(schemaSuccess());
      if (endpoint === "page/memories") {
        return Promise.resolve(memoryResponse("Memory hit", 11));
      }
      return Promise.resolve(emptyRemoteResponse(endpoint));
    });

    render(<SearchBar onNavigate={onNavigate} />);

    openSearch();
    changeSearch("memory");

    await advanceSearchDebounce();
    fireEvent.click(screen.getByRole("option", { name: /Memory hit/ }));

    expect(onNavigate).toHaveBeenCalledWith("memory", {
      entityTarget: {
        requestId: expect.any(Number),
        id: "11",
      },
    });
    expect(screen.queryByRole("combobox", { name: "Global search" })).toBeNull();
  });

  it("loads the config schema once across close and reopen after success", async () => {
    render(<SearchBar onNavigate={onNavigate} />);

    openSearch();
    await flushPromises();
    fireEvent.click(screen.getByRole("button", { name: "Close search" }));
    openSearch();
    await flushPromises();

    expect(
      bridge.apiGet.mock.calls.filter(
        (call) => call[0] === "page/config/schema",
      ),
    ).toHaveLength(1);
  });

  it("retries a failed config schema request only after the search is reopened", async () => {
    let schemaAttempts = 0;
    bridge.apiGet.mockImplementation((endpoint: string) => {
      if (endpoint !== "page/config/schema") {
        return Promise.resolve(emptyRemoteResponse(endpoint));
      }
      schemaAttempts += 1;
      return schemaAttempts === 1
        ? Promise.reject(new Error("schema unavailable"))
        : Promise.resolve(schemaSuccess());
    });

    render(<SearchBar onNavigate={onNavigate} />);

    openSearch();
    await flushPromises();
    await flushPromises();
    expect(schemaAttempts).toBe(1);

    fireEvent.click(screen.getByRole("button", { name: "Close search" }));
    openSearch();
    await flushPromises();

    expect(schemaAttempts).toBe(2);
  });

  it("deduplicates an in-flight schema request across close and reopen", async () => {
    const pendingSchema = deferred<ApiResponse>();
    bridge.apiGet.mockImplementation((endpoint: string) => {
      if (endpoint === "page/config/schema") return pendingSchema.promise;
      return Promise.resolve(emptyRemoteResponse(endpoint));
    });

    render(<SearchBar onNavigate={onNavigate} />);

    openSearch();
    await flushPromises();
    fireEvent.click(screen.getByRole("button", { name: "Close search" }));
    openSearch();
    await flushPromises();

    const schemaCalls = () =>
      bridge.apiGet.mock.calls.filter((call) => call[0] === "page/config/schema");
    expect(schemaCalls()).toHaveLength(1);

    await act(async () => {
      pendingSchema.resolve(schemaSuccess());
      await Promise.resolve();
      await Promise.resolve();
    });

    fireEvent.click(screen.getByRole("button", { name: "Close search" }));
    openSearch();
    await flushPromises();

    expect(schemaCalls()).toHaveLength(1);
  });

  it("does not let an older remote response overwrite a newer query", async () => {
    const olderMemory = deferred<ApiResponse>();
    bridge.apiGet.mockImplementation(
      (endpoint: string, params: Record<string, string> = {}) => {
        if (endpoint === "page/config/schema") return Promise.resolve(schemaSuccess());
        const requestQuery = params.keyword ?? params.query;
        if (endpoint === "page/memories" && requestQuery === "older") {
          return olderMemory.promise;
        }
        if (endpoint === "page/memories" && requestQuery === "newer") {
          return Promise.resolve(memoryResponse("Newer memory", 32));
        }
        return Promise.resolve(emptyRemoteResponse(endpoint));
      },
    );

    render(<SearchBar onNavigate={onNavigate} />);
    openSearch();
    const input = screen.getByRole("combobox", { name: "Global search" });

    fireEvent.change(input, { target: { value: "older" } });
    await advanceSearchDebounce();
    fireEvent.change(input, { target: { value: "newer" } });
    await advanceSearchDebounce();

    expect(screen.getByRole("option", { name: /Newer memory/ })).toBeTruthy();

    await act(async () => {
      olderMemory.resolve(memoryResponse("Older memory", 31));
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(screen.getByRole("option", { name: /Newer memory/ })).toBeTruthy();
    expect(screen.queryByRole("option", { name: /Older memory/ })).toBeNull();
  });

  it("cancels a pending debounce when search closes or unmounts", async () => {
    const view = render(<SearchBar onNavigate={onNavigate} />);
    openSearch();
    changeSearch("closing");

    fireEvent.click(screen.getByRole("button", { name: "Close search" }));
    await act(async () => {
      vi.advanceTimersByTime(300);
    });
    expect(remoteSearchCalls(bridge.apiGet)).toEqual([]);

    openSearch();
    changeSearch("unmounting");
    view.unmount();
    await act(async () => {
      vi.advanceTimersByTime(300);
    });

    expect(remoteSearchCalls(bridge.apiGet)).toEqual([]);
  });

  it("invalidates an in-flight query on Escape before a reopened search", async () => {
    const staleMemory = deferred<ApiResponse>();
    bridge.apiGet.mockImplementation(
      (endpoint: string, params: Record<string, string> = {}) => {
        if (endpoint === "page/config/schema") return Promise.resolve(schemaSuccess());
        const requestQuery = params.keyword ?? params.query;
        if (endpoint === "page/memories" && requestQuery === "stale") {
          return staleMemory.promise;
        }
        if (endpoint === "page/memories" && requestQuery === "fresh") {
          return Promise.resolve(memoryResponse("Fresh memory", 42));
        }
        return Promise.resolve(emptyRemoteResponse(endpoint));
      },
    );

    render(<SearchBar onNavigate={onNavigate} />);
    openSearch();
    changeSearch("stale");
    await advanceSearchDebounce();

    fireEvent.keyDown(window, { key: "Escape" });
    openSearch();
    changeSearch("fresh");
    await advanceSearchDebounce();

    expect(screen.getByRole("option", { name: /Fresh memory/ })).toBeTruthy();

    await act(async () => {
      staleMemory.resolve(memoryResponse("Stale memory", 41));
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(screen.getByRole("option", { name: /Fresh memory/ })).toBeTruthy();
    expect(screen.queryByRole("option", { name: /Stale memory/ })).toBeNull();
  });

  it("cycles active options with arrows and opens the active option with Enter", async () => {
    installSchemaAndEmptyRemoteResponses();
    render(<SearchBar onNavigate={onNavigate} />);
    openSearch();
    await flushPromises();
    changeSearch("provider");

    const input = screen.getByRole("combobox", { name: "Global search" });
    const options = screen.getAllByRole("option");
    expect(input.getAttribute("aria-controls")).toBe("global-search-results");
    expect(input.getAttribute("aria-expanded")).toBe("true");
    expect(input.getAttribute("aria-activedescendant")).toBe(options[0].id);
    expect(options[0].getAttribute("aria-selected")).toBe("true");

    fireEvent.keyDown(input, { key: "ArrowUp" });
    expect(options[options.length - 1].getAttribute("aria-selected")).toBe("true");

    fireEvent.keyDown(input, { key: "ArrowDown" });
    expect(options[0].getAttribute("aria-selected")).toBe("true");

    fireEvent.keyDown(input, { key: "Enter" });
    expect(onNavigate).toHaveBeenCalled();
  });

  it("scrolls the keyboard-active option into the result viewport", async () => {
    installSchemaAndEmptyRemoteResponses();
    render(<SearchBar onNavigate={onNavigate} />);
    openSearch();
    await flushPromises();
    changeSearch("provider");

    const input = screen.getByRole("combobox", { name: "Global search" });
    const options = screen.getAllByRole("option");
    const scrollIntoView = vi.fn();
    options[1].scrollIntoView = scrollIntoView;

    fireEvent.keyDown(input, { key: "ArrowDown" });
    await flushPromises();

    expect(scrollIntoView).toHaveBeenCalledWith({ block: "nearest" });
  });

  it("uses a constrained vertical result viewport inside the search dialog", async () => {
    installSchemaAndEmptyRemoteResponses();
    render(<SearchBar onNavigate={onNavigate} />);
    openSearch();
    await flushPromises();
    changeSearch("provider");

    const dialog = screen.getByRole("dialog", { name: "Global search" });
    const listbox = screen.getByRole("listbox", { name: "Search results" });

    expect(dialog.className).toContain("grid-rows-[auto_minmax(0,1fr)_auto]");
    expect(listbox.parentElement?.className).toContain("overflow-y-auto");
  });

  it("dispatches config results with the correct typed navigation", async () => {
    installSchemaAndEmptyRemoteResponses();
    render(<SearchBar onNavigate={onNavigate} />);
    openSearch();
    await flushPromises();
    changeSearch("LLM provider");

    fireEvent.click(screen.getByRole("option", { name: /LLM provider/ }));

    expect(onNavigate).toHaveBeenCalledWith("config", {
      configTarget: {
        requestId: expect.any(Number),
        path: "provider_settings.llm_provider_id",
        query: "LLM provider",
      },
    });
  });

  it("dispatches entity results with a repeatable exact navigation target", async () => {
    installSchemaAndRemoteResponses();
    render(<SearchBar onNavigate={onNavigate} />);
    openSearch();
    await flushPromises();
    changeSearch("provider");
    await advanceSearchDebounce();
    await flushPromises();

    fireEvent.click(screen.getByRole("option", { name: /Provider memory/ }));

    expect(onNavigate).toHaveBeenCalledWith("memory", {
      entityTarget: {
        requestId: expect.any(Number),
        id: "provider-memory",
      },
    });
  });

  it("renders safe highlighted segments and counted group labels", async () => {
    installSchemaAndRemoteResponses();
    render(<SearchBar onNavigate={onNavigate} />);
    openSearch();
    await flushPromises();
    changeSearch("provider");

    expect(
      screen.getAllByText(/provider/i, { selector: "mark" }).length,
    ).toBeGreaterThan(0);
    const configOption = screen.getByRole("option", {
      name: /Configuration.*LLM provider/,
    });
    expect(configOption.querySelectorAll("mark").length).toBeGreaterThan(1);

    await advanceSearchDebounce();
    await flushPromises();

    expect(screen.getByText("Showing 1/9")).toBeTruthy();
    const memoryOption = screen.getByRole("option", {
      name: /Provider memory <img src=x onerror=alert\(1\)>/,
    });
    expect(memoryOption.querySelector("img")).toBeNull();
    expect(memoryOption.textContent).toContain("<img src=x onerror=alert(1)>");
  });

  it("keeps the five result groups in the documented order", async () => {
    installSchemaAndRemoteResponses();
    render(<SearchBar onNavigate={onNavigate} />);
    openSearch();
    await flushPromises();
    changeSearch("memory");
    await advanceSearchDebounce();
    await flushPromises();

    const listbox = screen.getByRole("listbox", { name: "Search results" });
    expect(
      Array.from(listbox.querySelectorAll("[data-search-group]")).map((group) =>
        group.getAttribute("data-search-group"),
      ),
    ).toEqual(["page", "config", "memories", "knowledge", "notes"]);
  });

  it("does not expose results for a one-character trimmed query", async () => {
    installSchemaAndEmptyRemoteResponses();
    render(<SearchBar onNavigate={onNavigate} />);
    openSearch();
    await flushPromises();
    changeSearch(" p ");

    expect(screen.getByRole("listbox", { name: "Search results" })).toBeTruthy();
    expect(screen.queryAllByRole("option")).toHaveLength(0);
    expect(
      screen.getByText("Type at least 2 characters to start searching"),
    ).toBeTruthy();
  });

  it("synchronizes the active option with mouse movement", async () => {
    installSchemaAndEmptyRemoteResponses();
    render(<SearchBar onNavigate={onNavigate} />);
    openSearch();
    await flushPromises();
    changeSearch("provider");

    const options = screen.getAllByRole("option");
    expect(options.length).toBeGreaterThan(1);
    fireEvent.mouseMove(options[1]);

    expect(options[1].getAttribute("aria-selected")).toBe("true");
    expect(options[0].getAttribute("aria-selected")).toBe("false");
  });

  it("resets the active option when the query or visible result set changes", async () => {
    installSchemaAndRemoteResponses();
    render(<SearchBar onNavigate={onNavigate} />);
    openSearch();
    await flushPromises();
    changeSearch("provider");

    const input = screen.getByRole("combobox", { name: "Global search" });
    let options = screen.getAllByRole("option");
    fireEvent.keyDown(input, { key: "ArrowDown" });
    expect(options[1].getAttribute("aria-selected")).toBe("true");

    changeSearch("provider ");
    await waitForSearchUi(() => {
      expect(
        screen.getAllByRole("option")[0].getAttribute("aria-selected"),
      ).toBe("true");
    });
    fireEvent.keyDown(input, { key: "ArrowDown" });
    options = screen.getAllByRole("option");
    expect(options[1].getAttribute("aria-selected")).toBe("true");

    await advanceSearchDebounce();
    await flushPromises();
    await waitForSearchUi(() => {
      expect(
        screen.getAllByRole("option")[0].getAttribute("aria-selected"),
      ).toBe("true");
    });
  });
});
