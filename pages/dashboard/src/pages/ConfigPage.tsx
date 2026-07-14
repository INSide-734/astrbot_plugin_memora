import { LoaderCircle, RefreshCw, Save, Settings2 } from "lucide-react";
import {
  type UIEvent,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import { ConfigConflictDialog } from "@/components/config/ConfigConflictDialog";
import { ConfigField } from "@/components/config/ConfigField";
import {
  PageContent,
  PageFrame,
  PageHeader,
  PageToolbar,
} from "@/components/layout/PageLayout";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Separator } from "@/components/ui/separator";
import { selectionStateVariants } from "@/components/ui/selection-state";
import { StatePanel } from "@/components/ui/StatePanel";
import { Switch } from "@/components/ui/switch";
import { useConfigSync } from "@/hooks/useConfigSync";
import { useI18n } from "@/hooks/useI18n";
import { getConfigValue } from "@/lib/config";
import { filterConfigSections } from "@/lib/configSections";
import type { Translate } from "@/lib/i18n";
import { cn } from "@/lib/utils";
import type { ConfigNavigationTarget } from "@/types";
import type { ConfigSyncStatus } from "@/types/config";

export interface ConfigPageProps {
  showToast?: (
    message: string,
    type?: "success" | "error" | "info",
  ) => void;
  onDirtyChange?: (dirty: boolean) => void;
  navigationTarget?: ConfigNavigationTarget | null;
}

function syncStatusLabel(t: Translate, status: ConfigSyncStatus): string {
  switch (status) {
    case "loading":
      return t("config.status.loading");
    case "synced":
      return t("config.status.synced");
    case "dirty":
      return t("config.status.dirty");
    case "applying":
      return t("config.status.applying");
    case "reloading":
      return t("config.status.reloading");
    case "conflict":
      return t("config.status.conflict");
    case "offline":
      return t("config.status.offline");
    case "error":
      return t("config.status.error");
  }
}

function statusVariant(status: ConfigSyncStatus) {
  if (status === "conflict" || status === "error") return "destructive";
  if (status === "dirty") return "secondary";
  if (status === "offline") return "outline";
  return "default";
}

