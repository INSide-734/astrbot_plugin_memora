import { ArrowDown, ArrowUp, Pencil, Plus, Trash2 } from "lucide-react";
import { useState } from "react";

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
import { Separator } from "@/components/ui/separator";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { Switch } from "@/components/ui/switch";
import { useI18n } from "@/hooks/useI18n";
import type { GateProfileData, GateRuleData } from "@/types/config";
import {
  ActionEditor,
  actionForKind,
  actionSummary,
} from "./RuleActionEditor";
import {
  emptyGroup,
  PredicateEditor,
} from "./RuleConditionEditor";
import {
  validatePredicateDepth,
  validateRuleDraft,
  type RuleDraftIssue,
} from "./validation";

export interface RulesSectionProps {
  profile: GateProfileData;
  disabled: boolean;
  onChange: (patch: Partial<GateProfileData>) => void;
}

function nextRuleId(existing: readonly GateRuleData[]): string {
  const ids = new Set(existing.map((rule) => rule.id));
  for (let index = 1; index < 100; index += 1) {
    const candidate = `r${index}`;
    if (!ids.has(candidate)) return candidate;
  }
  return `r${Date.now()}`;
}

const ISSUE_LABEL_KEYS: Record<RuleDraftIssue, string> = {
  id_format: "gate.rules.idError",
  id_duplicate: "gate.rules.idDuplicateError",
  description_too_long: "gate.rules.descriptionHint",
  depth: "gate.rules.depthError",
  regex_invalid: "gate.rules.regexError",
  leaf_incomplete: "gate.rules.leafRequired",
  action_incomplete: "gate.rules.leafRequired",
};

interface RuleSheetProps {
  open: boolean;
  rule: GateRuleData;
  existingIds: readonly string[];
  isNew: boolean;
  disabled: boolean;
  onClose: () => void;
  onSave: (rule: GateRuleData) => void;
  onDraftChange: (rule: GateRuleData) => void;
}

function RuleSheet({
  open,
  rule,
  existingIds,
  isNew,
  disabled,
  onClose,
  onSave,
  onDraftChange,
}: RuleSheetProps) {
  const { t } = useI18n();
  const validation = validateRuleDraft(rule, existingIds);
  const label = (key: string, ...args: string[]) => t(key, ...args);
  const depthInvalid = validatePredicateDepth(rule.when);

  return (
    <Sheet
      open={open}
      onOpenChange={(next) => {
        if (!next) onClose();
      }}
    >
      <SheetContent
        showCloseButton={false}
        className="w-full max-w-full overflow-hidden sm:max-w-2xl"
      >
        <SheetHeader className="shrink-0">
          <SheetTitle>
            {isNew ? t("gate.rules.newTitle") : t("gate.rules.editTitle")}
          </SheetTitle>
          <SheetDescription>
            {t("gate.rules.condition")}
          </SheetDescription>
        </SheetHeader>
        <div className="flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto overscroll-contain px-5 py-4">
          <div className="flex min-w-0 flex-wrap gap-2">
            <Field>
              <FieldContent>
                <FieldTitle>{t("gate.rules.id")}</FieldTitle>
              </FieldContent>
              <Input
                aria-label={t("gate.rules.id")}
                value={rule.id}
                disabled={disabled}
                onChange={(event) =>
                  onDraftChange({ ...rule, id: event.currentTarget.value })
                }
                className="h-8 w-40"
              />
            </Field>
            <Field className="flex-1">
              <FieldContent>
                <FieldTitle>{t("gate.rules.description")}</FieldTitle>
                <FieldDescription>
                  {t("gate.rules.descriptionHint")}
                </FieldDescription>
              </FieldContent>
              <Input
                aria-label={t("gate.rules.description")}
                value={rule.description}
                disabled={disabled}
                onChange={(event) =>
                  onDraftChange({
                    ...rule,
                    description: event.currentTarget.value,
                  })
                }
                className="h-8"
              />
            </Field>
            <Field orientation="horizontal">
              <FieldContent>
                <FieldTitle>{t("gate.rules.enabled")}</FieldTitle>
              </FieldContent>
              <Switch
                checked={rule.enabled}
                disabled={disabled}
                onCheckedChange={(checked) =>
                  onDraftChange({ ...rule, enabled: checked })
                }
                aria-label={t("gate.rules.enabled")}
              />
            </Field>
          </div>
          <div className="flex min-w-0 flex-col gap-2">
            <span className="text-sm font-medium">
              {t("gate.rules.condition")}
            </span>
            <PredicateEditor
              node={rule.when}
              label={label}
              disabled={disabled}
              onChange={(when) => onDraftChange({ ...rule, when })}
            />
            {depthInvalid ? (
              <FieldError>{t("gate.rules.depthError")}</FieldError>
            ) : null}
          </div>
          <Separator />
          <ActionEditor
            action={rule.action}
            label={label}
            disabled={disabled}
            onChange={(action) => onDraftChange({ ...rule, action })}
          />
          {validation.issues.length > 0 ? (
            <div role="alert" className="flex min-w-0 flex-col gap-1">
              {validation.issues.map((issue) => (
                <p key={issue} className="text-sm text-destructive">
                  {issue === "regex_invalid" && validation.regexError
                    ? t(ISSUE_LABEL_KEYS[issue], validation.regexError)
                    : t(ISSUE_LABEL_KEYS[issue])}
                </p>
              ))}
            </div>
          ) : null}
        </div>
        <SheetFooter className="shrink-0">
          <Button type="button" variant="outline" onClick={onClose}>
            {t("gate.rules.cancel")}
          </Button>
          <Button
            type="button"
            disabled={disabled || validation.issues.length > 0}
            onClick={() => onSave(rule)}
          >
            {t("gate.rules.save")}
          </Button>
        </SheetFooter>
      </SheetContent>
    </Sheet>
  );
}

