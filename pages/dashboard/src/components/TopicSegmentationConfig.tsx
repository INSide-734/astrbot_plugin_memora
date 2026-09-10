import { useState, useEffect, useCallback } from "react";
import { Play, RefreshCw, GitBranch } from "lucide-react";
import { useI18n } from "@/hooks/useI18n";
import { apiRequest, unwrapApiData } from "@/lib/bridge";
import { translateEnum } from "@/lib/i18n";
import { Button } from "@/components/ui/Button";

interface TopicSegmentationConfigProps {
  showToast: (msg: string, isError?: boolean) => void;
}

interface BackfillStatus {
  status: string;
  processed?: number;
  total?: number;
  errors?: number;
  job_id?: string;
}

export function TopicSegmentationConfig({ showToast }: TopicSegmentationConfigProps) {
  const { t } = useI18n();
  const [backfill, setBackfill] = useState<BackfillStatus | null>(null);

  const fetchBackfill = useCallback(async () => {
    try {
      const res = unwrapApiData(await apiRequest("backfill/status"));
      setBackfill(res as unknown as BackfillStatus);
    } catch { /* silent */ }
  }, []);

  useEffect(() => {
    fetchBackfill();
  }, [fetchBackfill]);

  const startBackfill = async () => {
    try {
      await apiRequest("backfill/start", { method: "POST" });
      showToast(t("backfill.started"));
      fetchBackfill();
    } catch (e) {
      showToast(String(e), true);
    }
  };

  const backfillProgress =
    backfill?.total && backfill.total > 0
      ? Math.round(((backfill.processed || 0) / backfill.total) * 100)
      : 0;

  const statusLabel = (status: string): string => {
    const rawStatus = String(status ?? "").trim();
    return rawStatus ? translateEnum(t, "runtime.status", rawStatus) : "--";
  };

  return (
    <div className="flex flex-col gap-4 rounded-lg border bg-card p-6">
      {/* Header */}
      <div className="flex items-center gap-2">
        <GitBranch className="w-5 h-5 text-primary" />
        <h3 className="text-lg font-semibold">{t("backfill.title")}</h3>
      </div>

      {/* Backfill section */}
      <div className="space-y-3">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <RefreshCw
              className={`w-4 h-4 ${backfill?.status === "running" ? "animate-spin" : ""}`}
            />
            <span className="text-sm text-muted-foreground">
              {t("backfill.description")}
            </span>
          </div>
          <Button
            variant="outline"
            size="sm"
            onClick={startBackfill}
            disabled={backfill?.status === "running"}
          >
            <Play className="w-3 h-3 mr-1" />
            {t("backfill.startButton")}
          </Button>
        </div>

        {backfill && backfill.status !== "idle" && backfill.status !== "unavailable" && (
          <div className="space-y-1">
            <div className="flex justify-between text-xs text-muted-foreground">
              <span>{statusLabel(backfill.status)}</span>
              <span>
                {backfill.processed || 0}
                {backfill.total ? ` / ${backfill.total}` : ""}
              </span>
            </div>
            <div className="h-2 rounded-full bg-muted overflow-hidden">
              <div
                className="h-full rounded-full bg-primary transition-all duration-500"
                style={{ width: `${backfillProgress}%` }}
              />
            </div>
            {backfill.errors && backfill.errors > 0 && (
              <p className="text-xs text-red-500">
                {t("backfill.errors", String(backfill.errors))}
              </p>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