export function ConfigPage({
  navigationTarget,
  onDirtyChange,
  showToast,
}: ConfigPageProps) {
  const { t } = useI18n();
  const sync = useConfigSync();
  const [query, setQuery] = useState("");
  const [modifiedOnly, setModifiedOnly] = useState(false);
  const [activePath, setActivePath] = useState("");
  const [pendingTarget, setPendingTarget] =
    useState<ConfigNavigationTarget | null>(null);
  const [highlightedPath, setHighlightedPath] = useState<string | null>(null);
  const dirtyOwnerRef = useRef<ConfigPageProps["onDirtyChange"]>(undefined);
  const groupNavigationRef = useRef<HTMLElement | null>(null);
  const configFormRef = useRef<HTMLDivElement | null>(null);
  const sectionFocusTimerRef = useRef<number | null>(null);
  const processedTargetRef = useRef<number | null>(null);
  const targetFocusTimerRef = useRef<number | null>(null);
  const targetHighlightTimerRef = useRef<number | null>(null);
  const previousStatusRef = useRef(sync.status);
  const dirty = sync.dirtyPaths.length > 0;
  const loaded = Boolean(sync.schemaData && sync.draft && sync.baseConfig);

  const sections = useMemo(
    () =>
      filterConfigSections(sync.schemaData?.schema ?? {}, {
        query,
        modifiedOnly,
        dirtyPaths: sync.dirtyPaths,
      }),
    [modifiedOnly, query, sync.dirtyPaths, sync.schemaData?.schema],
  );

  useEffect(() => {
    if (!sections.some((section) => section.path === activePath)) {
      setActivePath(sections[0]?.path ?? "");
    }
  }, [activePath, sections]);

  useEffect(() => {
    if (
      !navigationTarget ||
      processedTargetRef.current === navigationTarget.requestId
    ) {
      return;
    }

    processedTargetRef.current = navigationTarget.requestId;
    if (targetFocusTimerRef.current !== null) {
      window.clearTimeout(targetFocusTimerRef.current);
      targetFocusTimerRef.current = null;
    }
    if (targetHighlightTimerRef.current !== null) {
      window.clearTimeout(targetHighlightTimerRef.current);
      targetHighlightTimerRef.current = null;
    }
    setHighlightedPath(null);
    setModifiedOnly(false);
    setQuery(navigationTarget.query);
    setPendingTarget(navigationTarget);
  }, [navigationTarget]);

  useEffect(() => {
    if (!loaded || !pendingTarget || sync.status === "conflict") return;

    const { path } = pendingTarget;
    const rootPath = path.split(".")[0] || path;
    const rootSection = sections.find((section) => section.path === rootPath);
    if (!rootSection) {
      if (query !== path) {
        setQuery(path);
      } else {
        setPendingTarget(null);
      }
      return;
    }

    if (targetFocusTimerRef.current !== null) {
      window.clearTimeout(targetFocusTimerRef.current);
    }
    targetFocusTimerRef.current = window.setTimeout(() => {
      targetFocusTimerRef.current = null;
      const target = configFormRef.current
        ? Array.from(
            configFormRef.current.querySelectorAll<HTMLElement>(
              "[data-config-path]",
            ),
          ).find((element) => element.dataset.configPath === path)
        : undefined;

      if (!target) {
        if (query !== path) {
          setQuery(path);
        } else {
          setPendingTarget(null);
        }
        return;
      }

      setActivePath(rootPath);
      setHighlightedPath(path);
      target.scrollIntoView({ behavior: "auto", block: "center" });

      let focusTarget: HTMLElement = target;
      if (target.dataset.slot === "config-group") {
        target.tabIndex = -1;
      } else {
        const control = Array.from(
          target.querySelectorAll<HTMLElement>(
            "input:not([disabled]), textarea:not([disabled]), select:not([disabled]), button:not([disabled]), [tabindex]:not([tabindex='-1']):not([aria-disabled='true'])",
          ),
        ).find(
          (element) =>
            !element.hasAttribute("disabled") &&
            element.getAttribute("aria-disabled") !== "true",
        );
        if (control) {
          focusTarget = control;
        } else {
          target.tabIndex = -1;
        }
      }
      focusTarget.focus({ preventScroll: true });
      setPendingTarget(null);

      if (targetHighlightTimerRef.current !== null) {
        window.clearTimeout(targetHighlightTimerRef.current);
      }
      targetHighlightTimerRef.current = window.setTimeout(() => {
        targetHighlightTimerRef.current = null;
        setHighlightedPath(null);
      }, 1_600);
    }, 0);

    return () => {
      if (targetFocusTimerRef.current === null) return;
      window.clearTimeout(targetFocusTimerRef.current);
      targetFocusTimerRef.current = null;
    };
  }, [loaded, pendingTarget, query, sections, sync.status]);

  useEffect(() => {
    const navigation = groupNavigationRef.current;
    if (!navigation || !activePath) return;
    const activeItem = Array.from(
      navigation.querySelectorAll<HTMLElement>("[data-config-nav-path]"),
    ).find((item) => item.dataset.configNavPath === activePath);
    if (!activeItem) return;

    const navigationRect = navigation.getBoundingClientRect();
    const itemRect = activeItem.getBoundingClientRect();
    if (itemRect.top < navigationRect.top) {
      navigation.scrollTop = Math.max(
        0,
        navigation.scrollTop - (navigationRect.top - itemRect.top),
      );
    } else if (itemRect.bottom > navigationRect.bottom) {
      navigation.scrollTop += itemRect.bottom - navigationRect.bottom;
    }
  }, [activePath, sections]);

  useEffect(() => {
    const owner = dirtyOwnerRef.current;
    if (!dirty) {
      if (owner) {
        dirtyOwnerRef.current = undefined;
        owner(false);
      }
      return;
    }
    if (owner === onDirtyChange) return;
    if (owner) {
      dirtyOwnerRef.current = undefined;
      owner(false);
    }
    if (onDirtyChange) {
      dirtyOwnerRef.current = onDirtyChange;
      onDirtyChange(true);
    }
  }, [dirty, onDirtyChange]);

  useEffect(() => {
    const previousStatus = previousStatusRef.current;
    previousStatusRef.current = sync.status;
    if (
      previousStatus === "applying" &&
      (sync.status === "synced" ||
        sync.status === "dirty" ||
        sync.status === "reloading")
    ) {
      showToast?.(t("config.appliedToast"), "success");
    }
  }, [showToast, sync.status, t]);

  useEffect(
    () => () => {
      const owner = dirtyOwnerRef.current;
      if (!owner) return;
      dirtyOwnerRef.current = undefined;
      owner(false);
    },
    [],
  );

  useEffect(() => {
    if (!dirty) return;
    const preventUnsavedClose = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", preventUnsavedClose);
    return () => {
      window.removeEventListener("beforeunload", preventUnsavedClose);
    };
  }, [dirty]);

  const goToSection = useCallback((id: string, path: string) => {
    const section = document.getElementById(id);
    if (!section) return;
    setActivePath(path);
    section.scrollIntoView({ behavior: "auto", block: "start" });
    const focusTarget =
      section.querySelector<HTMLElement>("[data-slot='config-group']") ??
      section;
    focusTarget.setAttribute("tabindex", "-1");
    focusTarget.focus({ preventScroll: true });
  }, []);

  const handleSectionScroll = useCallback((event: UIEvent<HTMLDivElement>) => {
    const container = event.currentTarget;
    const sectionElements = Array.from(
      container.querySelectorAll<HTMLElement>("[data-config-section]"),
    );
    if (sectionElements.length === 0) return;

    let nextPath = sectionElements[0].dataset.configSection ?? "";
    const maximumScrollTop = container.scrollHeight - container.clientHeight;
    if (container.scrollTop <= 1) {
      nextPath = sectionElements[0].dataset.configSection ?? nextPath;
    } else if (container.scrollTop >= maximumScrollTop - 1) {
      nextPath =
        sectionElements[sectionElements.length - 1].dataset.configSection ??
        nextPath;
    } else {
      const activationLine = container.getBoundingClientRect().top + 24;
      for (const section of sectionElements) {
        if (section.getBoundingClientRect().top > activationLine) break;
        nextPath = section.dataset.configSection ?? nextPath;
      }
    }

    if (nextPath) {
      setActivePath((currentPath) =>
        currentPath === nextPath ? currentPath : nextPath,
      );
    }
  }, []);

  const scheduleSectionFocus = useCallback(
    (id: string, path: string) => {
      if (sectionFocusTimerRef.current !== null) {
        window.clearTimeout(sectionFocusTimerRef.current);
      }
      sectionFocusTimerRef.current = window.setTimeout(() => {
        sectionFocusTimerRef.current = null;
        goToSection(id, path);
      }, 0);
    },
    [goToSection],
  );

  useEffect(
    () => () => {
      if (sectionFocusTimerRef.current !== null) {
        window.clearTimeout(sectionFocusTimerRef.current);
        sectionFocusTimerRef.current = null;
      }
      if (targetFocusTimerRef.current !== null) {
        window.clearTimeout(targetFocusTimerRef.current);
        targetFocusTimerRef.current = null;
      }
      if (targetHighlightTimerRef.current !== null) {
        window.clearTimeout(targetHighlightTimerRef.current);
        targetHighlightTimerRef.current = null;
      }
    },
    [],
  );

  const controlsDisabled =
    sync.status === "applying" ||
    sync.status === "reloading" ||
    sync.status === "conflict";
  const applyDisabled =
    sync.dirtyPaths.length === 0 ||
    sync.status === "loading" ||
    sync.status === "applying" ||
    sync.status === "reloading" ||
    sync.status === "conflict";
  const providerOptions = sync.schemaData?.provider_options ?? {
    llm: [],
    embedding: [],
  };
  const sectionItems = sections.map(({ label, path }) => ({
    label,
    value: path,
  }));

  return (
    <PageFrame variant="dense" aria-label={t("config.title")}>
      <PageHeader
        title={t("config.title")}
        description={t("config.subtitle")}
        icon={<Settings2 aria-hidden="true" />}
        status={
          <Badge variant={statusVariant(sync.status)}>
            {sync.status === "applying" ? (
              <LoaderCircle aria-hidden="true" className="animate-spin" />
            ) : sync.status === "reloading" ? (
              <RefreshCw aria-hidden="true" className="animate-spin" />
            ) : null}
            {syncStatusLabel(t, sync.status)}
          </Badge>
        }
        actions={
          loaded ? (
            <dl className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
              <div className="flex min-w-0 items-center gap-1">
                <dt>{t("config.revision")}</dt>
                <dd>
                  <code className="break-all text-foreground">
                    {sync.revision}
                  </code>
                </dd>
              </div>
              <div className="flex min-w-0 items-center gap-1">
                <dt>{t("config.instance")}</dt>
                <dd>
                  <code className="break-all text-foreground">
                    {sync.instanceId}
                  </code>
                </dd>
              </div>
            </dl>
          ) : undefined
        }
      />

      <PageToolbar aria-label={t("config.title")}>
        <Input
          type="search"
          value={query}
          onChange={(event) => setQuery(event.currentTarget.value)}
          aria-label={t("config.search")}
          placeholder={t("config.searchPlaceholder")}
          className="min-w-44 flex-1 sm:max-w-md"
          disabled={!loaded}
        />
        <div className="flex min-h-8 items-center gap-2 px-1">
          <Switch
            id="config-modified-only"
            size="sm"
            checked={modifiedOnly}
            onCheckedChange={setModifiedOnly}
            disabled={!loaded}
          />
          <Label htmlFor="config-modified-only">
            {t("config.modifiedOnly")}
          </Label>
        </div>
        <Button
          type="button"
          variant="outline"
          size="icon"
          aria-label={t("config.refresh")}
          title={t("config.refresh")}
          disabled={!loaded || sync.status === "applying" || sync.status === "reloading"}
          onClick={() => void sync.refresh()}
        >
          <RefreshCw
            aria-hidden="true"
            className={sync.status === "reloading" ? "animate-spin" : undefined}
          />
        </Button>
        <Button
          type="button"
          disabled={applyDisabled}
          onClick={() => void sync.apply()}
        >
          {sync.status === "applying" ? (
            <LoaderCircle data-icon="inline-start" className="animate-spin" />
          ) : sync.status === "reloading" ? (
            <RefreshCw data-icon="inline-start" className="animate-spin" />
          ) : (
            <Save data-icon="inline-start" />
          )}
          {sync.status === "applying"
            ? t("config.applying")
            : sync.status === "reloading"
              ? t("config.reloading")
              : t("config.apply")}
        </Button>
      </PageToolbar>

      <PageContent
        width="full"
        className="min-w-0 lg:overflow-hidden"
        onScroll={handleSectionScroll}
      >
        {!loaded && sync.status === "loading" ? (
          <StatePanel
            state="loading"
            title={t("config.loading")}
            className="min-h-64"
          />
        ) : !loaded ? (
          <StatePanel
            state="error"
            title={
              sync.status === "offline"
                ? t("config.loadOfflineTitle")
                : t("config.loadErrorTitle")
            }
            description={
              sync.status === "offline"
                ? t("config.loadOfflineDescription")
                : t("config.loadErrorDescription")
            }
            actionLabel={t("config.retry")}
            onAction={() => void sync.refresh()}
          />
        ) : (
          <div className="flex min-h-0 min-w-0 flex-col gap-4 lg:h-full">
            {sync.status === "offline" || sync.status === "error" ? (
              <>
                <div
                  role={sync.status === "error" ? "alert" : "status"}
                  className="flex min-w-0 flex-wrap items-center justify-between gap-2"
                >
                  <p className="min-w-0 text-sm text-muted-foreground">
                    {sync.status === "offline"
                      ? t("config.loadedOffline")
                      : t("config.loadedError")}
                  </p>
                  <Button
                    type="button"
                    variant="outline"
                    onClick={() => void sync.refresh()}
                  >
                    <RefreshCw data-icon="inline-start" />
                    {t("config.retry")}
                  </Button>
                </div>
                <Separator />
              </>
            ) : null}
            <div className="lg:hidden">
              <Label htmlFor="config-group-select" className="sr-only">
                {t("config.groupSelect")}
              </Label>
              <Select
                items={sectionItems}
                value={activePath || sectionItems[0]?.value || null}
                onValueChange={(path) => {
                  const section = sections.find((item) => item.path === path);
                  if (section) {
                    scheduleSectionFocus(section.id, section.path);
                  }
                }}
              >
                <SelectTrigger
                  id="config-group-select"
                  aria-label={t("config.groupSelect")}
                  className="w-full"
                >
                  <SelectValue />
                </SelectTrigger>
                <SelectContent
                  align="start"
                  alignItemWithTrigger={false}
                  className="w-[var(--anchor-width)] min-w-[var(--anchor-width)]"
                >
                  <SelectGroup>
                    {sectionItems.map((item) => (
                      <SelectItem key={item.value} value={item.value}>
                        {item.label}
                      </SelectItem>
                    ))}
                  </SelectGroup>
                </SelectContent>
              </Select>
            </div>

            <div className="grid min-w-0 gap-5 lg:min-h-0 lg:flex-1 lg:grid-cols-[minmax(11rem,14rem)_minmax(0,1fr)]">
              <nav
                ref={groupNavigationRef}
                aria-label={t("config.groupNavigation")}
                className="hidden min-w-0 overflow-y-auto overscroll-contain lg:block lg:h-full lg:min-h-0"
              >
                <div className="flex min-w-0 flex-col gap-1 pr-2">
                  {sections.map((section) => (
                    <Button
                      key={section.path}
                      type="button"
                      aria-current={
                        activePath === section.path ? "true" : undefined
                      }
                      data-config-nav-path={section.path}
                      variant={activePath === section.path ? "secondary" : "ghost"}
                      className={cn(
                        "h-auto min-h-8 min-w-0 justify-start whitespace-normal text-left",
                        selectionStateVariants({
                          kind: "current-item",
                          selected: activePath === section.path,
                        }),
                      )}
                      onClick={() => goToSection(section.id, section.path)}
                    >
                      {section.label}
                    </Button>
                  ))}
                </div>
              </nav>

              <div
                ref={configFormRef}
                data-slot="config-form-scroll"
                className="flex min-w-0 flex-col gap-5 lg:min-h-0 lg:overflow-y-auto lg:overscroll-contain lg:pr-2"
                onScroll={handleSectionScroll}
              >
                {sections.length === 0 ? (
                  <p className="py-10 text-center text-sm text-muted-foreground">
                    {t("config.noResults")}
                  </p>
                ) : (
                  sections.map((section, index) => (
                    <div key={section.path} className="flex min-w-0 flex-col gap-5">
                      <div
                        id={section.id}
                        role="group"
                        aria-label={section.label}
                        tabIndex={-1}
                        data-config-section={section.path}
                        className="min-w-0 scroll-mt-4 outline-none focus-visible:ring-2 focus-visible:ring-ring"
                      >
                        <ConfigField
                          path={section.path}
                          node={section.node}
                          value={getConfigValue(sync.draft, section.path)}
                          onChange={sync.changeField}
                          providerOptions={providerOptions}
                          disabled={controlsDisabled}
                          fieldErrors={sync.fieldErrors}
                          defaultProviderLabel={t("config.defaultProvider")}
                          targetPath={highlightedPath}
                        />
                      </div>
                      {index < sections.length - 1 ? <Separator /> : null}
                    </div>
                  ))
                )}
              </div>
            </div>
          </div>
        )}
      </PageContent>

      <ConfigConflictDialog
        open={sync.status === "conflict"}
        localPaths={sync.localPaths}
        remotePaths={sync.remotePaths}
        overlapPaths={sync.overlapPaths}
        remoteReady={Boolean(sync.remoteConfig)}
        labels={{
          title: t("config.conflict.title"),
          description: t("config.conflict.description"),
          localChanges: t("config.conflict.local"),
          remoteChanges: t("config.conflict.remote"),
          overlapChanges: t("config.conflict.overlap"),
          loadRemote: t("config.conflict.loadRemote"),
          reapplyLocal: t("config.conflict.reapplyLocal"),
          waitingRemote: t("config.conflict.waitingRemote"),
          refreshRemote: t("config.conflict.refreshRemote"),
        }}
        onAcceptRemote={sync.acceptRemote}
        onRebaseRemote={sync.rebaseRemote}
        onRefresh={() => void sync.refresh()}
      />
    </PageFrame>
  );
}
