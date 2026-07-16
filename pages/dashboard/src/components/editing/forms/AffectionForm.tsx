import * as React from "react";
import { EditFormLayout, InlineFieldError as FieldError } from "@/components/editing/EditFormLayout";
import { Field, FieldDescription, FieldGroup, FieldLabel } from "@/components/ui/field";
import { Input } from "@/components/ui/Input";
import { useI18n } from "@/hooks/useI18n";
import type { AffectionDraft, AffectionUserEntry } from "@/types";
import type { FieldErrors } from "@/types/editing";

type AffectionValue = AffectionDraft & Partial<Pick<AffectionUserEntry, "affection_level" | "level_name" | "interaction_count" | "last_interaction">>;
export interface AffectionFormProps { value: AffectionValue; onChange(value: AffectionValue): void; fieldErrors: FieldErrors; formErrors?: readonly string[]; disabled?: boolean; mode: "create" | "edit"; }

export function AffectionForm({ value, onChange, fieldErrors, formErrors = [], disabled = false, mode }: AffectionFormProps) {
  const { t } = useI18n();
  const [rangeError, setRangeError] = React.useState<string>();
  const update = <K extends keyof AffectionDraft>(field: K, next: AffectionDraft[K]) => onChange({ ...value, [field]: next });
  const identityDisabled = disabled || mode === "edit";
  const validationErrors = { ...fieldErrors, ...(rangeError ? { affection_score: rangeError } : {}) };
  return <EditFormLayout summaryLabel={t("edit.validationSummary")} fieldErrors={validationErrors} formErrors={formErrors} focusInvalid={Object.keys(validationErrors).length > 0 || formErrors.length > 0}>
    {({ getFieldError, registerField }) => {
      const userIdError = getFieldError("user_id");
      const groupIdError = getFieldError("group_id");
      const scoreError = getFieldError("affection_score");
      return <FieldGroup>
      <Field data-invalid={Boolean(userIdError)} data-disabled={identityDisabled}><FieldLabel htmlFor="affection-user-id">{t("affection.userId")}</FieldLabel><Input ref={(element) => registerField("user_id", element)} id="affection-user-id" value={value.user_id} aria-invalid={Boolean(userIdError)} aria-describedby={userIdError?.id} disabled={identityDisabled} onChange={(e) => update("user_id", e.currentTarget.value)} />{userIdError ? <FieldError id={userIdError.id}>{userIdError.message}</FieldError> : null}</Field>
      <Field data-invalid={Boolean(groupIdError)} data-disabled={identityDisabled}><FieldLabel htmlFor="affection-group-id">{t("affection.groupId")}</FieldLabel><Input ref={(element) => registerField("group_id", element)} id="affection-group-id" value={value.group_id} aria-invalid={Boolean(groupIdError)} aria-describedby={groupIdError?.id} disabled={identityDisabled} onChange={(e) => update("group_id", e.currentTarget.value)} />{groupIdError ? <FieldError id={groupIdError.id}>{groupIdError.message}</FieldError> : null}</Field>
      <Field data-invalid={Boolean(scoreError)}><FieldLabel htmlFor="affection-score">{t("affection.score")}</FieldLabel><Input ref={(element) => registerField("affection_score", element)} id="affection-score" type="number" min="-100" max="100" step="1" value={value.affection_score} aria-invalid={Boolean(scoreError)} aria-describedby={scoreError?.id} onChange={(e) => { const next = Number(e.currentTarget.value); if (Number.isInteger(next) && next >= -100 && next <= 100) { setRangeError(undefined); update("affection_score", next); } else setRangeError(Number.isInteger(next) ? t("affection.scoreRange") : t("affection.scoreInteger")); }} />{scoreError ? <FieldError id={scoreError.id}>{scoreError.message}</FieldError> : null}</Field>
      <div aria-label={t("affection.details")}><FieldLabel>{t("affection.level")}</FieldLabel><FieldDescription>{value.affection_level ?? "—"} {value.level_name ?? "—"}</FieldDescription><FieldLabel>{t("affection.interactions")}</FieldLabel><FieldDescription>{value.interaction_count ?? "—"}</FieldDescription><FieldLabel>{t("affection.lastInteraction")}</FieldLabel><FieldDescription>{value.last_interaction ?? "—"}</FieldDescription></div>
    </FieldGroup>}}
  </EditFormLayout>;
}
