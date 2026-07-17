import { useEffect, useRef } from "react";
import { Loader2 } from "lucide-react";

import { ConfigConflictDialog } from "@/components/config/ConfigConflictDialog";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/Button";
import {
  Field,
  FieldContent,
  FieldDescription,
  FieldError,
  FieldGroup,
  FieldLabel,
  FieldLegend,
  FieldSet,
  FieldTitle,
} from "@/components/ui/field";
import { Input } from "@/components/ui/input";
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
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useI18n } from "@/hooks/useI18n";
import type { useInjectionStrategyConfig } from "@/hooks/useInjectionStrategyConfig";
import { dashboardLocale, formatDashboardNumber } from "@/lib/i18n";
import type { Translate } from "@/lib/i18n";
import {
  INJECTION_STRATEGY_PATHS,
  type InjectionDeliveryMode,
  type InjectionPresetName,
  type InjectionRoutingMode,
  type InjectionStrategyCatalog,
  type InjectionStrategyDraft,
} from "@/types/injection";

interface InjectionConfigTabProps {
  config: ReturnType<typeof useInjectionStrategyConfig>;
  showToast: (
    message: string,
    type?: "success" | "error" | "info",
  ) => void;
}

type ConfigHook = ReturnType<typeof useInjectionStrategyConfig>;
type PresetField =
  | "manualPreset"
  | "autoFallbackPreset"
  | "hybridBasePreset"
  | "hybridMinPreset"
  | "hybridMaxPreset";
type NumericField =
  | "budgetChars"
  | "memoryMaxChars"
  | "metadataMaxChars"
  | "maxRows";
type ToggleField =
  | "includeKeyFacts"
  | "includeTopics"
  | "includeParticipants"
  | "compactHeader";

interface PresetSelectProps {
  catalog: InjectionStrategyCatalog;
  config: ConfigHook;
  draft: InjectionStrategyDraft;
  field: PresetField;
  t: Translate;
}

interface NumberFieldProps {
  config: ConfigHook;
  draft: InjectionStrategyDraft;
  field: NumericField;
  max: number;
  min: number;
  step: number;
  description: string;
  t: Translate;
}

interface ToggleFieldProps {
  config: ConfigHook;
  draft: InjectionStrategyDraft;
  field: ToggleField;
  t: Translate;
}

function fieldError(
  config: ConfigHook,
  field: keyof InjectionStrategyDraft,
): string | undefined {
  return config.errors[field]
    ?? config.serverFieldErrors[INJECTION_STRATEGY_PATHS[field]];
}

function PresetSelect({
  catalog,
  config,
  draft,
  field,
  t,
}: PresetSelectProps) {
  const errorKey = fieldError(config, field);
  const items = catalog.presets.map((preset) => ({
    label: t(`injection.preset.${preset.name}`),
    value: preset.name,
  }));

  return (
    <Field data-invalid={Boolean(errorKey)}>
      <FieldLabel htmlFor={`injection-${field}`}>
        {t(`injection.field.${field}`)}
      </FieldLabel>
      <Select
        items={items}
        value={draft[field]}
        onValueChange={(value) => {
          if (value) config.change(field, value as InjectionPresetName);
        }}
      >
        <SelectTrigger
          id={`injection-${field}`}
          aria-label={t(`injection.field.${field}`)}
          aria-describedby={`injection-${field}-help`}
          aria-invalid={Boolean(errorKey)}
          className="w-full"
        >
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          <SelectGroup>
            {items.map((item) => (
              <SelectItem key={item.value} value={item.value}>
                {item.label}
              </SelectItem>
            ))}
          </SelectGroup>
        </SelectContent>
      </Select>
      <FieldDescription id={`injection-${field}-help`}>
        {t("injection.help.preset")}
      </FieldDescription>
      {errorKey ? <FieldError>{t(errorKey)}</FieldError> : null}
    </Field>
  );
}

function NumberField({
  config,
  description,
  draft,
  field,
  max,
  min,
  step,
  t,
}: NumberFieldProps) {
  const errorKey = fieldError(config, field);

  return (
    <Field data-invalid={Boolean(errorKey)}>
      <FieldLabel htmlFor={`injection-${field}`}>
        {t(`injection.field.${field}`)}
      </FieldLabel>
      <Input
        id={`injection-${field}`}
        aria-label={t(`injection.field.${field}`)}
        aria-invalid={Boolean(errorKey)}
        type="number"
        min={min}
        max={max}
        step={step}
        value={draft[field]}
        onChange={(event) => {
          const value = event.currentTarget.valueAsNumber;
          if (Number.isFinite(value)) config.change(field, Math.trunc(value));
        }}
      />
      <FieldDescription>{description}</FieldDescription>
      {errorKey ? <FieldError>{t(errorKey)}</FieldError> : null}
    </Field>
  );
}

