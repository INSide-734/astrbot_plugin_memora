import type { ConfigSchemaNode } from "@/types/config";

export interface ConfigSection {
  id: string;
  path: string;
  label: string;
  node: ConfigSchemaNode;
  dirtyCount: number;
}

interface ConfigSectionFilter {
  query: string;
  modifiedOnly: boolean;
  dirtyPaths: readonly string[];
}

function sectionId(path: string): string {
  const slug =
    path
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-+|-+$/g, "") || "group";
  let hash = 2166136261;
  for (let index = 0; index < path.length; index += 1) {
    hash ^= path.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  return `config-section-${slug}-${(hash >>> 0).toString(36)}`;
}

function nodeMatches(
  node: ConfigSchemaNode,
  path: string,
  query: string,
): boolean {
  if (!query) return true;
  const searchable = [
    path,
    node.description,
    node.hint,
    ...(node.options ?? []).map(String),
  ]
    .filter(Boolean)
    .join("\n")
    .toLocaleLowerCase();
  return searchable.includes(query);
}

function filterNode(
  node: ConfigSchemaNode,
  path: string,
  query: string,
  modifiedOnly: boolean,
  dirtySet: ReadonlySet<string>,
  ancestorMatchesQuery = false,
): ConfigSchemaNode | null {
  if (node.invisible) return null;

  const matchesQuery = ancestorMatchesQuery || nodeMatches(node, path, query);
  if (node.type !== "object") {
    if (!matchesQuery || (modifiedOnly && !dirtySet.has(path))) return null;
    return node;
  }

  const items: Record<string, ConfigSchemaNode> = {};
  for (const [key, child] of Object.entries(node.items)) {
    const childPath = `${path}.${key}`;
    const filtered = filterNode(
      child,
      childPath,
      query,
      modifiedOnly,
      dirtySet,
      Boolean(query) && matchesQuery,
    );
    if (filtered) items[key] = filtered;
  }

  if (Object.keys(items).length === 0) return null;
  return { ...node, items };
}

export function filterConfigSections(
  schema: Readonly<Record<string, ConfigSchemaNode>>,
  filter: ConfigSectionFilter,
): ConfigSection[] {
  const query = filter.query.trim().toLocaleLowerCase();
  const dirtySet = new Set(filter.dirtyPaths);

  return Object.entries(schema).flatMap(([path, node]) => {
    const filteredNode = filterNode(
      node,
      path,
      query,
      filter.modifiedOnly,
      dirtySet,
    );
    if (!filteredNode) return [];

    return [{
      id: sectionId(path),
      path,
      label: node.description?.trim() || path,
      node: filteredNode,
      dirtyCount: filter.dirtyPaths.filter(
        (dirtyPath) => dirtyPath === path || dirtyPath.startsWith(`${path}.`),
      ).length,
    }];
  });
}
