import { useCallback, useEffect, useRef, useState } from "react";
import { Download, Info, RotateCw } from "lucide-react";

import { ActionConfirmDialog } from "@/components/editing/ActionConfirmDialog";
import { Button } from "@/components/ui/Button";
import { useI18n } from "@/hooks/useI18n";
import { apiRequest, unwrapApiData } from "@/lib/bridge";

interface UpdateRelease {
  version: string;
  tag?: string;
  published_at?: string;
  notes?: string;
  runtime_filename?: string;
  source?: "mirror" | "official" | string;
}

interface UpdateStatus {
  enabled?: boolean;
  current_version?: string;
  capabilities?: {
    auto_apply?: boolean;
  };
  available?: boolean;
  ignored?: boolean;
  ignored_version?: string | null;
  release?: UpdateRelease | null;
}

interface UpdateOperationStatus {
  operation_id?: string;
  version?: string;
  status?: string;
  rollback_performed?: boolean;
  requires_manual_restart?: boolean;
}

interface UpdateNoticeProps {
  showToast: (message: string, isError?: boolean) => void;
}

/**
 * 在系统概览中展示可用 runtime 更新，并处理忽略、安装与安全下载动作。
 *
 * 更新检查是可选旁路；网络失败时保持页面其余内容可用。支持自动安装的
 * AstrBot 会在 SHA-256 校验后替换 runtime 并重载，旧版宿主保留下载暂存。
 */
