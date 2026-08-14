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
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import { useI18n } from "@/hooks/useI18n";
import type { GateProfileData } from "@/types/config";
import { validateJudgeTemplate } from "./validation";

export interface JudgeSectionProps {
  profile: GateProfileData;
  disabled: boolean;
  onChange: (patch: Partial<GateProfileData>) => void;
}

/** Judge 开关与 prompt 模板；模板非空时校验双占位符与未知占位符。 */
export function JudgeSection({
  profile,
  disabled,
  onChange,
}: JudgeSectionProps) {
  const { t } = useI18n();
  const judge = profile.judge;
  const issue = validateJudgeTemplate(judge.prompt_template);

  return (
    <FieldSet className="rounded-lg border p-4">
      <FieldLegend>{t("gate.judge.title")}</FieldLegend>
      <FieldGroup>
        <Field orientation="horizontal">
          <FieldContent>
            <FieldTitle>{t("gate.judge.enabled")}</FieldTitle>
            <FieldDescription>{t("gate.judge.costHint")}</FieldDescription>
          </FieldContent>
          <Switch
            checked={judge.enabled}
            disabled={disabled}
            onCheckedChange={(checked) =>
              onChange({ judge: { ...judge, enabled: checked } })
            }
            aria-label={t("gate.judge.enabled")}
          />
        </Field>
        <Field data-invalid={issue !== null}>
          <FieldTitle className="text-sm font-medium">
            {t("gate.judge.template")}
          </FieldTitle>
          <Textarea
            id="gate-judge-template"
            aria-label={t("gate.judge.template")}
            value={judge.prompt_template}
            disabled={disabled}
            rows={6}
            onChange={(event) =>
              onChange({
                judge: { ...judge, prompt_template: event.currentTarget.value },
              })
            }
          />
          <FieldDescription>{t("gate.judge.templateHint")}</FieldDescription>
          {issue?.code === "missing_placeholders" ? (
            <FieldError>{t("gate.judge.placeholderError")}</FieldError>
          ) : null}
          {issue?.code === "unknown_placeholders" ? (
            <FieldError>
              {t("gate.judge.unknownPlaceholderError", issue.placeholders.join(", "))}
            </FieldError>
          ) : null}
          {issue?.code === "too_long" ? (
            <FieldError>{t("gate.judge.templateHint")}</FieldError>
          ) : null}
        </Field>
      </FieldGroup>
    </FieldSet>
  );
}
