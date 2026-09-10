import { LoaderCircle, Play } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/Button";
import {
  Field,
  FieldContent,
  FieldDescription,
  FieldGroup,
  FieldLegend,
  FieldSet,
  FieldTitle,
} from "@/components/ui/field";
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
import { Textarea } from "@/components/ui/textarea";
import { useI18n } from "@/hooks/useI18n";
import { apiPost, unwrapApiData } from "@/lib/bridge";
import type {
  GateChatType,
  GateConfigData,
  GateDisposition,
} from "@/types/config";
import { GATE_CHAT_TYPE_LABEL_KEYS } from "./validation";

export interface DryRunPanelProps {
  config: GateConfigData;
  disabled: boolean;
}

interface GateDryRunResult {
  profile: string;
  quality: "low" | "normal";
  matched_rules: string[];
  disposition: GateDisposition;
}

const DISPOSITION_LABEL_KEYS: Record<string, string> = {
  quarantine: "gate.disposition.quarantine",
  discard: "gate.disposition.discard",
  mark_write: "gate.disposition.markWrite",
};

const CHAT_TYPES: Array<{ value: GateChatType | ""; labelKey: string }> = [
  { value: "private", labelKey: GATE_CHAT_TYPE_LABEL_KEYS.private },
  { value: "group", labelKey: GATE_CHAT_TYPE_LABEL_KEYS.group },
];

