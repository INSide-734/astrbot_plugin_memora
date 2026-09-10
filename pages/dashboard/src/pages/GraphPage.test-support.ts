import { cleanup } from "@testing-library/react";
import { vi } from "vitest";

interface GraphPointerEvent {
  target?: { id?: string };
  targetType?: "node" | "edge" | "combo" | "canvas";
}

type GraphEventHandler = (event?: GraphPointerEvent) => void;

export interface ClickSelectBehaviorMock {
  type: "click-select";
  state: string;
  animation?: boolean;
  enable?: boolean | ((event: GraphPointerEvent) => boolean);
  onClick?: GraphEventHandler;
}

interface GraphInstanceMock {
  config: Record<string, unknown>;
  handlers: Map<string, GraphEventHandler>;
  setData: ReturnType<typeof vi.fn>;
  setOptions: ReturnType<typeof vi.fn>;
  draw: ReturnType<typeof vi.fn>;
  render: ReturnType<typeof vi.fn>;
  destroy: ReturnType<typeof vi.fn>;
  focusElement: ReturnType<typeof vi.fn>;
  setElementVisibility: ReturnType<typeof vi.fn>;
  setElementState: ReturnType<typeof vi.fn>;
  getElementState: ReturnType<typeof vi.fn>;
  getZoom: ReturnType<typeof vi.fn>;
  on: (eventName: string, handler: GraphEventHandler) => void;
  emit: (eventName: string, event?: GraphPointerEvent) => void;
}

export interface BridgeMock {
  apiGet: ReturnType<typeof vi.fn>;
  apiPost: ReturnType<typeof vi.fn>;
  getLocale?: ReturnType<typeof vi.fn>;
  getI18n?: ReturnType<typeof vi.fn>;
  t?: ReturnType<typeof vi.fn>;
}

/** 创建可由测试显式完成或拒绝的 Promise。 */
export function deferred<T>(): {
  promise: Promise<T>;
  resolve: (value: T) => void;
  reject: (reason?: unknown) => void;
} {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

/** 返回当前测试进程中的 G6 模拟实例集合。 */
export function getGraphMockState(): { instances: GraphInstanceMock[] } {
  const globalState = globalThis as typeof globalThis & {
    __graphMockState__?: { instances: GraphInstanceMock[] };
  };
  if (!globalState.__graphMockState__) {
    globalState.__graphMockState__ = { instances: [] };
  }
  return globalState.__graphMockState__;
}

/** 构造与 Page API envelope 一致的成功响应。 */
export function ok<T>(data: T): { status: "ok"; data: T } {
  return { status: "ok", data };
}

/** 安装 G6 模拟并重新导入 GraphPage。 */
export async function loadGraphPage(): Promise<typeof import("./GraphPage")> {
  vi.resetModules();
  getGraphMockState().instances.length = 0;

  vi.doMock("@antv/g6", () => ({
    Graph: class GraphMock {
      config: Record<string, unknown>;
      handlers = new Map<string, GraphEventHandler>();
      setData = vi.fn();
      setOptions = vi.fn();
      draw = vi.fn().mockResolvedValue(undefined);
      render = vi.fn().mockResolvedValue(undefined);
      destroy = vi.fn();
      focusElement = vi.fn().mockResolvedValue(undefined);
      setElementVisibility = vi.fn().mockResolvedValue(undefined);
      stateMap = new Map<string, string[]>();
      setElementState = vi.fn(async (id: string, states: string[]) => {
        this.stateMap.set(id, [...states]);
      });
      getElementState = vi.fn((id: string) => this.stateMap.get(id) ?? []);
      getZoom = vi.fn().mockReturnValue(1.75);

      /** 保存配置并登记当前 G6 模拟实例。 */
      constructor(config: Record<string, unknown>) {
        this.config = config;
        getGraphMockState().instances.push(this);
      }

      /** 注册图谱事件回调。 */
      on(eventName: string, handler: GraphEventHandler): void {
        this.handlers.set(eventName, handler);
      }

      /** 模拟 G6 指针事件、选择状态变化及订阅回调。 */
      emit(eventName: string, event: GraphPointerEvent = {}): void {
        const clickSelect = (this.config.behaviors as unknown[] | undefined)?.find(
          (behavior): behavior is ClickSelectBehaviorMock => (
            typeof behavior === "object"
            && behavior !== null
            && (behavior as ClickSelectBehaviorMock).type === "click-select"
          ),
        );
        const targetType = event.targetType
          ?? (eventName.split(":", 1)[0] as GraphPointerEvent["targetType"]);
        const pointerEvent = { ...event, targetType };
        const enabled = clickSelect?.enable === undefined
          || clickSelect.enable === true
          || (typeof clickSelect.enable === "function" && clickSelect.enable(pointerEvent));
        if (clickSelect && enabled && (targetType === "node" || targetType === "edge" || targetType === "combo")) {
          const id = event.target?.id;
          if (id) {
            const state = clickSelect.state;
            const current = this.getElementState(id);
            if (current.includes(state)) {
              this.stateMap.set(id, current.filter((item) => item !== state));
            } else {
              for (const [elementId, states] of this.stateMap) {
                this.stateMap.set(elementId, states.filter((item) => item !== state));
              }
              this.stateMap.set(id, [...current, state]);
            }
            clickSelect.onClick?.(pointerEvent);
          }
        } else if (clickSelect && enabled && targetType === "canvas") {
          for (const [elementId, states] of this.stateMap) {
            this.stateMap.set(
              elementId,
              states.filter((item) => item !== clickSelect.state),
            );
          }
          clickSelect.onClick?.(pointerEvent);
        }
        this.handlers.get(eventName)?.(event);
      }
    },
  }));

  return import("./GraphPage");
}

/** 安装每条 GraphPage 测试需要的宿主 Bridge 与浏览器能力。 */
export function setupGraphPageTestEnvironment(): {
  bridge: BridgeMock;
  showToast: ReturnType<typeof vi.fn>;
} {
  document.documentElement.dataset.theme = "light";
  Object.defineProperty(window, "matchMedia", {
    configurable: true,
    writable: true,
    value: vi.fn().mockReturnValue({ matches: false }),
  });

  const bridge: BridgeMock = {
    apiGet: vi.fn(),
    apiPost: vi.fn(),
    getLocale: vi.fn().mockReturnValue("en-US"),
    getI18n: vi.fn().mockReturnValue({}),
    t: vi.fn((key: string) => key),
  };
  const showToast = vi.fn();

  Object.defineProperty(window, "AstrBotPluginPage", {
    configurable: true,
    value: bridge,
  });
  Object.defineProperty(document, "fullscreenElement", {
    configurable: true,
    writable: true,
    value: null,
  });
  Object.defineProperty(HTMLElement.prototype, "requestFullscreen", {
    configurable: true,
    writable: true,
    value: vi.fn().mockResolvedValue(undefined),
  });
  Object.defineProperty(document, "exitFullscreen", {
    configurable: true,
    writable: true,
    value: vi.fn().mockResolvedValue(undefined),
  });

  return { bridge, showToast };
}

/** 清理 GraphPage 测试挂载、模拟和宿主 Bridge。 */
export function cleanupGraphPageTestEnvironment(): void {
  cleanup();
  vi.restoreAllMocks();
  vi.doUnmock("@antv/g6");
  Object.defineProperty(window, "AstrBotPluginPage", {
    configurable: true,
    value: undefined,
  });
}