/** 规则列表与受控 Sheet 编辑器（AND/OR ≤2 层 + 动作六选一）。 */
export function RulesSection({
  profile,
  disabled,
  onChange,
}: RulesSectionProps) {
  const { t } = useI18n();
  const [sheet, setSheet] = useState<{
    rule: GateRuleData;
    index: number | null;
  } | null>(null);
  const rules = profile.rules;

  const openNew = () => {
    setSheet({
      rule: {
        id: nextRuleId(rules),
        enabled: true,
        description: "",
        when: emptyGroup(),
        action: actionForKind("force_disposition"),
      },
      index: null,
    });
  };

  const openEdit = (rule: GateRuleData, index: number) => {
    setSheet({ rule: structuredClone(rule), index });
  };

  const saveSheet = () => {
    if (!sheet) return;
    const next =
      sheet.index === null
        ? [...rules, sheet.rule]
        : rules.map((rule, index) =>
            index === sheet.index ? sheet.rule : rule,
          );
    onChange({ rules: next });
    setSheet(null);
  };

  const moveRule = (index: number, offset: number) => {
    const target = index + offset;
    if (target < 0 || target >= rules.length) return;
    const next = [...rules];
    const [moved] = next.splice(index, 1);
    next.splice(target, 0, moved);
    onChange({ rules: next });
  };

  return (
    <FieldSet className="rounded-lg border p-4">
      <FieldLegend>{t("gate.rules.title")}</FieldLegend>
      <FieldGroup>
        {rules.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            {t("gate.rules.empty")}
          </p>
        ) : (
          <ul className="flex min-w-0 list-none flex-col gap-1">
            {rules.map((rule, index) => (
              <li
                key={rule.id}
                className="flex min-w-0 flex-wrap items-center gap-2 rounded-md border px-2 py-1.5"
              >
                <Switch
                  checked={rule.enabled}
                  disabled={disabled}
                  onCheckedChange={(checked) =>
                    onChange({
                      rules: rules.map((entry, ruleIndex) =>
                        ruleIndex === index
                          ? { ...entry, enabled: checked }
                          : entry,
                      ),
                    })
                  }
                  aria-label={t("gate.rules.enabled")}
                />
                <span className="min-w-0 flex-1 break-all">
                  <code className="font-mono text-xs">{rule.id}</code>
                  {rule.description ? (
                    <span className="ml-2 text-sm text-muted-foreground">
                      {rule.description}
                    </span>
                  ) : null}
                </span>
                <span className="hidden text-xs text-muted-foreground sm:inline">
                  {actionSummary(t, rule)}
                </span>
                <div className="flex shrink-0 items-center gap-1">
                  <Button
                    type="button"
                    variant="outline"
                    size="icon"
                    aria-label={t("gate.rules.up")}
                    disabled={disabled || index === 0}
                    onClick={() => moveRule(index, -1)}
                  >
                    <ArrowUp aria-hidden="true" />
                  </Button>
                  <Button
                    type="button"
                    variant="outline"
                    size="icon"
                    aria-label={t("gate.rules.down")}
                    disabled={disabled || index === rules.length - 1}
                    onClick={() => moveRule(index, 1)}
                  >
                    <ArrowDown aria-hidden="true" />
                  </Button>
                  <Button
                    type="button"
                    variant="outline"
                    size="icon"
                    aria-label={t("gate.rules.edit")}
                    disabled={disabled}
                    onClick={() => openEdit(rule, index)}
                  >
                    <Pencil aria-hidden="true" />
                  </Button>
                  <Button
                    type="button"
                    variant="outline"
                    size="icon"
                    aria-label={t("gate.rules.delete")}
                    disabled={disabled}
                    onClick={() =>
                      onChange({
                        rules: rules.filter(
                          (_, ruleIndex) => ruleIndex !== index,
                        ),
                      })
                    }
                  >
                    <Trash2 aria-hidden="true" />
                  </Button>
                </div>
              </li>
            ))}
          </ul>
        )}
        <div>
          <Button
            type="button"
            variant="outline"
            disabled={disabled}
            onClick={openNew}
          >
            <Plus data-icon="inline-start" />
            {t("gate.rules.add")}
          </Button>
        </div>
      </FieldGroup>

      <RuleSheet
        open={sheet !== null}
        rule={
          sheet?.rule ?? {
            id: "",
            enabled: true,
            description: "",
            when: emptyGroup(),
            action: actionForKind("force_disposition"),
          }
        }
        existingIds={
          sheet === null
            ? []
            : rules
                .filter((_, index) => index !== sheet.index)
                .map((rule) => rule.id)
        }
        isNew={sheet?.index === null}
        disabled={disabled}
        onClose={() => setSheet(null)}
        onSave={saveSheet}
        onDraftChange={(rule) =>
          setSheet((current) => (current ? { ...current, rule } : current))
        }
      />
    </FieldSet>
  );
}
