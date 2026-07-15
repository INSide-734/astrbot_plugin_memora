import * as React from "react";
import { EditFormLayout } from "@/components/editing/EditFormLayout";
import { Field, FieldDescription, FieldError, FieldGroup, FieldLabel } from "@/components/ui/field";
import { Input } from "@/components/ui/Input";
import { useI18n } from "@/hooks/useI18n";
import type { AffectionDraft, AffectionUserEntry } from "@/types";
import type { FieldErrors } from "@/types/editing";

type AffectionValue = AffectionDraft & Partial<Pick<AffectionUserEntry, "affection_level" | "level_name" | "interaction_count" | "last_interaction">>;
export interface AffectionFormProps { value: AffectionValue; onChange(value: AffectionValue): void; fieldErrors: FieldErrors; disabled?: boolean; mode: "create" | "edit"; }

export function AffectionForm({ value, onChange, fieldErrors, disabled = false, mode }: AffectionFormProps) {
  const { t } = useI18n();
  const [rangeError, setRangeError] = React.useState<string>();
  const update = <K extends keyof AffectionDraft>(field: K, next: AffectionDraft[K]) => onChange({ ...value, [field]: next });
  const identityDisabled = disabled || mode === "edit";
  const scoreError = fieldErrors.affection_score ?? rangeError;
  return <EditFormLayout summaryLabel={t("edit.validationSummary")} fieldErrors={{ ...fieldErrors, ...(rangeError ? { affection_score: rangeError } : {}) }} focusInvalid={Boolean(scoreError)}>
    {({ getFieldError }) => <FieldGroup>
      <Field data-disabled={identityDisabled}><FieldLabel htmlFor="affection-user-id">{t("affection.userId")}</FieldLabel><Input id="affection-user-id" value={value.user_id} disabled={identityDisabled} onChange={(e) => update("user_id", e.currentTarget.value)} /></Field>
      <Field data-disabled={identityDisabled}><FieldLabel htmlFor="affection-group-id">{t("affection.groupId")}</FieldLabel><Input id="affection-group-id" value={value.group_id} disabled={identityDisabled} onChange={(e) => update("group_id", e.currentTarget.value)} /></Field>
      <Field data-invalid={Boolean(scoreError)}><FieldLabel htmlFor="affection-score">{t("affection.score")}</FieldLabel><Input id="affection-score" type="number" min="-100" max="100" step="1" value={value.affection_score} aria-invalid={Boolean(scoreError)} aria-describedby={scoreError ? "affection-score-error" : undefined} onChange={(e) => { const next = Number(e.currentTarget.value); if (Number.isInteger(next) && next >= -100 && next <= 100) { setRangeError(undefined); update("affection_score", next); } else setRangeError(Number.isInteger(next) ? t("affection.scoreRange") : t("affection.scoreInteger")); }} />{scoreError ? <FieldError id="affection-score-error">{scoreError}</FieldError> : null}</Field>
      <div aria-label={t("affection.details")}><FieldLabel>{t("affection.level")}</FieldLabel><FieldDescription>{value.affection_level ?? "—"} {value.level_name ?? "—"}</FieldDescription><FieldLabel>{t("affection.interactions")}</FieldLabel><FieldDescription>{value.interaction_count ?? "—"}</FieldDescription><FieldLabel>{t("affection.lastInteraction")}</FieldLabel><FieldDescription>{value.last_interaction ?? "—"}</FieldDescription></div>
    </FieldGroup>}
  </EditFormLayout>;
}
