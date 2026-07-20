import { AlertTriangle, CircleAlert, RefreshCw, Settings2 } from "lucide-react";

import { Button } from "@/components/ui/Button";
import { useI18n } from "@/hooks/useI18n";
import type { RuntimeStatus, RuntimeStatusSnapshot } from "@/hooks/useRuntimeStatus";
import { cn } from "@/lib/utils";

interface RuntimeStatusBannerProps {
  snapshot: RuntimeStatusSnapshot;
  onConfigure: () => void;
  onRetry: () => void;
}

function providerLabel(
  provider: string,
  translate: (key: string, ...args: string[]) => string,
): string {
  if (provider === "embedding") return translate("runtime.notice.provider.embedding");
  if (provider === "llm") return translate("runtime.notice.provider.llm");
  return provider;
}

function statusCopy(
  status: RuntimeStatus,
  translate: (key: string, ...args: string[]) => string,
  missingProviders: string[],
): { title: string; description: string; error: boolean } {
  if (status === "waiting") {
    const providers = missingProviders.length > 0
      ? missingProviders.map((provider) => providerLabel(provider, translate)).join("、")
      : translate("runtime.notice.provider.unknown");
    return {
      title: translate("runtime.notice.waiting"),
      description: translate("runtime.notice.waitingDescription", providers),
      error: false,
    };
  }
  if (status === "failed") {
    return {
      title: translate("runtime.notice.failed"),
      description: translate("runtime.notice.failedDescription"),
      error: true,
    };
  }
  if (status === "offline") {
    return {
      title: translate("runtime.notice.offline"),
      description: translate("runtime.notice.offlineDescription"),
      error: true,
    };
  }
  return {
    title: translate("runtime.notice.unknown"),
    description: translate("runtime.notice.unknownDescription"),
    error: status !== "loading",
  };
}

export function RuntimeStatusBanner({
  onConfigure,
  onRetry,
  snapshot,
}: RuntimeStatusBannerProps) {
  const { t } = useI18n();
  if (snapshot.status === "loading" || snapshot.status === "ready") return null;

  const copy = statusCopy(snapshot.status, t, snapshot.missingProviders);
  const Icon = copy.error ? AlertTriangle : CircleAlert;

  return (
    <div
      role={copy.error ? "alert" : "status"}
      data-runtime-status={snapshot.status}
      className={cn(
        "flex shrink-0 flex-wrap items-center gap-3 border-b px-4 py-3 text-sm sm:px-5 lg:px-6",
        copy.error
          ? "border-destructive/30 bg-destructive/10 text-destructive"
          : "border-amber-500/30 bg-amber-500/10 text-amber-700 dark:text-amber-300",
      )}
    >
      <Icon aria-hidden="true" className="size-4 shrink-0" />
      <div className="min-w-0 flex-1">
        <p className="font-medium">{copy.title}</p>
        <p className="mt-0.5 text-xs opacity-90">{copy.description}</p>
      </div>
      <div className="flex shrink-0 flex-wrap items-center gap-2">
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={onConfigure}
          className="border-current/30 bg-background/60"
        >
          <Settings2 data-icon="inline-start" />
          {t("runtime.notice.openConfig")}
        </Button>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          onClick={onRetry}
          className="text-current hover:bg-background/60 hover:text-current"
        >
          <RefreshCw data-icon="inline-start" />
          {t("runtime.notice.retry")}
        </Button>
      </div>
    </div>
  );
}