/** Dry-run 测试区：不写库、不调 LLM，仅预览 profile 解析与处置。 */
export function DryRunPanel({ config, disabled }: DryRunPanelProps) {
  const { t } = useI18n();
  const [profile, setProfile] = useState("");
  const [chatType, setChatType] = useState<string>("private");
  const [groupId, setGroupId] = useState("");
  const [personaId, setPersonaId] = useState("");
  const [content, setContent] = useState("");
  const [summary, setSummary] = useState("");
  const [keyFacts, setKeyFacts] = useState("");
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState<GateDryRunResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  const profileItems = config.profiles.map((entry) => ({
    label: entry.name,
    value: entry.name,
  }));
  const chatTypeItems = CHAT_TYPES.map((option) => ({
    label: t(option.labelKey),
    value: option.value,
  }));

  const run = async () => {
    setRunning(true);
    setResult(null);
    setError(null);
    try {
      const response = await apiPost("gate/dry-run", {
        ...(profile ? { profile } : {}),
        chat_type: chatType,
        ...(groupId.trim() ? { group_id: groupId.trim() } : {}),
        ...(personaId.trim() ? { persona_id: personaId.trim() } : {}),
        content,
        summary,
        key_facts: keyFacts
          .split("\n")
          .map((entry) => entry.trim())
          .filter(Boolean),
      });
      setResult(unwrapApiData<GateDryRunResult>(response));
    } catch (runError) {
      setError(
        runError instanceof Error ? runError.message : String(runError),
      );
    } finally {
      setRunning(false);
    }
  };

  return (
    <FieldSet className="rounded-lg border p-4">
      <FieldLegend>{t("gate.dryrun.title")}</FieldLegend>
      <p className="text-sm text-muted-foreground">{t("gate.help.dryrun")}</p>
      <FieldGroup>
        <FieldDescription>{t("gate.dryrun.hint")}</FieldDescription>
        <div className="grid min-w-0 gap-3 sm:grid-cols-2">
          <Field>
            <FieldContent>
              <FieldTitle>{t("gate.dryrun.profile")}</FieldTitle>
            </FieldContent>
            <Select
              items={[
                { label: t("gate.dryrun.none"), value: "" },
                ...profileItems,
              ]}
              value={profile}
              disabled={disabled || running}
              onValueChange={(value) => setProfile(value ?? "")}
            >
              <SelectTrigger
                aria-label={t("gate.dryrun.profile")}
                size="sm"
                className="w-full"
              >
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectGroup>
                  <SelectItem value="">{t("gate.dryrun.none")}</SelectItem>
                  {profileItems.map((item) => (
                    <SelectItem key={item.value} value={item.value}>
                      {item.label}
                    </SelectItem>
                  ))}
                </SelectGroup>
              </SelectContent>
            </Select>
          </Field>
          <Field>
            <FieldContent>
              <FieldTitle>{t("gate.dryrun.contextLabel")}</FieldTitle>
            </FieldContent>
            <Select
              items={chatTypeItems}
              value={chatType}
              disabled={disabled || running}
              onValueChange={(value) => {
                if (value) setChatType(value);
              }}
            >
              <SelectTrigger
                aria-label={t("gate.dryrun.chatType")}
                size="sm"
                className="w-full"
              >
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectGroup>
                  {chatTypeItems.map((item) => (
                    <SelectItem key={item.value} value={item.value}>
                      {item.label}
                    </SelectItem>
                  ))}
                </SelectGroup>
              </SelectContent>
            </Select>
          </Field>
          <Field>
            <Label
              htmlFor="gate-dryrun-group-id"
              className="text-xs font-medium text-muted-foreground"
            >
              {t("gate.dryrun.groupId")}
            </Label>
            <Input
              id="gate-dryrun-group-id"
              aria-label={t("gate.dryrun.groupId")}
              value={groupId}
              disabled={disabled || running}
              onChange={(event) => setGroupId(event.currentTarget.value)}
              className="h-8"
            />
          </Field>
          <Field>
            <Label
              htmlFor="gate-dryrun-persona-id"
              className="text-xs font-medium text-muted-foreground"
            >
              {t("gate.dryrun.personaId")}
            </Label>
            <Input
              id="gate-dryrun-persona-id"
              aria-label={t("gate.dryrun.personaId")}
              value={personaId}
              disabled={disabled || running}
              onChange={(event) => setPersonaId(event.currentTarget.value)}
              className="h-8"
            />
          </Field>
        </div>
        <Field>
          <Label
            htmlFor="gate-dryrun-content"
            className="text-xs font-medium text-muted-foreground"
          >
            {t("gate.dryrun.content")}
          </Label>
          <Textarea
            id="gate-dryrun-content"
            aria-label={t("gate.dryrun.content")}
            value={content}
            disabled={disabled || running}
            rows={4}
            onChange={(event) => setContent(event.currentTarget.value)}
          />
        </Field>
        <div className="grid min-w-0 gap-3 sm:grid-cols-2">
          <Field>
            <Label
              htmlFor="gate-dryrun-summary"
              className="text-xs font-medium text-muted-foreground"
            >
              {t("gate.dryrun.summary")}
            </Label>
            <Input
              id="gate-dryrun-summary"
              aria-label={t("gate.dryrun.summary")}
              value={summary}
              disabled={disabled || running}
              onChange={(event) => setSummary(event.currentTarget.value)}
              className="h-8"
            />
          </Field>
          <Field>
            <Label
              htmlFor="gate-dryrun-key-facts"
              className="text-xs font-medium text-muted-foreground"
            >
              {t("gate.dryrun.keyFacts")}
            </Label>
            <Textarea
              id="gate-dryrun-key-facts"
              aria-label={t("gate.dryrun.keyFacts")}
              value={keyFacts}
              disabled={disabled || running}
              rows={2}
              onChange={(event) => setKeyFacts(event.currentTarget.value)}
            />
          </Field>
        </div>
        <div>
          <Button
            type="button"
            disabled={disabled || running || !content.trim()}
            onClick={() => void run()}
          >
            {running ? (
              <LoaderCircle data-icon="inline-start" className="animate-spin" />
            ) : (
              <Play data-icon="inline-start" />
            )}
            {running ? t("gate.dryrun.running") : t("gate.dryrun.run")}
          </Button>
        </div>
        {error ? (
          <p role="alert" className="text-sm text-destructive">
            {t("gate.dryrun.error", error)}
          </p>
        ) : null}
        {result ? (
          <dl
            role="status"
            className="grid min-w-0 gap-x-4 gap-y-2 rounded-lg border p-3 text-sm sm:grid-cols-2"
          >
            <div className="flex min-w-0 items-baseline gap-2">
              <dt className="shrink-0 text-muted-foreground">
                {t("gate.dryrun.resultProfile")}
              </dt>
              <dd className="min-w-0 truncate font-medium">{result.profile}</dd>
            </div>
            <div className="flex min-w-0 items-baseline gap-2">
              <dt className="shrink-0 text-muted-foreground">
                {t("gate.dryrun.resultQuality")}
              </dt>
              <dd className="font-medium">
                {t(`gate.dryrun.quality.${result.quality}`)}
              </dd>
            </div>
            <div className="flex min-w-0 items-baseline gap-2">
              <dt className="shrink-0 text-muted-foreground">
                {t("gate.dryrun.resultRules")}
              </dt>
              <dd className="min-w-0 break-all font-medium">
                {result.matched_rules.length > 0
                  ? result.matched_rules.join(", ")
                  : t("gate.dryrun.none")}
              </dd>
            </div>
            <div className="flex min-w-0 items-baseline gap-2">
              <dt className="shrink-0 text-muted-foreground">
                {t("gate.dryrun.resultDisposition")}
              </dt>
              <dd className="font-medium">
                {t(
                  DISPOSITION_LABEL_KEYS[result.disposition] ??
                    "gate.disposition.quarantine",
                )}
              </dd>
            </div>
          </dl>
        ) : null}
      </FieldGroup>
    </FieldSet>
  );
}
