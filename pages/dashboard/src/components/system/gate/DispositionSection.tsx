import { Plus, Trash2 } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/Button";
import {
  Field,
  FieldContent,
  FieldGroup,
  FieldLegend,
  FieldSet,
  FieldTitle,
} from "@/components/ui/field";
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useI18n } from "@/hooks/useI18n";
import type { GateDisposition, GateProfileData } from "@/types/config";
import {
  GATE_BUILTIN_REASON_CODES,
  GATE_OVERRIDES_MAX,
} from "./validation";

export interface DispositionSectionProps {
  profile: GateProfileData;
  disabled: boolean;
  onChange: (patch: Partial<GateProfileData>) => void;
}

const DISPOSITIONS: GateDisposition[] = ["quarantine", "discard", "mark_write"];

const DISPOSITION_LABEL_KEYS: Record<string, string> = {
  quarantine: "gate.disposition.quarantine",
  discard: "gate.disposition.discard",
  mark_write: "gate.disposition.markWrite",
};

/** 处置策略：默认处置三选一 + 原因码映射（≤20 条）。 */
export function DispositionSection({
  profile,
  disabled,
  onChange,
}: DispositionSectionProps) {
  const { t } = useI18n();
  const [draftCode, setDraftCode] = useState("");
  const [draftDisposition, setDraftDisposition] =
    useState<GateDisposition>("quarantine");
  const overrides = profile.disposition_overrides;
  const entries = Object.entries(overrides);
  const reached = entries.length >= GATE_OVERRIDES_MAX;

  const dispositionItems = DISPOSITIONS.map((value) => ({
    label: t(DISPOSITION_LABEL_KEYS[value]),
    value,
  }));

  const ruleCodes = profile.rules.map((rule) => `custom_rule_${rule.id}`);
  const builtinItems = GATE_BUILTIN_REASON_CODES.map((code) => ({
    label: code,
    value: code,
  }));

  const codeItemsFor = (extra?: string) => {
    const values = Array.from(
      new Set([...GATE_BUILTIN_REASON_CODES, ...ruleCodes, ...(extra ? [extra] : [])]),
    );
    return values.map((code) => ({ label: code, value: code }));
  };

  const addOverride = () => {
    if (!draftCode || reached || draftCode in overrides) return;
    onChange({
      disposition_overrides: { ...overrides, [draftCode]: draftDisposition },
    });
    setDraftCode("");
  };

  return (
    <FieldSet className="rounded-lg border p-4">
      <FieldLegend>{t("gate.disposition.title")}</FieldLegend>
      <FieldGroup>
        <Field>
          <FieldContent>
            <FieldTitle>{t("gate.disposition.default")}</FieldTitle>
          </FieldContent>
          <Tabs
            value={profile.disposition}
            onValueChange={(value) => {
              if (value) {
                onChange({ disposition: value as GateDisposition });
              }
            }}
          >
            <TabsList aria-label={t("gate.disposition.default")}>
              {DISPOSITIONS.map((value) => (
                <TabsTrigger key={value} value={value} disabled={disabled}>
                  {t(DISPOSITION_LABEL_KEYS[value])}
                </TabsTrigger>
              ))}
            </TabsList>
          </Tabs>
        </Field>

        <Field>
          <FieldContent>
            <FieldTitle>{t("gate.disposition.overridesTitle")}</FieldTitle>
          </FieldContent>
          <ul className="flex min-w-0 list-none flex-col gap-1">
            {entries.map(([code, disposition]) => (
              <li
                key={code}
                className="flex min-w-0 flex-wrap items-center gap-2 rounded-md border px-2 py-1"
              >
                <span className="min-w-0 flex-1 break-all font-mono text-xs">
                  {code}
                </span>
                <Select
                  items={dispositionItems}
                  value={disposition}
                  disabled={disabled}
                  onValueChange={(value) => {
                    if (value) {
                      onChange({
                        disposition_overrides: {
                          ...overrides,
                          [code]: value as GateDisposition,
                        },
                      });
                    }
                  }}
                >
                  <SelectTrigger
                    aria-label={t("gate.disposition.default")}
                    size="sm"
                    className="min-w-40"
                  >
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectGroup>
                      {dispositionItems.map((item) => (
                        <SelectItem key={item.value} value={item.value}>
                          {item.label}
                        </SelectItem>
                      ))}
                    </SelectGroup>
                  </SelectContent>
                </Select>
                <Button
                  type="button"
                  variant="outline"
                  size="icon"
                  aria-label={t("gate.disposition.overrideRemove")}
                  disabled={disabled}
                  onClick={() => {
                    const next = { ...overrides };
                    delete next[code];
                    onChange({ disposition_overrides: next });
                  }}
                >
                  <Trash2 aria-hidden="true" />
                </Button>
              </li>
            ))}
          </ul>
          <div className="flex min-w-0 flex-wrap items-center gap-2">
            <Select
              items={codeItemsFor(draftCode || undefined)}
              value={draftCode || null}
              disabled={disabled || reached}
              onValueChange={(value) => setDraftCode(value ?? "")}
            >
              <SelectTrigger
                aria-label={t("gate.disposition.reasonCode")}
                size="sm"
                className="min-w-56"
              >
                <SelectValue placeholder={t("gate.disposition.reasonCode")} />
              </SelectTrigger>
              <SelectContent>
                <SelectGroup>
                  {codeItemsFor(draftCode || undefined).map((item) => (
                    <SelectItem key={item.value} value={item.value}>
                      {item.label}
                    </SelectItem>
                  ))}
                </SelectGroup>
              </SelectContent>
            </Select>
            <Select
              items={dispositionItems}
              value={draftDisposition}
              disabled={disabled || reached}
              onValueChange={(value) => {
                if (value) setDraftDisposition(value as GateDisposition);
              }}
            >
              <SelectTrigger
                aria-label={t("gate.disposition.default")}
                size="sm"
                className="min-w-40"
              >
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectGroup>
                  {dispositionItems.map((item) => (
                    <SelectItem key={item.value} value={item.value}>
                      {item.label}
                    </SelectItem>
                  ))}
                </SelectGroup>
              </SelectContent>
            </Select>
            <Button
              type="button"
              variant="outline"
              disabled={disabled || reached || !draftCode || draftCode in overrides}
              onClick={addOverride}
            >
              <Plus data-icon="inline-start" />
              {t("gate.disposition.overrideAdd")}
            </Button>
          </div>
          {reached ? (
            <p className="text-xs text-muted-foreground">
              {t("gate.wordlists.itemLimitHint", String(GATE_OVERRIDES_MAX))}
            </p>
          ) : null}
        </Field>
      </FieldGroup>
    </FieldSet>
  );
}