export function UpdateNotice({ showToast }: UpdateNoticeProps) {
  const { t } = useI18n();
  const [updateStatus, setUpdateStatus] = useState<UpdateStatus | null>(null);
  const [detailsOpen, setDetailsOpen] = useState(false);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [action, setAction] = useState<"idle" | "ignoring" | "downloading" | "applying">("idle");
  const pollTimerRef = useRef<number | null>(null);
  const pollGenerationRef = useRef(0);

  /** 从后端读取当前 Release 摘要；失败时保持系统概览可用。 */
  const fetchUpdateStatus = useCallback(async () => {
    try {
      const data = unwrapApiData(await apiRequest("update/check", { retries: 0 }));
      setUpdateStatus(data as UpdateStatus);
    } catch {
      // 更新检查失败不应打断系统概览的其他数据。
    }
  }, []);

  useEffect(() => {
    void fetchUpdateStatus();
    return () => {
      pollGenerationRef.current += 1;
      if (pollTimerRef.current !== null) window.clearTimeout(pollTimerRef.current);
    };
  }, [fetchUpdateStatus]);

  /** 停止更新重载状态轮询并使旧轮询失效。 */
  const stopPolling = useCallback(() => {
    pollGenerationRef.current += 1;
    if (pollTimerRef.current !== null) {
      window.clearTimeout(pollTimerRef.current);
      pollTimerRef.current = null;
    }
  }, []);

  /** 轮询 AstrBot 重载结果，并在热重载短暂断连时继续有限重试。 */
  const pollApplyStatus = useCallback((operationId: string, version: string) => {
    stopPolling();
    const generation = pollGenerationRef.current;
    const deadline = Date.now() + 60_000;
    const terminalStatuses = new Set(["succeeded", "rolled_back", "failed"]);

    const poll = async () => {
      if (pollGenerationRef.current !== generation) return;
      try {
        const data = unwrapApiData(await apiRequest(
          `update/status?operation_id=${encodeURIComponent(operationId)}`,
          { retries: 0 },
        )) as UpdateOperationStatus;
        const status = String(data.status ?? "");
        if (terminalStatuses.has(status)) {
          stopPolling();
          setAction("idle");
          setConfirmOpen(false);
          if (status === "succeeded") {
            showToast(t("system.updateApplied", version), false);
            await fetchUpdateStatus();
          } else if (status === "rolled_back") {
            showToast(
              data.requires_manual_restart
                ? t("system.updateRolledBackRestart")
                : t("system.updateRolledBack"),
              true,
            );
          } else {
            showToast(t("system.updateApplyFailed"), true);
          }
          return;
        }
      } catch {
        // 插件热重载期间的短暂断连属于预期状态，继续有界轮询。
      }

      if (Date.now() >= deadline) {
        stopPolling();
        setAction("idle");
        setConfirmOpen(false);
        showToast(t("system.updateStillRunning"), true);
        return;
      }
      pollTimerRef.current = window.setTimeout(() => void poll(), 1000);
    };

    void poll();
  }, [fetchUpdateStatus, showToast, stopPolling, t]);

  /** 保存忽略版本并隐藏当前更新卡片。 */
  const ignoreUpdate = async () => {
    const version = updateStatus?.release?.version;
    if (!version || action !== "idle") return;
    setAction("ignoring");
    try {
      await apiRequest("update/ignore", {
        method: "POST",
        body: { version },
        retries: 0,
      });
      setDetailsOpen(false);
      await fetchUpdateStatus();
      showToast(t("system.updateIgnored"));
    } catch (error) {
      showToast(readableUpdateError(error), true);
    } finally {
      setAction("idle");
    }
  };

  /** 仅下载已校验的 runtime 包，供不支持宿主重载的旧版 AstrBot 使用。 */
  const downloadUpdate = async () => {
    if (action !== "idle") return;
    setAction("downloading");
    try {
      const data = unwrapApiData(await apiRequest("update/download", {
        method: "POST",
        retries: 0,
      })) as Record<string, unknown>;
      showToast(t("system.updateDownloaded", String(data.version ?? "")));
      await fetchUpdateStatus();
    } catch (error) {
      showToast(readableUpdateError(error), true);
    } finally {
      setAction("idle");
    }
  };

  /** 下载并安装 runtime，然后等待 AstrBot 完成单插件重载。 */
  const applyUpdate = async () => {
    if (action !== "idle") return;
    setAction("applying");
    try {
      const data = unwrapApiData(await apiRequest("update/apply", {
        method: "POST",
        body: {},
        retries: 0,
      })) as UpdateOperationStatus;
      const operationId = String(data.operation_id ?? "");
      const version = String(data.version ?? updateStatus?.release?.version ?? "");
      if (!operationId) throw new Error(t("system.updateApplyFailed"));
      pollApplyStatus(operationId, version);
    } catch (error) {
      setAction("idle");
      setConfirmOpen(false);
      showToast(readableUpdateError(error), true);
    }
  };

  if (updateStatus?.enabled === false || !updateStatus?.available || !updateStatus.release) {
    return null;
  }

  const release = updateStatus.release;
  return (
    <section className="rounded-lg border border-primary/40 bg-primary/5 p-5">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="flex min-w-0 items-start gap-3">
          <Info className="mt-0.5 size-5 shrink-0 text-primary" aria-hidden="true" />
          <div className="min-w-0">
            <p className="text-xs font-semibold uppercase tracking-wide text-primary">
              {t("system.updateAvailable")}
            </p>
            <h3 className="mt-1 text-base font-semibold text-foreground">
              {t("system.updateVersion", release.version)}
            </h3>
            <p className="mt-1 text-xs text-muted-foreground">
              {t("system.updateCurrent", updateStatus.current_version ?? "--")}
            </p>
          </div>
        </div>
        <Button
          variant="secondary"
          size="sm"
          onClick={() => setDetailsOpen((open) => !open)}
          aria-expanded={detailsOpen}
        >
          {detailsOpen ? t("system.updateHide") : t("system.updateView")}
        </Button>
      </div>

      {detailsOpen && (
        <div className="mt-4 border-t border-primary/20 pt-4">
          <div className="grid gap-2 text-xs text-muted-foreground sm:grid-cols-2">
            <span>{t("system.updatePackage", release.runtime_filename ?? "--")}</span>
            <span>{t("system.updateSource", release.source ?? "official")}</span>
          </div>
          <div className="mt-3 rounded-md bg-card p-3 text-sm text-muted-foreground">
            <p className="mb-2 text-xs font-semibold text-foreground">{t("system.updateNotes")}</p>
            <p className="whitespace-pre-wrap break-words">{release.notes || t("system.updateNoNotes")}</p>
          </div>
          <div className="mt-4 flex flex-wrap justify-end gap-2">
            <Button
              variant="ghost"
              size="sm"
              onClick={() => void ignoreUpdate()}
              disabled={action !== "idle"}
            >
              {action === "ignoring" ? t("system.updateIgnoring") : t("system.updateIgnore")}
            </Button>
            {updateStatus.capabilities?.auto_apply ? (
              <Button
                size="sm"
                onClick={() => setConfirmOpen(true)}
                disabled={action !== "idle"}
              >
                {action === "applying" ? (
                  <><RotateCw size={14} className="animate-spin" />{t("system.updateApplying")}</>
                ) : (
                  <><RotateCw size={14} />{t("system.updateApply")}</>
                )}
              </Button>
            ) : (
              <Button
                size="sm"
                onClick={() => void downloadUpdate()}
                disabled={action !== "idle"}
              >
                {action === "downloading" ? (
                  <><RotateCw size={14} className="animate-spin" />{t("system.updateDownloading")}</>
                ) : (
                  <><Download size={14} />{t("system.updateDownload")}</>
                )}
              </Button>
            )}
          </div>
        </div>
      )}

      <ActionConfirmDialog
        open={confirmOpen}
        title={t("system.updateConfirmTitle", release.version)}
        description={t("system.updateConfirmDescription")}
        cancelLabel={t("common.cancel")}
        actionLabel={t("system.updateConfirmAction")}
        pendingLabel={t("system.updateApplying")}
        pending={action === "applying"}
        onCancel={() => setConfirmOpen(false)}
        onConfirm={applyUpdate}
      />
    </section>
  );
}

/** 返回适合 toast 展示的更新错误文本。 */
function readableUpdateError(error: unknown): string {
  return error instanceof Error ? error.message : String(error || "更新操作失败");
}
