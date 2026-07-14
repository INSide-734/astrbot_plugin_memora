export interface ConfigNavigationTarget {
  requestId: number;
  path: string;
  query: string;
}

export interface EntityNavigationTarget {
  requestId: number;
  id: string;
}

export interface PageNavigationIntent {
  configTarget?: ConfigNavigationTarget;
  entityTarget?: EntityNavigationTarget;
}
