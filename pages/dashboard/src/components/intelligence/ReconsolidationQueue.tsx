import { useCallback, useEffect, useRef, useState } from "react";
import { Check, GitCompareArrows, RefreshCw, RotateCcw, X } from "lucide-react";

import { DataTablePagination } from "@/components/data-table/DataTablePagination";
import { Button } from "@/components/ui/Button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectTrigger,
} from "@/components/ui/select";
import { useI18n } from "@/hooks/useI18n";
import { apiRequest, unwrapApiData } from "@/lib/bridge";
import { dashboardLocale, translateEnum } from "@/lib/i18n";
import { cn } from "@/lib/utils";
import type {
  ReconsolidationReviewAction,
  ReconsolidationReviewActionResponse,
  ReconsolidationReviewActionValue,
  ReconsolidationReviewDetail,
  ReconsolidationReviewDetailResponse,
  ReconsolidationReviewItem,
  ReconsolidationReviewItemsResponse,
} from "@/types/intelligence";

interface ReconsolidationQueueProps {
  showToast: (message: string, isError?: boolean) => void;
}

const PAGE_SIZE = 10;
const STATUSES = ["pending", "approved", "rejected", "failed", "rolled_back"] as const;

/** 构造带真实服务端状态、偏移量和页大小的候选列表路径。 */
function buildListPath(status: string, offset: number): string {
  const params = new URLSearchParams({
    status,
    offset: String(offset),
    limit: String(PAGE_SIZE),
  });
  return `review/reconsolidation?${params.toString()}`;
}

/** 将不可信列表响应收敛到后端公开的低敏字段白名单。 */
function normalizeCandidate(value: ReconsolidationReviewItem): ReconsolidationReviewItem {
  return {
    candidate_id: String(value?.candidate_id ?? ""),
    status: String(value?.status ?? "pending"),
    change_summary: String(value?.change_summary ?? ""),
    evidence_type: String(value?.evidence_type ?? ""),
    reason_code: String(value?.reason_code ?? ""),
    created_at: typeof value?.created_at === "number" ? value.created_at : String(value?.created_at ?? ""),
    updated_at: typeof value?.updated_at === "number" ? value.updated_at : String(value?.updated_at ?? ""),
  };
}

/** 将候选详情收敛到摘要白名单与两段人工复核正文。 */
function normalizeDetail(value: ReconsolidationReviewDetail): ReconsolidationReviewDetail {
  return {
    ...normalizeCandidate(value),
    old_content: String(value?.old_content ?? ""),
    proposed_content: String(value?.proposed_content ?? ""),
  };
}

/** 将动作历史收敛到动作、稳定原因码和时间字段。 */
function normalizeAction(value: ReconsolidationReviewAction): ReconsolidationReviewAction {
  return {
    action: String(value?.action ?? ""),
    reason_code: String(value?.reason_code ?? ""),
    created_at: typeof value?.created_at === "number" ? value.created_at : String(value?.created_at ?? ""),
  };
}

/** 按 Dashboard 当前语言格式化后端时间值。 */
function formatTime(value: string | number, locale: string): string {
  if (value === "" || value === null || value === undefined) return "--";
  const numeric = typeof value === "number" ? value : Number(value);
  const normalized = Number.isFinite(numeric)
    ? (numeric < 10_000_000_000 ? numeric * 1000 : numeric)
    : value;
  const date = new Date(normalized);
  return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString(locale);
}

/** 返回候选状态对应的语义状态样式。 */
function statusClass(status: string): string {
  if (status === "approved" || status === "rolled_back") {
    return "border-[var(--color-success)]/25 bg-[var(--color-success)]/10 text-[var(--color-success)]";
  }
  if (status === "pending") {
    return "border-[var(--color-warning)]/25 bg-[var(--color-warning)]/10 text-[var(--color-warning)]";
  }
  if (status === "failed" || status === "rejected") {
    return "border-destructive/25 bg-destructive/10 text-destructive";
  }
  return "border-border bg-muted text-muted-foreground";
}

/** 按候选状态限制后端允许的人工动作。 */
function actionAllowed(status: string, action: ReconsolidationReviewActionValue): boolean {
  if (action === "rollback") return status === "approved";
  return status === "pending";
}

