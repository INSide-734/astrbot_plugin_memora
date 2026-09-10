import { ArrowDown, ArrowUp, Copy, Pencil, Plus, Trash2 } from "lucide-react";
import { useId, useState } from "react";

import { Button } from "@/components/ui/Button";
import {
  Field,
  FieldContent,
  FieldDescription,
  FieldError,
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
import { Separator } from "@/components/ui/separator";
import { useI18n } from "@/hooks/useI18n";
import { cn } from "@/lib/utils";
import type {
  GateBindingData,
  GateChatType,
  GateConfigData,
  GateProfileData,
} from "@/types/config";
import { validateProfileName } from "./validation";

export interface ProfileSectionProps {
  config: GateConfigData;
  disabled: boolean;
  activeProfile: string;
  onConfigChange: (config: GateConfigData) => void;
  onSelectProfile: (name: string) => void;
}

/** 生成未被占用的 profile 名（小写字母数字-/_）。 */
function nextProfileName(config: GateConfigData, base: string): string {
  const names = new Set(config.profiles.map((profile) => profile.name));
  const candidate = base.slice(0, 32);
  if (!names.has(candidate)) return candidate;
  for (let index = 2; index < 100; index += 1) {
    const suffix = `-${index}`;
    const next = `${base.slice(0, 32 - suffix.length)}${suffix}`;
    if (!names.has(next)) return next;
  }
  return `profile-${Date.now()}`;
}

function freshProfile(name: string): GateProfileData {
  return {
    name,
    checks: {
      numeric_check: true,
      negation_check: true,
      group_subject_check: true,
      quality_low_check: true,
    },
    thresholds: {
      min_deterministic_score: 0.42,
      min_judge_score: 0.08,
      min_inference_score: 0.2,
    },
    scoring: {
      token_weight: 1,
      sequence_enabled: true,
      sequence_weight: 0.7,
    },
    references: { max_references: 8 },
    quality: { min_summary_chars: 10 },
    word_lists: {
      negation_whitelist: [],
      negation_markers: { mode: "append", items: [] },
      generic_terms: { mode: "append", items: [] },
      synonym_pairs: [],
    },
    judge: { enabled: false, prompt_template: "" },
    disposition: "quarantine",
    disposition_overrides: {},
    rules: [],
  };
}

const BINDING_MAX = 50;
const CHAT_TYPES: Array<{ value: GateChatType | ""; labelKey: string }> = [
  { value: "", labelKey: "gate.profile.any" },
  { value: "private", labelKey: "gate.dryrun.chatType" },
  { value: "group", labelKey: "gate.dryrun.chatType" },
];

/** Profile 列表管理：新增/复制/重命名/删除、默认 profile 与绑定规则。 */
export function ProfileSection({
  config,
  disabled,
  activeProfile,
  onConfigChange,
  onSelectProfile,
}: ProfileSectionProps) {
  const { t } = useI18n();
  const nameInputId = useId();
  const [renaming, setRenaming] = useState<string | null>(null);
  const [renameDraft, setRenameDraft] = useState("");
  const [renameError, setRenameError] = useState<string | null>(null);

  const profileNames = config.profiles.map((profile) => profile.name);
  const defaultItems = config.profiles.map((profile) => ({
    label: profile.name,
    value: profile.name,
  }));
  const referenced = new Set(config.bindings.map((binding) => binding.profile));

  const startRename = (name: string) => {
    setRenaming(name);
    setRenameDraft(name);
    setRenameError(null);
  };

  const commitRename = () => {
    if (renaming === null) return;
    const issue = validateProfileName(
      renameDraft,
      profileNames.filter((name) => name !== renaming),
    );
    if (issue === "format") {
      setRenameError(t("gate.profile.nameHint"));
      return;
    }
    if (issue === "duplicate") {
      setRenameError(t("gate.profile.nameHint"));
      return;
    }
    onConfigChange({
      ...config,
      default_profile:
        config.default_profile === renaming ? renameDraft : config.default_profile,
      bindings: config.bindings.map((binding) =>
        binding.profile === renaming ? { ...binding, profile: renameDraft } : binding,
      ),
      profiles: config.profiles.map((profile) =>
        profile.name === renaming ? { ...profile, name: renameDraft } : profile,
      ),
    });
    if (activeProfile === renaming) onSelectProfile(renameDraft);
    setRenaming(null);
    setRenameDraft("");
  };

  const deleteProfile = (name: string) => {
    if (name === config.default_profile || referenced.has(name)) {
      return;
    }
    const profiles = config.profiles.filter((profile) => profile.name !== name);
    onConfigChange({ ...config, profiles });
    if (activeProfile === name && profiles.length > 0) {
      onSelectProfile(config.default_profile);
    }
  };

  const addProfile = () => {
    const name = nextProfileName(config, "profile");
    onConfigChange({
      ...config,
      profiles: [...config.profiles, freshProfile(name)],
    });
    onSelectProfile(name);
  };

  const duplicateProfile = (name: string) => {
    const source = config.profiles.find((profile) => profile.name === name);
    if (!source) return;
    const copy = structuredClone(source);
    copy.name = nextProfileName(config, `${name}-copy`);
    onConfigChange({ ...config, profiles: [...config.profiles, copy] });
    onSelectProfile(copy.name);
  };

  const changeBinding = (index: number, patch: Partial<GateBindingData>) => {
    onConfigChange({
      ...config,
      bindings: config.bindings.map((binding, bindingIndex) =>
        bindingIndex === index ? { ...binding, ...patch } : binding,
      ),
    });
  };

  const moveBinding = (index: number, offset: number) => {
    const target = index + offset;
    if (target < 0 || target >= config.bindings.length) return;
    const bindings = [...config.bindings];
    const [moved] = bindings.splice(index, 1);
    bindings.splice(target, 0, moved);
    onConfigChange({ ...config, bindings });
  };

  return (
    <FieldSet className="rounded-lg border p-4">
      <FieldLegend>{t("gate.profile.title")}</FieldLegend>

      <FieldGroup>
        <Field>
          <FieldContent>
            <FieldTitle>{t("gate.profile.defaultLabel")}</FieldTitle>
            <FieldDescription>{t("gate.profile.defaultHint")}</FieldDescription>
          </FieldContent>
          <Select
            items={defaultItems}
            value={config.default_profile}
            disabled={disabled}
            onValueChange={(value) => {
              if (value) onConfigChange({ ...config, default_profile: value });
            }}
          >
            <SelectTrigger
              id="gate-default-profile"
              aria-label={t("gate.profile.defaultLabel")}
              className="w-full max-w-64"
            >
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectGroup>
                {defaultItems.map((item) => (
                  <SelectItem key={item.value} value={item.value}>
                    {item.label}
                  </SelectItem>
                ))}
              </SelectGroup>
            </SelectContent>
          </Select>
        </Field>

        <div className="flex min-w-0 flex-col gap-2">
          <span className="text-sm font-medium">
            {t("gate.profile.bindingTitle")}
          </span>
          <div className="flex min-w-0 flex-col gap-3">
            {config.bindings.map((binding, index) => (
              <div
                key={index}
                className="flex min-w-0 flex-wrap items-center gap-2 rounded-md border p-2"
              >
                <div className="flex min-w-0 flex-1 flex-wrap items-center gap-2">
                  <Label
                    htmlFor={`gate-binding-profile-${index}`}
                    className="text-xs font-medium text-muted-foreground"
                  >
                    {t("gate.profile.bindingProfile")}
                  </Label>
                  <Select
                    items={defaultItems}
                    value={binding.profile}
                    disabled={disabled}
                    onValueChange={(value) => {
                      if (value) changeBinding(index, { profile: value });
                    }}
                  >
                    <SelectTrigger
                      id={`gate-binding-profile-${index}`}
                      aria-label={t("gate.profile.bindingProfile")}
                      size="sm"
                      className="min-w-32"
                    >
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectGroup>
                        {defaultItems.map((item) => (
                          <SelectItem key={item.value} value={item.value}>
                            {item.label}
                          </SelectItem>
                        ))}
                      </SelectGroup>
                    </SelectContent>
                  </Select>
                  <Label
                    htmlFor={`gate-binding-chat-${index}`}
                    className="text-xs font-medium text-muted-foreground"
                  >
                    {t("gate.profile.bindingChatType")}
                  </Label>
                  <Select
                    items={CHAT_TYPES.map((option) => ({
                      label: t(option.labelKey),
                      value: option.value,
                    }))}
                    value={binding.chat_type ?? ""}
                    disabled={disabled}
                    onValueChange={(value) => {
                      changeBinding(index, {
                        chat_type: (value || null) as GateChatType | null,
                      });
                    }}
                  >
                    <SelectTrigger
                      id={`gate-binding-chat-${index}`}
                      aria-label={t("gate.profile.bindingChatType")}
                      size="sm"
                      className="min-w-32"
                    >
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectGroup>
                        {CHAT_TYPES.map((option) => (
                          <SelectItem key={option.value || "any"} value={option.value}>
                            {t(option.labelKey)}
                          </SelectItem>
                        ))}
                      </SelectGroup>
                    </SelectContent>
                  </Select>
                  <Input
                    id={`gate-binding-group-${index}`}
                    aria-label={t("gate.profile.bindingGroupId")}
                    placeholder={t("gate.profile.bindingGroupId")}
                    value={binding.group_id ?? ""}
                    disabled={disabled}
                    onChange={(event) =>
                      changeBinding(index, {
                        group_id: event.currentTarget.value || null,
                      })
                    }
                    className="h-8 w-full min-w-28 flex-1"
                  />
                  <Input
                    id={`gate-binding-persona-${index}`}
                    aria-label={t("gate.profile.bindingPersonaId")}
                    placeholder={t("gate.profile.bindingPersonaId")}
                    value={binding.persona_id ?? ""}
                    disabled={disabled}
                    onChange={(event) =>
                      changeBinding(index, {
                        persona_id: event.currentTarget.value || null,
                      })
                    }
                    className="h-8 w-full min-w-28 flex-1"
                  />
                </div>
                <div className="flex shrink-0 items-center gap-1">
                  <Button
                    type="button"
                    variant="outline"
                    size="icon"
                    aria-label={t("gate.profile.bindingUp")}
                    disabled={disabled || index === 0}
                    onClick={() => moveBinding(index, -1)}
                  >
                    <ArrowUp aria-hidden="true" />
                  </Button>
                  <Button
                    type="button"
                    variant="outline"
                    size="icon"
                    aria-label={t("gate.profile.bindingDown")}
                    disabled={disabled || index === config.bindings.length - 1}
                    onClick={() => moveBinding(index, 1)}
                  >
                    <ArrowDown aria-hidden="true" />
                  </Button>
                  <Button
                    type="button"
                    variant="outline"
                    size="icon"
                    aria-label={t("gate.profile.bindingRemove")}
                    disabled={disabled}
                    onClick={() =>
                      onConfigChange({
                        ...config,
                        bindings: config.bindings.filter(
                          (_, bindingIndex) => bindingIndex !== index,
                        ),
                      })
                    }
                  >
                    <Trash2 aria-hidden="true" />
                  </Button>
                </div>
              </div>
            ))}
          </div>
          <div className="flex min-w-0 items-center gap-2">
            <Button
              type="button"
              variant="outline"
              disabled={disabled || config.bindings.length >= BINDING_MAX}
              onClick={() =>
                onConfigChange({
                  ...config,
                  bindings: [
                    ...config.bindings,
                    { profile: config.default_profile, chat_type: null },
                  ],
                })
              }
            >
              <Plus data-icon="inline-start" />
              {t("gate.profile.bindingAdd")}
            </Button>
            {config.bindings.length >= BINDING_MAX ? (
              <span className="text-xs text-muted-foreground">
                {t("gate.wordlists.itemLimitHint", String(BINDING_MAX))}
              </span>
            ) : null}
          </div>
        </div>

        <Separator />

        <div className="flex min-w-0 flex-col gap-2">
          <div className="flex min-w-0 flex-wrap items-center gap-2">
            <Label htmlFor={nameInputId} className="text-sm font-medium">
              {t("gate.profile.nameLabel")}
            </Label>
            <span className="text-xs text-muted-foreground">
              {t("gate.profile.nameHint")}
            </span>
          </div>
          <ul className="flex min-w-0 list-none flex-col gap-1">
            {config.profiles.map((profile) => {
              const isDefault = profile.name === config.default_profile;
              const blocked =
                isDefault || referenced.has(profile.name);
              const isRenaming = renaming === profile.name;
              return (
                <li
                  key={profile.name}
                  className={cn(
                    "flex min-w-0 flex-wrap items-center gap-2 rounded-md border px-2 py-1.5",
                    activeProfile === profile.name &&
                      "border-primary bg-primary/5",
                  )}
                >
                  {isRenaming ? (
                    <Input
                      id={nameInputId}
                      aria-label={t("gate.profile.nameLabel")}
                      value={renameDraft}
                      disabled={disabled}
                      onChange={(event) => {
                        setRenameDraft(event.currentTarget.value);
                        setRenameError(null);
                      }}
                      onKeyDown={(event) => {
                        if (event.key === "Enter") commitRename();
                        if (event.key === "Escape") setRenaming(null);
                      }}
                      className="h-8 w-full min-w-28 flex-1"
                      autoFocus
                    />
                  ) : (
                    <Button
                      type="button"
                      variant="ghost"
                      aria-current={
                        activeProfile === profile.name ? "true" : undefined
                      }
                      className="h-auto min-h-8 min-w-0 justify-start px-2 text-left"
                      disabled={disabled}
                      onClick={() => onSelectProfile(profile.name)}
                    >
                      <span className="min-w-0 truncate font-medium">
                        {profile.name}
                      </span>
                      {isDefault ? (
                        <span className="ml-2 text-xs text-muted-foreground">
                          {t("gate.profile.defaultLabel")}
                        </span>
                      ) : null}
                    </Button>
                  )}
                  {isRenaming ? (
                    <>
                      <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        disabled={disabled}
                        onClick={commitRename}
                      >
                        {t("gate.rules.save")}
                      </Button>
                      <Button
                        type="button"
                        variant="ghost"
                        size="sm"
                        disabled={disabled}
                        onClick={() => setRenaming(null)}
                      >
                        {t("gate.rules.cancel")}
                      </Button>
                    </>
                  ) : (
                    <div className="flex shrink-0 items-center gap-1">
                      <Button
                        type="button"
                        variant="outline"
                        size="icon"
                        aria-label={t("gate.profile.rename")}
                        disabled={disabled}
                        onClick={() => startRename(profile.name)}
                      >
                        <Pencil aria-hidden="true" />
                      </Button>
                      <Button
                        type="button"
                        variant="outline"
                        size="icon"
                        aria-label={t("gate.profile.duplicate")}
                        disabled={disabled}
                        onClick={() => duplicateProfile(profile.name)}
                      >
                        <Copy aria-hidden="true" />
                      </Button>
                      <Button
                        type="button"
                        variant="outline"
                        size="icon"
                        aria-label={t("gate.profile.delete")}
                        disabled={disabled || blocked}
                        onClick={() => deleteProfile(profile.name)}
                      >
                        <Trash2 aria-hidden="true" />
                      </Button>
                    </div>
                  )}
                  {isRenaming && renameError ? (
                    <FieldError>{renameError}</FieldError>
                  ) : null}
                  {blocked && !isRenaming ? (
                    <p className="w-full text-xs text-muted-foreground">
                      {t("gate.profile.deleteBlocked")}
                    </p>
                  ) : null}
                </li>
              );
            })}
          </ul>
          <div>
            <Button
              type="button"
              variant="outline"
              disabled={disabled}
              onClick={addProfile}
            >
              <Plus data-icon="inline-start" />
              {t("gate.profile.add")}
            </Button>
          </div>
        </div>
      </FieldGroup>
    </FieldSet>
  );
}
