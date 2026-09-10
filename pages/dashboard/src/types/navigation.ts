import type { InjectionWorkbenchTab } from "./injection";
import type { IntelligenceTabId } from "./intelligence";

export interface ConfigNavigationTarget {
  requestId: number;
  path: string;
  query: string;
}

export interface EntityNavigationTarget {
  requestId: number;
  id: string;
}

export interface InjectionNavigationTarget {
  requestId: number;
  tab: InjectionWorkbenchTab;
  decisionId?: string;
}

export interface IntelligenceNavigationTarget {
  requestId: number;
  tab: IntelligenceTabId;
  traceId?: string;
}

export interface PageNavigationIntent {
  configTarget?: ConfigNavigationTarget;
  entityTarget?: EntityNavigationTarget;
  injectionTarget?: InjectionNavigationTarget;
  intelligenceTarget?: IntelligenceNavigationTarget;
}
