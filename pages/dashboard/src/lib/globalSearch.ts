import type { Translate } from "@/lib/i18n";
import { DASHBOARD_NAVIGATION } from "@/lib/navigation";
import type { PageId } from "@/types";

export type LocalSearchKind = "page" | "config-group" | "config-field";

export interface LocalSearchEntry {
  id: string;
  kind: LocalSearchKind;
  title: string;
  subtitle: string;
  path: string;
  order: number;
  searchable: string;
  normalizedTitle: string;
  normalizedPath: string;
  normalizedDescription: string;
  normalizedHint: string;
  normalizedOptions: string;
  page?: PageId;
  parentPath?: string | null;
}

export interface LimitedSearchResult<T> {
  items: T[];
  total: number;
}

export interface HighlightSegment {
  text: string;
  matched: boolean;
}

const CONFIG_LEAF_TYPES = new Set(["bool", "string", "text", "int", "float"]);

function normalize(value: unknown): string {
  return String(value ?? "").trim().toLocaleLowerCase("en-US");
}

function queryTokens(query: unknown): string[] {
  return [...new Set(normalize(query).split(/\s+/).filter(Boolean))];
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function displayText(value: unknown): string {
  return String(value ?? "").trim();
}

function makeEntry(
  entry: Omit<
    LocalSearchEntry,
    | "searchable"
    | "normalizedTitle"
    | "normalizedPath"
    | "normalizedDescription"
    | "normalizedHint"
    | "normalizedOptions"
  >,
  description: string,
  hint: string,
  options: readonly string[],
): LocalSearchEntry {
  const normalizedTitle = normalize(entry.title);
  const normalizedPath = normalize(entry.path);
  const normalizedDescription = normalize(description);
  const normalizedHint = normalize(hint);
  const normalizedOptions = normalize(options.join(" "));

  return {
    ...entry,
    searchable: normalize([
      entry.path,
      entry.title,
      description,
      hint,
      ...options,
    ].join(" ")),
    normalizedTitle,
    normalizedPath,
    normalizedDescription,
    normalizedHint,
    normalizedOptions,
  };
}

export function buildConfigSearchEntries(schema: unknown): LocalSearchEntry[] {
  if (!isRecord(schema)) return [];

  const entries: LocalSearchEntry[] = [];
  let order = 0;

  const visit = (
    node: unknown,
    path: string,
    parentPath: string | null,
  ): void => {
    if (!isRecord(node) || node.invisible === true) return;

    const nodeType = node.type;
    const isGroup = nodeType === "object";
    let children: Record<string, unknown> | undefined;
    if (isGroup) {
      if (!isRecord(node.items)) return;
      children = node.items;
    } else if (typeof nodeType !== "string" || !CONFIG_LEAF_TYPES.has(nodeType)) {
      return;
    }

    const description = displayText(node.description);
    const hint = displayText(node.hint);
    const options = Array.isArray(node.options) ? node.options.map(String) : [];
    const pathSegments = path.split(".");
    const title = description || pathSegments[pathSegments.length - 1] || path;

    entries.push(makeEntry({
      id: `config:${path}`,
      kind: isGroup ? "config-group" : "config-field",
      title,
      subtitle: hint || path,
      path,
      order,
      parentPath,
    }, description, hint, options));
    order += 1;

    if (!children) return;
    for (const [key, child] of Object.entries(children)) {
      visit(child, `${path}.${key}`, path);
    }
  };

  for (const [path, node] of Object.entries(schema)) {
    visit(node, path, null);
  }

  return entries;
}

export function buildPageSearchEntries(t: Translate): LocalSearchEntry[] {
  let order = 0;

  return DASHBOARD_NAVIGATION.flatMap((group) => {
    const subtitle = displayText(t(group.labelKey));
    return group.items.map((item) => {
      const title = displayText(t(item.labelKey));
      const entry = makeEntry({
        id: `page:${item.id}`,
        kind: "page",
        title,
        subtitle,
        path: item.id,
        order,
        page: item.id,
      }, subtitle, "", []);
      order += 1;
      return entry;
    });
  });
}

function entryRank(entry: LocalSearchEntry, query: string): number {
  if (entry.normalizedTitle === query || entry.normalizedPath === query) return 700;
  if (entry.normalizedTitle.startsWith(query) || entry.normalizedPath.startsWith(query)) return 600;
  if (entry.normalizedTitle.includes(query)) return 500;
  if (entry.normalizedPath.includes(query)) return 400;
  if (entry.normalizedDescription.includes(query)) return 300;
  if (entry.normalizedHint.includes(query)) return 200;
  if (entry.normalizedOptions.includes(query)) return 100;
  return 0;
}

function searchKindRank(kind: LocalSearchKind): number {
  return kind === "page" ? 0 : 1;
}

function compareOrdinal(left: string, right: string): number {
  if (left < right) return -1;
  if (left > right) return 1;
  return 0;
}

export function searchLocalEntries<T extends LocalSearchEntry>(
  entries: readonly T[],
  query: unknown,
  limit: number,
): LimitedSearchResult<T> {
  const tokens = queryTokens(query);
  if (tokens.length === 0) return { items: [], total: 0 };
  const rankingQuery = tokens.join(" ");

  const matches = entries
    .filter((entry) => {
      const searchable = normalize(entry.searchable);
      return tokens.every((token) => searchable.includes(token));
    })
    .map((entry) => ({ entry, rank: entryRank(entry, rankingQuery) }))
    .sort((left, right) => {
      const rankDifference = right.rank - left.rank;
      if (rankDifference !== 0) return rankDifference;

      const kindDifference =
        searchKindRank(left.entry.kind) - searchKindRank(right.entry.kind);
      if (kindDifference !== 0) return kindDifference;

      if (left.entry.kind === "page") {
        const orderDifference = left.entry.order - right.entry.order;
        if (orderDifference !== 0) return orderDifference;
      }

      const pathDifference = compareOrdinal(left.entry.path, right.entry.path);
      return pathDifference || compareOrdinal(left.entry.id, right.entry.id);
    });

  const displayLimit = Number.isFinite(limit) ? Math.max(0, Math.floor(limit)) : 0;
  return {
    items: matches.slice(0, displayLimit).map(({ entry }) => entry),
    total: matches.length,
  };
}

function escapeRegularExpression(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

export function highlightSegments(text: unknown, query: unknown): HighlightSegment[] {
  const source = String(text ?? "");
  const tokens = queryTokens(query).sort(
    (left, right) => right.length - left.length || left.localeCompare(right),
  );
  if (!source || tokens.length === 0) {
    return source ? [{ text: source, matched: false }] : [];
  }

  const tokenSet = new Set(tokens);
  const matcher = new RegExp(`(${tokens.map(escapeRegularExpression).join("|")})`, "gi");
  return source
    .split(matcher)
    .filter(Boolean)
    .map((segment) => ({
      text: segment,
      matched: tokenSet.has(normalize(segment)),
    }));
}