/** 展示带真分页、正文对照和受控人工动作的再巩固复核队列。 */
export function ReconsolidationQueue({ showToast }: ReconsolidationQueueProps) {
  const { t, currentLang } = useI18n();
  const [status, setStatus] = useState("pending");
  const [offset, setOffset] = useState(0);
  const [items, setItems] = useState<ReconsolidationReviewItem[]>([]);
  const [total, setTotal] = useState(0);
  const [pageOffset, setPageOffset] = useState(0);
  const [pageLimit, setPageLimit] = useState(PAGE_SIZE);
  const [selectedId, setSelectedId] = useState("");
  const [detail, setDetail] = useState<ReconsolidationReviewDetail | null>(null);
  const [actions, setActions] = useState<ReconsolidationReviewAction[]>([]);
  const [loadingList, setLoadingList] = useState(true);
  const [loadingDetail, setLoadingDetail] = useState(false);
  const [listError, setListError] = useState<string | null>(null);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [pendingAction, setPendingAction] = useState<ReconsolidationReviewActionValue | null>(null);
  const selectedIdRef = useRef("");
  const listRequestRef = useRef(0);
  const detailRequestRef = useRef(0);
  const submittingRef = useRef(false);
  const actionPromiseRef = useRef<Promise<void> | null>(null);
  selectedIdRef.current = selectedId;

  const locale = dashboardLocale(currentLang());
  const title = t("intelligence.reconsolidation.title");
  const statusLabel = (value: string) => translateEnum(
    t,
    "intelligence.reconsolidation.status",
    value,
    value,
  );
  const actionLabel = (value: string) => translateEnum(
    t,
    "intelligence.reconsolidation.action",
    value,
    value,
  );
  const evidenceLabel = (value: string) => translateEnum(
    t,
    "intelligence.reconsolidation.evidence",
    value,
    value,
  );
  const reasonLabel = (value: string) => translateEnum(
    t,
    "intelligence.reconsolidation.reason",
    value,
    value,
  );

  /** 获取当前服务端筛选页，并忽略已过期响应。 */
  const loadItems = useCallback(async () => {
    const requestId = ++listRequestRef.current;
    setLoadingList(true);
    setListError(null);
    try {
      const data = unwrapApiData<ReconsolidationReviewItemsResponse>(
        await apiRequest(buildListPath(status, offset)),
      );
      if (listRequestRef.current !== requestId) return;
      const normalized = Array.isArray(data.items)
        ? data.items.map(normalizeCandidate).filter((item) => item.candidate_id)
        : [];
      const nextTotal = Number.isFinite(data.total) ? Math.max(0, Number(data.total)) : normalized.length;
      const reportedOffset = Number.isFinite(data.offset) && data.offset >= 0
        ? Number(data.offset)
        : offset;
      const reportedLimit = Number.isFinite(data.limit) && data.limit > 0
        ? Number(data.limit)
        : PAGE_SIZE;
      setItems(normalized);
      setTotal(nextTotal);
      setPageOffset(reportedOffset);
      setPageLimit(reportedLimit);
      if (normalized.length === 0 && nextTotal > 0 && reportedOffset >= nextTotal) {
        setOffset(Math.floor((nextTotal - 1) / reportedLimit) * reportedLimit);
        return;
      }
      setSelectedId((current) => {
        if (current && normalized.some((item) => item.candidate_id === current)) return current;
        return normalized[0]?.candidate_id ?? "";
      });
    } catch (error) {
      if (listRequestRef.current !== requestId) return;
      const message = String(error);
      setListError(message);
      showToast(message, true);
    } finally {
      if (listRequestRef.current === requestId) setLoadingList(false);
    }
  }, [offset, showToast, status]);

  /** 获取所选候选详情，并在切换候选时阻止旧响应覆盖新响应。 */
  const loadDetail = useCallback(async (candidateId: string) => {
    const requestId = ++detailRequestRef.current;
    setDetail(null);
    setActions([]);
    setDetailError(null);
    setActionError(null);
    if (!candidateId) {
      setLoadingDetail(false);
      return;
    }
    setLoadingDetail(true);
    try {
      const data = unwrapApiData<ReconsolidationReviewDetailResponse>(
        await apiRequest(
          `review/reconsolidation/detail?candidate_id=${encodeURIComponent(candidateId)}`,
        ),
      );
      if (detailRequestRef.current !== requestId || selectedIdRef.current !== candidateId) return;
      const normalized = normalizeDetail(data.candidate);
      if (normalized.candidate_id !== candidateId) {
        throw new Error(t("intelligence.reconsolidation.detailMismatch"));
      }
      setDetail(normalized);
      setActions(Array.isArray(data.actions) ? data.actions.map(normalizeAction) : []);
    } catch (error) {
      if (detailRequestRef.current !== requestId || selectedIdRef.current !== candidateId) return;
      const message = String(error);
      setDetailError(message);
      showToast(message, true);
    } finally {
      if (detailRequestRef.current === requestId && selectedIdRef.current === candidateId) {
        setLoadingDetail(false);
      }
    }
  }, [showToast, t]);

  useEffect(() => {
    void loadItems();
  }, [loadItems]);

  useEffect(() => {
    void loadDetail(selectedId);
  }, [loadDetail, selectedId]);

  /** 原子提交当前候选动作，并在成功后刷新详情和服务端页。 */
  const runAction = (action: ReconsolidationReviewActionValue): Promise<void> => {
    if (!detail || detail.candidate_id !== selectedId || !actionAllowed(detail.status, action)) {
      return Promise.resolve();
    }
    if (actionPromiseRef.current) return actionPromiseRef.current;
    if (submittingRef.current) return Promise.resolve();

    const candidateId = selectedId;
    submittingRef.current = true;
    setSubmitting(true);
    setActionError(null);
    const operation = (async () => {
      try {
        unwrapApiData<ReconsolidationReviewActionResponse>(
          await apiRequest("review/reconsolidation/action", {
            method: "POST",
            body: { candidate_id: candidateId, action },
          }),
        );
        if (selectedIdRef.current === candidateId) await loadDetail(candidateId);
        await loadItems();
        showToast(t("intelligence.reconsolidation.toastActionSubmitted", actionLabel(action)));
      } catch (error) {
        const message = String(error);
        setActionError(message);
        showToast(message, true);
        throw error;
      } finally {
        submittingRef.current = false;
        actionPromiseRef.current = null;
        setSubmitting(false);
      }
    })();
    actionPromiseRef.current = operation;
    return operation;
  };

  /** 确认对话框中的高影响动作，失败时保留对话框和当前详情。 */
  const confirmAction = async () => {
    if (!pendingAction || submittingRef.current) return;
    try {
      await runAction(pendingAction);
      setPendingAction(null);
    } catch {
      // 保留确认对话框，方便人工核对冲突后重试或取消。
    }
  };

  /** 切换服务端状态筛选并回到第一页。 */
  const changeStatus = (value: string) => {
    setStatus(value);
    setOffset(0);
  };

  const pageCount = total === 0 ? 0 : Math.ceil(total / pageLimit);
  const page = Math.floor(pageOffset / pageLimit);

  return (
    <section role="region" aria-label={title} className="min-w-0 rounded-lg border border-border bg-card">
      <div className="flex flex-wrap items-start justify-between gap-3 border-b border-border px-4 py-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <GitCompareArrows aria-hidden="true" className="size-4 text-primary" />
            <h3 className="text-sm font-semibold text-foreground">{title}</h3>
          </div>
          <p className="mt-1 text-xs text-muted-foreground">{t("intelligence.reconsolidation.subtitle")}</p>
        </div>
        <Button type="button" size="sm" variant="outline" disabled={loadingList || submitting} onClick={() => void loadItems()}>
          <RefreshCw aria-hidden="true" className={cn("size-3.5", loadingList && "animate-spin")} />
          {t("common.refresh")}
        </Button>
      </div>

      <div className="border-b border-border p-4">
        <label className="block max-w-64">
          <span className="text-xs font-medium text-muted-foreground">
            {t("intelligence.reconsolidation.statusLabel")}
          </span>
          <Select value={status} onValueChange={(value) => { if (value) changeStatus(value); }}>
            <SelectTrigger aria-label={t("intelligence.reconsolidation.statusLabel")} className="mt-1 w-full">
              <span>{status === "all" ? t("intelligence.reconsolidation.allStatuses") : statusLabel(status)}</span>
            </SelectTrigger>
            <SelectContent align="start">
              <SelectGroup>
                <SelectItem value="all">{t("intelligence.reconsolidation.allStatuses")}</SelectItem>
                {STATUSES.map((value) => (
                  <SelectItem key={value} value={value}>{statusLabel(value)}</SelectItem>
                ))}
              </SelectGroup>
            </SelectContent>
          </Select>
        </label>
      </div>

      <div className="grid min-w-0 gap-4 p-4 xl:grid-cols-[minmax(0,380px)_minmax(0,1fr)]">
        <div className="min-w-0 overflow-hidden rounded-lg border border-border">
          <div className="border-b border-border px-3 py-2.5">
            <h4 className="text-sm font-medium text-foreground">{t("intelligence.reconsolidation.items")}</h4>
          </div>
          <div className="max-h-[560px] divide-y divide-border overflow-y-auto">
            {loadingList ? (
              <p className="px-3 py-5 text-sm text-muted-foreground">{t("intelligence.reconsolidation.loading")}</p>
            ) : listError ? (
              <p role="alert" className="break-words px-3 py-5 text-sm text-destructive">{listError}</p>
            ) : items.length === 0 ? (
              <p className="px-3 py-5 text-sm text-muted-foreground">{t("intelligence.reconsolidation.noMatches")}</p>
            ) : items.map((item) => {
              const selected = item.candidate_id === selectedId;
              return (
                <button
                  key={item.candidate_id}
                  type="button"
                  aria-current={selected ? "true" : undefined}
                  onClick={() => setSelectedId(item.candidate_id)}
                  className={cn(
                    "block w-full min-w-0 px-3 py-3 text-left transition-colors",
                    selected ? "bg-accent text-accent-foreground" : "hover:bg-muted/60",
                  )}
                >
                  <div className="flex min-w-0 items-start justify-between gap-2">
                    <div className="min-w-0">
                      <p className="truncate font-mono text-xs font-medium">{item.candidate_id}</p>
                      <p className="mt-1 break-words text-sm leading-5">{item.change_summary}</p>
                    </div>
                    <span className={cn("shrink-0 rounded-full border px-2 py-0.5 text-xs", statusClass(item.status))}>
                      {statusLabel(item.status)}
                    </span>
                  </div>
                  <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-xs text-muted-foreground">
                    <span>{evidenceLabel(item.evidence_type)}</span>
                    <span>{reasonLabel(item.reason_code)}</span>
                    <time className="ml-auto" dateTime={String(item.updated_at)}>{formatTime(item.updated_at, locale)}</time>
                  </div>
                </button>
              );
            })}
          </div>
          <div className="border-t border-border p-3">
            <DataTablePagination
              page={page}
              pageCount={pageCount}
              total={total}
              onPageChange={(nextPage) => setOffset(nextPage * pageLimit)}
            />
          </div>
        </div>

        <section aria-label={t("intelligence.reconsolidation.detail")} className="min-w-0 rounded-lg border border-border p-4">
          {actionError ? <p role="alert" className="mb-3 break-words text-sm text-destructive">{actionError}</p> : null}
          {loadingDetail ? (
            <p className="text-sm text-muted-foreground">{t("intelligence.reconsolidation.loadingDetail")}</p>
          ) : detailError ? (
            <p role="alert" className="break-words text-sm text-destructive">{detailError}</p>
          ) : !detail ? (
            <p className="text-sm text-muted-foreground">{t("intelligence.reconsolidation.selectCandidate")}</p>
          ) : (
            <div className="min-w-0 space-y-4">
              <div className="flex min-w-0 flex-wrap items-center justify-between gap-2">
                <h4 className="min-w-0 break-all font-mono text-sm font-semibold text-foreground">{detail.candidate_id}</h4>
                <span className={cn("rounded-full border px-2 py-0.5 text-xs", statusClass(detail.status))}>
                  {statusLabel(detail.status)}
                </span>
              </div>

              <dl className="grid min-w-0 gap-3 sm:grid-cols-2">
                <div className="min-w-0 rounded-lg bg-muted/50 p-3">
                  <dt className="text-xs font-medium text-muted-foreground">{t("intelligence.reconsolidation.oldContent")}</dt>
                  <dd className="mt-2 whitespace-pre-wrap break-words text-sm leading-6 text-foreground">{detail.old_content}</dd>
                </div>
                <div className="min-w-0 rounded-lg bg-muted/50 p-3">
                  <dt className="text-xs font-medium text-muted-foreground">{t("intelligence.reconsolidation.proposedContent")}</dt>
                  <dd className="mt-2 whitespace-pre-wrap break-words text-sm leading-6 text-foreground">{detail.proposed_content}</dd>
                </div>
              </dl>

              <dl className="grid gap-3 text-sm sm:grid-cols-2">
                <div>
                  <dt className="text-xs text-muted-foreground">{t("intelligence.reconsolidation.evidenceLabel")}</dt>
                  <dd className="mt-1 break-words text-foreground">{evidenceLabel(detail.evidence_type)}</dd>
                </div>
                <div>
                  <dt className="text-xs text-muted-foreground">{t("intelligence.reconsolidation.reasonLabel")}</dt>
                  <dd className="mt-1 break-words text-foreground">{reasonLabel(detail.reason_code)}</dd>
                </div>
              </dl>

              <div>
                <h5 className="text-xs font-medium text-muted-foreground">{t("intelligence.reconsolidation.actions")}</h5>
                <div className="mt-2 flex flex-wrap gap-2">
                  <Button type="button" size="sm" disabled={submitting || !actionAllowed(detail.status, "approve")} onClick={() => setPendingAction("approve")}>
                    <Check aria-hidden="true" className="size-3.5" />
                    {t("intelligence.reconsolidation.approve")}
                  </Button>
                  <Button type="button" size="sm" variant="destructive" disabled={submitting || !actionAllowed(detail.status, "reject")} onClick={() => setPendingAction("reject")}>
                    <X aria-hidden="true" className="size-3.5" />
                    {t("intelligence.reconsolidation.reject")}
                  </Button>
                  <Button type="button" size="sm" variant="outline" disabled={submitting || !actionAllowed(detail.status, "rollback")} onClick={() => setPendingAction("rollback")}>
                    <RotateCcw aria-hidden="true" className="size-3.5" />
                    {t("intelligence.reconsolidation.rollback")}
                  </Button>
                </div>
              </div>

              <div>
                <h5 className="text-xs font-medium text-muted-foreground">{t("intelligence.reconsolidation.actionHistory")}</h5>
                {actions.length === 0 ? (
                  <p className="mt-2 text-sm text-muted-foreground">{t("intelligence.reconsolidation.noActions")}</p>
                ) : (
                  <ul className="mt-2 divide-y divide-border rounded-lg border border-border">
                    {actions.map((entry, index) => (
                      <li key={`${entry.action}-${entry.created_at}-${index}`} className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1 px-3 py-2 text-xs">
                        <span className="font-medium text-foreground">{actionLabel(entry.action)}</span>
                        <span className="break-words text-muted-foreground">{reasonLabel(entry.reason_code)}</span>
                        <time className="ml-auto text-muted-foreground" dateTime={String(entry.created_at)}>{formatTime(entry.created_at, locale)}</time>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            </div>
          )}
        </section>
      </div>

      <Dialog open={pendingAction !== null} onOpenChange={(open) => { if (!open && !submitting) setPendingAction(null); }}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>
              {t("intelligence.reconsolidation.confirmTitle", pendingAction ? actionLabel(pendingAction) : "")}
            </DialogTitle>
            <DialogDescription>
              {t("intelligence.reconsolidation.confirmDescription", detail?.candidate_id ?? "")}
            </DialogDescription>
          </DialogHeader>
          {actionError ? <p role="alert" className="break-words text-sm text-destructive">{actionError}</p> : null}
          <DialogFooter>
            <Button type="button" variant="outline" disabled={submitting} onClick={() => setPendingAction(null)}>
              {t("common.cancel")}
            </Button>
            <Button
              type="button"
              variant={pendingAction === "reject" ? "destructive" : "default"}
              disabled={submitting}
              onClick={() => void confirmAction()}
            >
              {submitting
                ? t("intelligence.reconsolidation.submitting")
                : t("intelligence.reconsolidation.confirm")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </section>
  );
}
