import { LoaderCircle, RefreshCw, Save, ShieldCheck } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { ConfigConflictDialog } from "@/components/config/ConfigConflictDialog";
import {
  PageContent,
  PageFrame,
  PageHeader,
  PageToolbar,
} from "@/components/layout/PageLayout";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { StatePanel } from "@/components/ui/StatePanel";
import { Switch } from "@/components/ui/switch";
import { useConfigSync } from "@/hooks/useConfigSync";
import { useI18n } from "@/hooks/useI18n";
import { getConfigValue } from "@/lib/config";
import type { Translate } from "@/lib/i18n";
import type {
  ConfigSyncStatus,
  ConfigValue,
  GateConfigData,
  GateProfileData,
} from "@/types/config";
import { ChecksSection } from "@/components/system/gate/ChecksSection";
import { DispositionSection } from "@/components/system/gate/DispositionSection";
import { DryRunPanel } from "@/components/system/gate/DryRunPanel";
import { JudgeSection } from "@/components/system/gate/JudgeSection";
import { ProfileSection } from "@/components/system/gate/ProfileSection";
import { RulesSection } from "@/components/system/gate/RulesSection";
import { ThresholdsSection } from "@/components/system/gate/ThresholdsSection";
import { WordListsSection } from "@/components/system/gate/WordListsSection";
import {
  validateJudgeTemplate,
  validateThresholdCross,
} from "@/components/system/gate/validation";

export interface GatePageProps {
  showToast?: (
    message: string,
    type?: "success" | "error" | "info",
  ) => void;
  onDirtyChange?: (dirty: boolean) => void;
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

/** quality.gate 复合分支形状守卫：schema 只表达两片标量叶。 */
function isGateConfig(value: ConfigValue): value is GateConfigData {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    return false;
  }
  const record = value as Record<string, unknown>;
  return (
    typeof record.enabled === "boolean" &&
    typeof record.default_profile === "string" &&
    Array.isArray(record.bindings) &&
    Array.isArray(record.profiles)
  );
}