function ToggleSetting({
  config,
  draft,
  field,
  t,
}: ToggleFieldProps) {
  return (
    <Field orientation="horizontal">
      <FieldContent>
        <FieldTitle>{t(`injection.field.${field}`)}</FieldTitle>
      </FieldContent>
      <Switch
        checked={draft[field]}
        onCheckedChange={(checked) => config.change(field, checked)}
        aria-label={t(`injection.field.${field}`)}
      />
    </Field>
  );
}

function PresetComparison({
  catalog,
  locale,
  t,
}: {
  catalog: InjectionStrategyCatalog;
  locale: string;
  t: Translate;
}) {
  return (
    <div className="max-w-full overflow-x-auto rounded-lg border">
      <Table
        aria-label={t("injection.config.presetComparison")}
        className="min-w-[48rem]"
      >
        <TableHeader>
          <TableRow>
            <TableHead>{t("injection.preset.name")}</TableHead>
            <TableHead>{t("injection.preset.autoInject")}</TableHead>
            <TableHead>{t("injection.preset.budget")}</TableHead>
            <TableHead>{t("injection.preset.maxMemories")}</TableHead>
            <TableHead>{t("injection.preset.contentLevel")}</TableHead>
            <TableHead>{t("injection.preset.toolFallback")}</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {catalog.presets.map((preset) => (
            <TableRow key={preset.name}>
              <TableCell>{t(`injection.preset.${preset.name}`)}</TableCell>
              <TableCell>
                {preset.auto_inject ? t("common.yes") : t("common.no")}
              </TableCell>
              <TableCell>
                {formatDashboardNumber(preset.memory_budget_chars, locale)}
              </TableCell>
              <TableCell>
                {formatDashboardNumber(preset.max_memories, locale)}
              </TableCell>
              <TableCell>{t(`injection.content.${preset.content_level}`)}</TableCell>
              <TableCell>
                {preset.allow_tool_fallback ? t("common.yes") : t("common.no")}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}

export function InjectionConfigTab({
  config,
  showToast,
}: InjectionConfigTabProps) {
  const { t, currentLang } = useI18n();
  const locale = dashboardLocale(currentLang());
  const previousStatusRef = useRef(config.status);
  const unsafeDeliveryReportedRef = useRef(false);
  const hasUsableConfig = Boolean(config.catalog && config.draft);
  const hasUnsafeDelivery = Boolean(
    config.catalog?.deliveries.some(
      (delivery) => String(delivery) === "system_prompt",
    ),
  );

  useEffect(() => {
    const previousStatus = previousStatusRef.current;
    previousStatusRef.current = config.status;
    if (
      (previousStatus === "applying" || previousStatus === "reloading")
      && config.status === "synced"
    ) {
      showToast(t("config.appliedToast"), "success");
    }
    if (config.status === "error" && previousStatus !== "error") {
      showToast(t("config.status.error"), "error");
    }
  }, [config.status, showToast, t]);

  useEffect(() => {
    if (hasUnsafeDelivery && !unsafeDeliveryReportedRef.current) {
      unsafeDeliveryReportedRef.current = true;
      showToast(t("config.status.error"), "error");
    } else if (!hasUnsafeDelivery) {
      unsafeDeliveryReportedRef.current = false;
    }
  }, [hasUnsafeDelivery, showToast, t]);

  if (!hasUsableConfig && (
    config.catalogStatus === "loading" || config.status === "loading"
  )) {
    return (
      <StatePanel state="loading" title={t("injection.state.loading")} />
    );
  }
  if (!config.catalog || !config.draft) {
    const catalogFailed = config.catalogStatus === "error";
    return (
      <StatePanel
        state="error"
        title={t("injection.state.error")}
        description={config.catalogError ?? undefined}
        actionLabel={t("common.retry")}
        onAction={catalogFailed
          ? () => { void config.retryCatalog(); }
          : () => { void config.refresh(); }}
      />
    );
  }

  const draft = config.draft;
  const catalog = config.catalog;
  const presetSelect = (field: PresetField) => (
    <PresetSelect
      key={field}
      catalog={catalog}
      config={config}
      draft={draft}
      field={field}
      t={t}
    />
  );
  const deliveryOptions = catalog.deliveries.filter(
    (delivery) => String(delivery) !== "system_prompt",
  );
  const deliveryItems = deliveryOptions.map((value) => ({
    label: t(`injection.delivery.${value}`),
    value,
  }));
  const retentionOptions = catalog.retention_options.filter(
    (value): value is 0 | 7 | 30 | 90 | 180 => (
      value === 0 || value === 7 || value === 30 || value === 90 || value === 180
    ),
  );
  const retentionLabel = (value: number) => value === 0
    ? t("injection.retention.never")
    : formatDashboardNumber(value, locale);
  const retentionItems = retentionOptions.map((value) => ({
    label: retentionLabel(value),
    value: String(value),
  }));
  const deliveryError = fieldError(config, "deliveryOverride");
  const retentionError = fieldError(config, "retentionDays");
  const busy = config.status === "applying" || config.status === "reloading";
  const save = async () => {
    await config.save();
  };

  return (
    <section
      role="region"
      aria-label={t("injection.tabs.config")}
      className="flex min-w-0 flex-col gap-5"
    >
      {config.status === "offline" || config.status === "error" ? (
        <Alert
          role={config.status === "error" ? "alert" : "status"}
          variant={config.status === "error" ? "destructive" : "default"}
        >
          <AlertDescription>
            {t(`config.status.${config.status}`)}
          </AlertDescription>
        </Alert>
      ) : null}

      <form
        aria-label={t("injection.tabs.config")}
        className="flex min-w-0 flex-col gap-6"
        onSubmit={(event) => event.preventDefault()}
      >
        <FieldSet className="rounded-lg border p-4">
          <FieldLegend>{t("injection.config.routing")}</FieldLegend>
          <Tabs
            value={draft.routingMode}
            onValueChange={(value) => config.change(
              "routingMode",
              value as InjectionRoutingMode,
            )}
          >
            <TabsList aria-label={t("injection.field.routingMode")}>
              <TabsTrigger value="manual">{t("injection.mode.manual")}</TabsTrigger>
              <TabsTrigger value="auto">{t("injection.mode.auto")}</TabsTrigger>
              <TabsTrigger value="hybrid">{t("injection.mode.hybrid")}</TabsTrigger>
            </TabsList>
          </Tabs>
          {draft.routingMode === "manual" ? presetSelect("manualPreset") : null}
          {draft.routingMode === "auto" ? presetSelect("autoFallbackPreset") : null}
          {draft.routingMode === "hybrid" ? (
            <FieldGroup className="grid gap-4 md:grid-cols-3">
              {presetSelect("hybridBasePreset")}
              {presetSelect("hybridMinPreset")}
              {presetSelect("hybridMaxPreset")}
            </FieldGroup>
          ) : null}
        </FieldSet>

        <FieldSet className="min-w-0 rounded-lg border p-4">
          <FieldLegend>{t("injection.config.presetComparison")}</FieldLegend>
          <PresetComparison catalog={catalog} locale={locale} t={t} />
        </FieldSet>

        <FieldSet className="rounded-lg border p-4">
          <FieldLegend>{t("injection.config.delivery")}</FieldLegend>
          <Field data-invalid={Boolean(deliveryError)}>
            <FieldLabel htmlFor="injection-deliveryOverride">
              {t("injection.field.deliveryOverride")}
            </FieldLabel>
            <Select
              items={deliveryItems}
              value={draft.deliveryOverride}
              onValueChange={(value) => {
                if (value && String(value) !== "system_prompt") {
                  config.change("deliveryOverride", value as InjectionDeliveryMode);
                }
              }}
            >
              <SelectTrigger
                id="injection-deliveryOverride"
                aria-label={t("injection.field.deliveryOverride")}
                aria-invalid={Boolean(deliveryError)}
                className="w-full"
              >
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectGroup>
                  {deliveryItems.map((item) => (
                    <SelectItem key={item.value} value={item.value}>
                      {item.label}
                    </SelectItem>
                  ))}
                </SelectGroup>
              </SelectContent>
            </Select>
            {deliveryError ? <FieldError>{t(deliveryError)}</FieldError> : null}
          </Field>
        </FieldSet>

        <FieldSet className="rounded-lg border p-4">
          <FieldLegend>{t("injection.config.advanced")}</FieldLegend>
          <Field orientation="horizontal">
            <FieldContent>
              <FieldTitle>{t("injection.field.overridesEnabled")}</FieldTitle>
              <FieldDescription>
                {t("injection.help.overridesEnabled")}
              </FieldDescription>
            </FieldContent>
            <Switch
              checked={draft.overridesEnabled}
              onCheckedChange={(checked) => config.change(
                "overridesEnabled",
                checked,
              )}
              aria-label={t("injection.field.overridesEnabled")}
            />
          </Field>
          {draft.overridesEnabled ? (
            <>
              <FieldGroup className="grid gap-4 md:grid-cols-3">
                <NumberField
                  config={config}
                  draft={draft}
                  field="budgetChars"
                  min={0}
                  max={10_000}
                  step={1}
                  description={t("injection.help.zeroUsesPreset")}
                  t={t}
                />
                <NumberField
                  config={config}
                  draft={draft}
                  field="memoryMaxChars"
                  min={0}
                  max={2_000}
                  step={1}
                  description={t("injection.help.zeroUsesPreset")}
                  t={t}
                />
                <NumberField
                  config={config}
                  draft={draft}
                  field="metadataMaxChars"
                  min={0}
                  max={500}
                  step={1}
                  description={t("injection.help.zeroUsesPreset")}
                  t={t}
                />
              </FieldGroup>
              <FieldGroup className="grid gap-4 md:grid-cols-2">
                {([
                  "includeKeyFacts",
                  "includeTopics",
                  "includeParticipants",
                  "compactHeader",
                ] as ToggleField[]).map((field) => (
                  <ToggleSetting
                    key={field}
                    config={config}
                    draft={draft}
                    field={field}
                    t={t}
                  />
                ))}
              </FieldGroup>
            </>
          ) : null}
        </FieldSet>

        <FieldSet className="rounded-lg border p-4">
          <FieldLegend>{t("injection.config.retention")}</FieldLegend>
          <FieldGroup className="grid gap-4 md:grid-cols-2">
            <Field data-invalid={Boolean(retentionError)}>
              <FieldLabel htmlFor="injection-retentionDays">
                {t("injection.field.retentionDays")}
              </FieldLabel>
              <Select
                items={retentionItems}
                value={String(draft.retentionDays)}
                onValueChange={(value) => {
                  if (!value) return;
                  const days = Number(value);
                  if (retentionOptions.includes(days as never)) {
                    config.change("retentionDays", days as 0 | 7 | 30 | 90 | 180);
                  }
                }}
              >
                <SelectTrigger
                  id="injection-retentionDays"
                  aria-label={t("injection.field.retentionDays")}
                  aria-invalid={Boolean(retentionError)}
                  className="w-full"
                >
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectGroup>
                    {retentionItems.map((item) => (
                      <SelectItem key={item.value} value={item.value}>
                        {item.label}
                      </SelectItem>
                    ))}
                  </SelectGroup>
                </SelectContent>
              </Select>
              <FieldDescription>{t("injection.help.retention")}</FieldDescription>
              {retentionError ? <FieldError>{t(retentionError)}</FieldError> : null}
            </Field>
            <NumberField
              config={config}
              draft={draft}
              field="maxRows"
              min={1_000}
              max={1_000_000}
              step={1_000}
              description={t("injection.help.retention")}
              t={t}
            />
          </FieldGroup>
        </FieldSet>

        <div className="flex flex-wrap justify-end gap-2">
          <Button
            type="button"
            variant="outline"
            onClick={config.restoreDefaults}
          >
            {t("injection.actions.restoreDefaults")}
          </Button>
          <Button
            type="button"
            variant="outline"
            disabled={!config.dirty}
            onClick={config.discard}
          >
            {t("injection.actions.discard")}
          </Button>
          <Button
            type="button"
            disabled={!config.canSave}
            onClick={() => { void save(); }}
          >
            {busy ? <Loader2 data-icon="inline-start" className="animate-spin" /> : null}
            {t(config.status === "applying"
              ? "injection.actions.saving"
              : "injection.actions.save")}
          </Button>
        </div>
      </form>

      <ConfigConflictDialog
        open={config.status === "conflict"}
        localPaths={config.localPaths}
        remotePaths={config.remotePaths}
        overlapPaths={config.overlapPaths}
        remoteReady={config.remoteReady}
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
        onAcceptRemote={config.acceptRemote}
        onRebaseRemote={config.rebaseRemote}
        onRefresh={() => { void config.refresh(); }}
      />
    </section>
  );
}
