import { useCallback, useEffect, useMemo, useState } from "react";
import { Filter, RefreshCw, Search } from "lucide-react";

import { Button } from "@/components/ui/Button";
import { selectionStateVariants } from "@/components/ui/selection-state";
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
  ReviewAction,
  ReviewActionValue,
  ReviewItem,
  ReviewItemDetailResponse,
  ReviewItemsResponse,
} from "@/types/intelligence";
import { ReviewItemDetail } from "./ReviewItemDetail";

interface ReviewQueueProps {
  showToast: (msg: string, isError?: boolean) => void;
}

type FilterState = {
  status: string;
  reason: string;
  severity: string;
  search: string;
};

const DEFAULT_FILTERS: FilterState = {
  status: "all",
  reason: "all",
  severity: "all",
  search: "",
};

function buildListPath(filters: FilterState): string {
  const params = new URLSearchParams();
  params.set("limit", "50");
  if (filters.status !== "all") params.set("status", filters.status);
  if (filters.reason !== "all") params.set("reason", filters.reason);
  if (filters.severity !== "all") params.set("severity", filters.severity);
  return `review/items?${params.toString()}`;
}

function normalizeItem(item: ReviewItem): ReviewItem {
  const metadata = item.metadata && typeof item.metadata === "object" && !Array.isArray(item.metadata)
    ? item.metadata
    : {};
  return {
    item_id: String(item.item_id || ""),
    memory_id: String(item.memory_id || ""),
    reasons: Array.isArray(item.reasons) ? item.reasons.map(String) : [],
    severity: String(item.severity || "low"),
    status: String(item.status || "open"),
    content_preview: String(item.content_preview || ""),
    metadata: metadata as Record<string, unknown>,
    created_at: Number(item.created_at || 0),
    updated_at: Number(item.updated_at || 0),
  };
}

function formatTime(value: number, locale: string): string {
  if (!value) return "--";
  const ms = value < 10_000_000_000 ? value * 1000 : value;
  const date = new Date(ms);
  if (Number.isNaN(date.getTime())) return String(value);
  return date.toLocaleString(locale);
}

function badgeClass(value: string): string {
  const normalized = value.toLowerCase();
  if (normalized === "high" || normalized === "critical") {
    return "border-[var(--color-danger)]/25 bg-[var(--color-danger)]/10 text-[var(--color-danger)]";
  }
  if (normalized === "medium" || normalized === "open") {
    return "border-[var(--color-warning)]/25 bg-[var(--color-warning)]/10 text-[var(--color-warning)]";
  }
  if (normalized === "approved" || normalized === "safe" || normalized === "edited" || normalized === "merged") {
    return "border-[var(--color-success)]/25 bg-[var(--color-success)]/10 text-[var(--color-success)]";
  }
  return "border-[var(--color-border)] bg-[var(--color-surface)] text-[var(--text-secondary)]";
}

function uniqueSorted(values: string[]): string[] {
  return Array.from(new Set(values.filter(Boolean))).sort((a, b) => a.localeCompare(b));
}

function FilterSelect({
  label,
  value,
  allLabel,
  options,
  optionLabel,
  onChange,
}: {
  label: string;
  value: string;
  allLabel: string;
  options: string[];
  optionLabel: (value: string) => string;
  onChange: (value: string) => void;
}) {
  return (
    <div className="block">
      <span className="text-2xs font-semibold uppercase tracking-normal text-[var(--text-tertiary)]">{label}</span>
      <Select value={value} onValueChange={(nextValue) => { if (nextValue) onChange(nextValue); }}>
        <SelectTrigger aria-label={label} className="mt-1 h-9 w-full">
          <span>{value === "all" ? allLabel : optionLabel(value)}</span>
        </SelectTrigger>
        <SelectContent>
          <SelectGroup>
            <SelectItem value="all">{allLabel}</SelectItem>
            {options.map((option) => <SelectItem key={option} value={option}>{optionLabel(option)}</SelectItem>)}
          </SelectGroup>
        </SelectContent>
      </Select>
    </div>
  );
}

