import { Checkbox } from "@/components/ui/checkbox";
import {
  Field,
  FieldContent,
  FieldDescription,
  FieldGroup,
  FieldLegend,
  FieldSet,
  FieldTitle,
} from "@/components/ui/field";
import { useI18n } from "@/hooks/useI18n";
import type { GateChecks, GateProfileData } from "@/types/config";

export interface ChecksSectionProps {
  profile: GateProfileData;
  disabled: boolean;
  onChange: (patch: Partial<GateProfileData>) => void;
}

/** 四项门禁检查开关；group_subject_check 恒定展示并注明仅群聊生效。 */
export function ChecksSection({
  profile,
  disabled,
  onChange,
}: ChecksSectionProps) {
  const { t } = useI18n();

  const rows: Array<{
    key: keyof GateChecks;
    label: string;
    hint?: string;
  }> = [
    { key: "numeric_check", label: t("gate.checks.numeric") },
    { key: "negation_check", label: t("gate.checks.negation") },
    {
      key: "group_subject_check",
      label: t("gate.checks.groupSubject"),
      hint: t("gate.checks.groupSubjectHint"),
    },
    { key: "quality_low_check", label: t("gate.checks.qualityLow") },
  ];

  return (
    <FieldSet className="rounded-lg border p-4">
      <FieldLegend>{t("gate.checks.title")}</FieldLegend>
      <p className="text-sm text-muted-foreground">{t("gate.help.checks")}</p>
      <FieldGroup>
        {rows.map((row) => (
          <Field key={row.key} orientation="horizontal">
            <FieldContent>
              <FieldTitle>{row.label}</FieldTitle>
              {row.hint ? (
                <FieldDescription>{row.hint}</FieldDescription>
              ) : null}
            </FieldContent>
            <Checkbox
              checked={profile.checks[row.key]}
              disabled={disabled}
              onCheckedChange={(checked) =>
                onChange({ checks: { ...profile.checks, [row.key]: checked } })
              }
              aria-label={row.label}
            />
          </Field>
        ))}
      </FieldGroup>
    </FieldSet>
  );
}