/** 门禁管理页：profile 与绑定、检查、阈值、词表、处置、Judge、规则与 dry-run。 */
export function GatePage({ showToast, onDirtyChange }: GatePageProps) {
  const { t } = useI18n();
  const sync = useConfigSync();
  const [activeProfile, setActiveProfile] = useState("");
  const dirtyOwnerRef = useRef<GatePageProps["onDirtyChange"]>(undefined);
  const previousStatusRef = useRef(sync.status);

  const gateConfig = useMemo(() => {
    if (!sync.draft) return null;
    return isGateConfig(getConfigValue(sync.draft, "quality.gate"))
      ? (getConfigValue(sync.draft, "quality.gate") as GateConfigData)
      : null;
  }, [sync.draft]);

  const loaded = Boolean(gateConfig);
  const dirty = sync.dirtyPaths.length > 0;

  useEffect(() => {
    if (!gateConfig) return;
    if (!gateConfig.profiles.some((profile) => profile.name === activeProfile)) {
      setActiveProfile(
        gateConfig.default_profile &&
          gateConfig.profiles.some(
            (profile) => profile.name === gateConfig.default_profile,
          )
          ? gateConfig.default_profile
          : (gateConfig.profiles[0]?.name ?? ""),
      );
    }
  }, [activeProfile, gateConfig]);

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
      showToast?.(t("gate.appliedToast"), "success");
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

  const changeGate = useCallback(
    (next: GateConfigData) => {
      sync.changeField("quality.gate", next);
    },
    [sync],
  );

  const changeActiveProfile = useCallback(
    (patch: Partial<GateProfileData>) => {
      if (!gateConfig) return;
      changeGate({
        ...gateConfig,
        profiles: gateConfig.profiles.map((profile) =>
          profile.name === activeProfile ? { ...profile, ...patch } : profile,
        ),
      });
    },
    [activeProfile, changeGate, gateConfig],
  );

  /** 页面级校验：阈值交叉与 Judge 模板（逐 profile 聚合）。 */
  const invalid = useMemo(() => {
    if (!gateConfig) return false;
    return gateConfig.profiles.some(
      (profile) =>
        validateThresholdCross(profile.thresholds) ||
        (profile.judge.prompt_template.length > 0 &&
          validateJudgeTemplate(profile.judge.prompt_template) !== null),
    );
  }, [gateConfig]);

  const busy =
    sync.status === "applying" || sync.status === "reloading";
  const controlsDisabled = busy || sync.status === "conflict";
  const applyDisabled =
    !loaded ||
    !dirty ||
    invalid ||
    sync.status === "loading" ||
    busy ||
    sync.status === "conflict";

  const activeProfileData = gateConfig?.profiles.find(
    (profile) => profile.name === activeProfile,
  );
  const profileItems =
    gateConfig?.profiles.map((profile) => ({
      label: profile.name,
      value: profile.name,
    })) ?? [];

  return (
    <PageFrame variant="dense" aria-label={t("nav.gate")}>
      <PageHeader
        title={t("gate.title")}
        description={t("gate.subtitle")}
        icon={<ShieldCheck aria-hidden="true" />}
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
            </dl>
          ) : undefined
        }
      />

      <PageToolbar aria-label={t("gate.title")}>
        <div className="flex min-h-8 min-w-0 items-center gap-2 px-1">
          <Switch
            id="gate-enabled"
            size="sm"
            checked={gateConfig?.enabled ?? false}
            onCheckedChange={(checked) => {
              if (gateConfig) changeGate({ ...gateConfig, enabled: checked });
            }}
            disabled={!loaded || controlsDisabled}
          />
          <Label htmlFor="gate-enabled" className="min-w-0 truncate">
            {t("gate.enabledLabel")}
          </Label>
        </div>
        <div className="flex min-h-8 min-w-0 items-center gap-2">
          <Label
            htmlFor="gate-profile-select"
            className="shrink-0 text-xs font-medium text-muted-foreground"
          >
            {t("gate.profileSelect")}
          </Label>
          <Select
            items={profileItems}
            value={activeProfile || null}
            disabled={!loaded || controlsDisabled}
            onValueChange={(value) => {
              if (value) setActiveProfile(value);
            }}
          >
            <SelectTrigger
              id="gate-profile-select"
              aria-label={t("gate.profileSelect")}
              className="min-w-36"
            >
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectGroup>
                {profileItems.map((item) => (
                  <SelectItem key={item.value} value={item.value}>
                    {item.label}
                  </SelectItem>
                ))}
              </SelectGroup>
            </SelectContent>
          </Select>
        </div>
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
          {busy ? t("gate.saving") : t("gate.save")}
        </Button>
      </PageToolbar>

      <PageContent width="constrained">
        {!loaded && sync.status === "loading" ? (
          <StatePanel
            state="loading"
            title={t("gate.loading")}
            className="min-h-64"
          />
        ) : !loaded ? (
          <StatePanel
            state="error"
            title={
              sync.status === "offline"
                ? t("config.loadOfflineTitle")
                : t("gate.loadErrorTitle")
            }
            description={
              sync.status === "offline"
                ? t("config.loadOfflineDescription")
                : t("gate.loadErrorDescription")
            }
            actionLabel={t("gate.retry")}
            onAction={() => void sync.refresh()}
          />
        ) : (
          <div className="flex min-w-0 flex-col gap-5">
            {invalid && dirty ? (
              <Alert variant="destructive" className="rounded-md">
                <AlertDescription>
                  {t("gate.invalidSaveHint")}
                </AlertDescription>
              </Alert>
            ) : null}
            {gateConfig && activeProfileData ? (
              <>
                <ProfileSection
                  config={gateConfig}
                  disabled={controlsDisabled}
                  activeProfile={activeProfile}
                  onConfigChange={changeGate}
                  onSelectProfile={setActiveProfile}
                />
                <ChecksSection
                  profile={activeProfileData}
                  disabled={controlsDisabled}
                  onChange={changeActiveProfile}
                />
                <ThresholdsSection
                  profile={activeProfileData}
                  disabled={controlsDisabled}
                  onChange={changeActiveProfile}
                />
                <WordListsSection
                  profile={activeProfileData}
                  disabled={controlsDisabled}
                  onChange={changeActiveProfile}
                />
                <DispositionSection
                  profile={activeProfileData}
                  disabled={controlsDisabled}
                  onChange={changeActiveProfile}
                />
                <JudgeSection
                  profile={activeProfileData}
                  disabled={controlsDisabled}
                  onChange={changeActiveProfile}
                  promptDefault={sync.promptDefaults?.gate_judge ?? ""}
                />
                <RulesSection
                  profile={activeProfileData}
                  disabled={controlsDisabled}
                  onChange={changeActiveProfile}
                />
                <DryRunPanel config={gateConfig} disabled={controlsDisabled} />
              </>
            ) : null}
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