export function ReviewQueue({ showToast }: ReviewQueueProps) {
  const { t, currentLang } = useI18n();
  const [items, setItems] = useState<ReviewItem[]>([]);
  const [total, setTotal] = useState(0);
  const [selectedId, setSelectedId] = useState("");
  const [selectedItem, setSelectedItem] = useState<ReviewItem | null>(null);
  const [actions, setActions] = useState<ReviewAction[]>([]);
  const [filters, setFilters] = useState<FilterState>(DEFAULT_FILTERS);
  const [loadingList, setLoadingList] = useState(true);
  const [loadingDetail, setLoadingDetail] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const locale = dashboardLocale(currentLang());
  const statusLabel = (value: string) => translateEnum(t, "intelligence.review.status", value, value);
  const reasonLabel = (value: string) => translateEnum(t, "intelligence.review.reason", value, value);
  const severityLabel = (value: string) => translateEnum(t, "severity", value, value);
  const actionLabel = (value: string) => translateEnum(t, "intelligence.review.action", value, value);

  const loadItems = useCallback(async () => {
    setLoadingList(true);
    try {
      const data = unwrapApiData<ReviewItemsResponse>(await apiRequest(buildListPath(filters)));
      const normalized = Array.isArray(data.items) ? data.items.map(normalizeItem) : [];
      setItems(normalized);
      setTotal(Number.isFinite(data.total) ? data.total : normalized.length);
      setSelectedId((current) => {
        if (current && normalized.some((item) => item.item_id === current)) return current;
        return normalized[0]?.item_id ?? "";
      });
    } catch (e) {
      showToast(String(e), true);
    } finally {
      setLoadingList(false);
    }
  }, [filters, showToast]);

  const loadDetail = useCallback(async (reviewId: string) => {
    if (!reviewId) {
      setSelectedItem(null);
      setActions([]);
      return;
    }
    setLoadingDetail(true);
    try {
      const data = unwrapApiData<ReviewItemDetailResponse>(
        await apiRequest(`review/items/detail?review_id=${encodeURIComponent(reviewId)}`),
      );
      setSelectedItem(normalizeItem(data.item));
      setActions(Array.isArray(data.actions) ? data.actions : []);
    } catch (e) {
      showToast(String(e), true);
    } finally {
      setLoadingDetail(false);
    }
  }, [showToast]);

  useEffect(() => {
    loadItems();
  }, [loadItems]);

  useEffect(() => {
    loadDetail(selectedId);
  }, [loadDetail, selectedId]);

  const filteredItems = useMemo(() => {
    const q = filters.search.trim().toLowerCase();
    return items.filter((item) => {
      if (filters.status !== "all" && item.status !== filters.status) return false;
      if (filters.reason !== "all" && !item.reasons.includes(filters.reason)) return false;
      if (filters.severity !== "all" && item.severity !== filters.severity) return false;
      if (!q) return true;
      const haystack = [
        item.content_preview,
        item.memory_id,
        item.status,
        item.severity,
        ...item.reasons,
      ].join(" ").toLowerCase();
      return haystack.includes(q);
    });
  }, [filters, items]);

  useEffect(() => {
    if (selectedId && filteredItems.some((item) => item.item_id === selectedId)) return;
    setSelectedId(filteredItems[0]?.item_id ?? "");
  }, [filteredItems, selectedId]);

  const statusOptions = useMemo(() => uniqueSorted(items.map((item) => item.status)), [items]);
  const reasonOptions = useMemo(() => uniqueSorted(items.flatMap((item) => item.reasons)), [items]);
  const severityOptions = useMemo(() => uniqueSorted(items.map((item) => item.severity)), [items]);

  const runRefresh = async () => {
    setSubmitting(true);
    try {
      unwrapApiData(await apiRequest("review/refresh", { method: "POST", body: {} }));
      await loadItems();
      showToast(t("intelligence.review.toastRefreshed"));
    } catch (e) {
      showToast(String(e), true);
    } finally {
      setSubmitting(false);
    }
  };

  const runAction = async (
    action: ReviewActionValue,
    payload: Record<string, unknown> = {},
    confirmed = false,
  ) => {
    if (!selectedId) return;
    setSubmitting(true);
    try {
      const body: Record<string, unknown> = {
        review_id: selectedId,
        action,
        payload,
      };
      if (confirmed === true) body.confirmed = true;
      unwrapApiData(await apiRequest("review/action", { method: "POST", body }));
      await Promise.all([loadItems(), loadDetail(selectedId)]);
      showToast(t("intelligence.review.toastActionSubmitted", actionLabel(action)));
    } catch (e) {
      showToast(String(e), true);
      throw e;
    } finally {
      setSubmitting(false);
    }
  };

  const updateFilter = (key: keyof FilterState, value: string) => {
    setFilters((current) => ({ ...current, [key]: value }));
  };

  return (
    <section className="space-y-4">
      <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface-secondary)]">
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-[var(--color-border)] px-4 py-3">
          <div className="flex items-center gap-2">
            <Filter size={16} className="text-[var(--color-accent)]" />
            <div>
              <h3 className="text-sm font-semibold text-[var(--text-primary)]">{t("intelligence.review.queue")}</h3>
              <p className="mt-0.5 text-xs text-[var(--text-tertiary)]">
                {t("intelligence.review.visibleTotal", String(filteredItems.length), String(total))}
              </p>
            </div>
          </div>
          <Button size="sm" variant="secondary" onClick={runRefresh} disabled={submitting || loadingList}>
            <RefreshCw size={13} />
            {t("common.refresh")}
          </Button>
        </div>

        <div className="grid gap-3 p-4 lg:grid-cols-[160px_160px_160px_1fr]">
          <FilterSelect
            label={t("intelligence.review.status")}
            value={filters.status}
            allLabel={t("intelligence.review.allStatus")}
            options={statusOptions}
            optionLabel={statusLabel}
            onChange={(value) => updateFilter("status", value)}
          />

          <FilterSelect
            label={t("intelligence.review.reason")}
            value={filters.reason}
            allLabel={t("intelligence.review.allReasons")}
            options={reasonOptions}
            optionLabel={reasonLabel}
            onChange={(value) => updateFilter("reason", value)}
          />

          <FilterSelect
            label={t("intelligence.review.severity")}
            value={filters.severity}
            allLabel={t("intelligence.review.allSeverity")}
            options={severityOptions}
            optionLabel={severityLabel}
            onChange={(value) => updateFilter("severity", value)}
          />

          <label className="block">
            <span className="text-2xs font-semibold uppercase tracking-normal text-[var(--text-tertiary)]">{t("intelligence.review.search")}</span>
            <div className="relative mt-1">
              <Search size={14} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-[var(--text-tertiary)]" />
              <input
                aria-label={t("intelligence.review.search")}
                placeholder={t("intelligence.review.searchPlaceholder")}
                value={filters.search}
                onChange={(event) => updateFilter("search", event.target.value)}
                className="h-9 w-full rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] pl-9 pr-3 text-sm text-[var(--text-primary)] outline-none focus:border-[var(--color-accent)]"
              />
            </div>
          </label>
        </div>
      </div>

      <div className="grid gap-4 xl:grid-cols-[420px_1fr]">
        <div className="overflow-hidden rounded-lg border border-[var(--color-border)] bg-[var(--color-surface-secondary)]">
          <div className="border-b border-[var(--color-border)] px-4 py-3">
            <h3 className="text-sm font-semibold text-[var(--text-primary)]">{t("intelligence.review.items")}</h3>
          </div>
          <div className="max-h-[680px] divide-y divide-[var(--color-border-light)] overflow-auto">
            {loadingList ? (
              <p className="px-4 py-5 text-sm text-[var(--text-tertiary)]">{t("intelligence.review.loadingItems")}</p>
            ) : filteredItems.length === 0 ? (
              <p className="px-4 py-5 text-sm text-[var(--text-tertiary)]">{t("intelligence.review.noMatches")}</p>
            ) : (
              filteredItems.map((item) => {
                const selected = item.item_id === selectedId;
                return (
                  <button
                    key={item.item_id}
                    type="button"
                    aria-current={selected ? "true" : undefined}
                    onClick={() => setSelectedId(item.item_id)}
                    className={cn(
                      "block w-full px-4 py-3 text-left",
                      selectionStateVariants({ kind: "current-item", selected }),
                      !selected && "hover:bg-[var(--color-surface)]",
                    )}
                  >
                    <div className="flex items-start justify-between gap-3">
                      <div>
                        <p className="font-mono text-xs font-semibold text-[var(--text-primary)]">{item.memory_id}</p>
                        <p className="mt-1 line-clamp-2 text-sm leading-5 text-[var(--text-secondary)]">{item.content_preview}</p>
                      </div>
                      <span className={`shrink-0 rounded-full border px-2 py-1 text-2xs font-semibold uppercase ${badgeClass(item.severity)}`}>
                        {severityLabel(item.severity)}
                      </span>
                    </div>
                    <div className="mt-3 flex flex-wrap items-center gap-2">
                      <span className={`rounded-full border px-2 py-1 text-2xs font-semibold uppercase ${badgeClass(item.status)}`}>
                        {statusLabel(item.status)}
                      </span>
                      {item.reasons.map((reason) => (
                        <span key={reason} className="rounded-full border border-[var(--color-border)] bg-[var(--color-surface)] px-2 py-1 text-2xs text-[var(--text-secondary)]">
                          {reasonLabel(reason)}
                        </span>
                      ))}
                      <span className="ml-auto text-2xs text-[var(--text-tertiary)]">{formatTime(item.updated_at, locale)}</span>
                    </div>
                  </button>
                );
              })
            )}
          </div>
        </div>

        <ReviewItemDetail
          item={selectedItem}
          actions={actions}
          loading={loadingDetail}
          submitting={submitting}
          onAction={runAction}
        />
      </div>
    </section>
  );
}
