import { X } from "lucide-react";
import { Button } from "@/components/ui/Button";
import { formatDashboardNumber, type Translate } from "@/lib/i18n";
import type { GraphNode } from "@/types";

export const GRAPH_NODE_COLORS: Record<string, string> = {
  topic: "#7950f2",
  person: "#20c997",
  fact: "#fcc419",
  summary: "#f06595",
  other: "#909296",
};

interface GraphNodeDetailProps {
  node: GraphNode;
  locale: string;
  t: Translate;
  onClose: () => void;
}

/** 返回节点类型对应的翻译键。 */
export function graphNodeTypeLabel(type: string): string {
  const normalizedType = String(type || "other").toLowerCase();
  const labels: Record<string, string> = {
    topic: "graph.nodeTopic",
    person: "graph.nodePerson",
    fact: "graph.nodeFact",
    summary: "graph.nodeSummary",
  };
  return labels[normalizedType] || "graph.nodeUnknown";
}

/** 展示图节点详情，并仅向人物节点公开可信稳定身份。 */
export function GraphNodeDetail({ node, locale, t, onClose }: GraphNodeDetailProps) {
  const title = node.display_name || node.label || t("graph.unnamedNode");
  const stableUserId = node.stable_user_id?.trim() || "";
  const showStableIdentity = node.type === "person" && stableUserId.length > 0;
  const identityLabel = node.identity_namespace === "qq" ? "QQ" : t("table.userId");

  return (
    <aside
      role="dialog"
      aria-modal="false"
      aria-labelledby="graph-node-detail-title"
      className="fixed inset-y-0 right-0 z-40 w-full max-w-[380px] overflow-y-auto border-l bg-popover text-popover-foreground shadow-lg animate-slide-in-right"
    >
      <div className="flex items-center justify-between border-b px-5 py-3">
        <div className="flex min-w-0 items-center gap-2">
          <span
            aria-hidden="true"
            className="h-3 w-3 shrink-0 rounded-full"
            style={{
              background: GRAPH_NODE_COLORS[node.type ?? "other"]
                ?? GRAPH_NODE_COLORS.other,
            }}
          />
          <h2
            id="graph-node-detail-title"
            className="break-words text-sm font-semibold"
          >
            {title}
          </h2>
        </div>
        <Button
          variant="ghost"
          size="icon-sm"
          onClick={onClose}
          aria-label={t("common.close")}
          title={t("common.close")}
        >
          <X aria-hidden="true" />
        </Button>
      </div>
      <div className="space-y-4 p-5">
        <div className="grid grid-cols-2 gap-3">
          <div>
            <p className="text-xs font-medium text-muted-foreground">
              {t("detail.nodeMemories")}
            </p>
            <p className="text-sm font-semibold">{node.memory_count ?? 0}</p>
          </div>
          <div>
            <p className="text-xs font-medium text-muted-foreground">
              {t("detail.nodeDegree")}
            </p>
            <p className="text-sm font-semibold">{node.degree ?? 0}</p>
          </div>
          <div>
            <p className="text-xs font-medium text-muted-foreground">
              {t("detail.nodeEntries")}
            </p>
            <p className="text-sm font-semibold">{node.entry_count ?? 0}</p>
          </div>
          <div>
            <p className="text-xs font-medium text-muted-foreground">
              {t("detail.nodeWeight")}
            </p>
            <p className="text-sm font-semibold">
              {formatDashboardNumber(node.weight ?? 0, locale, {
                minimumFractionDigits: 2,
                maximumFractionDigits: 2,
              })}
            </p>
          </div>
        </div>
        {showStableIdentity && (
          <div>
            <p className="text-xs font-medium text-muted-foreground">
              {identityLabel}
            </p>
            <p className="break-all text-sm">{stableUserId}</p>
          </div>
        )}
        <div>
          <p className="text-xs font-medium text-muted-foreground">
            {t("table.type")}
          </p>
          <p className="text-sm">{t(graphNodeTypeLabel(node.type ?? "other"))}</p>
        </div>
      </div>
    </aside>
  );
}
