import { ChevronLeft, ChevronRight } from "lucide-react";

import { Button } from "@/components/ui/Button";
import { useI18n } from "@/hooks/useI18n";

interface DataTablePaginationProps {
  page: number;
  pageCount: number;
  total: number;
  onPageChange(page: number): void;
}

export function DataTablePagination({
  page,
  pageCount,
  total,
  onPageChange,
}: DataTablePaginationProps) {
  const { t } = useI18n();
  const safePageCount = Math.max(pageCount, 1);

  return (
    <nav
      aria-label={t("pagination.label")}
      className="flex flex-wrap items-center justify-between gap-2 text-sm text-muted-foreground"
    >
      <span className="tabular-nums">{total}</span>
      <div className="flex items-center gap-2">
        <span className="tabular-nums">
          {t("pagination.pageOf", String(page + 1), String(safePageCount))}
        </span>
        <Button
          type="button"
          variant="outline"
          size="icon-sm"
          aria-label={t("pagination.previousPage")}
          disabled={page <= 0}
          onClick={() => onPageChange(page - 1)}
        >
          <ChevronLeft aria-hidden="true" />
        </Button>
        <Button
          type="button"
          variant="outline"
          size="icon-sm"
          aria-label={t("pagination.nextPage")}
          disabled={pageCount <= 0 || page >= pageCount - 1}
          onClick={() => onPageChange(page + 1)}
        >
          <ChevronRight aria-hidden="true" />
        </Button>
      </div>
    </nav>
  );
}
