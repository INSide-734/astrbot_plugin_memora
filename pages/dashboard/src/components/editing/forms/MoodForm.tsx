import * as React from "react";
import { EditFormLayout, InlineFieldError as FieldError } from "@/components/editing/EditFormLayout";
import { Field, FieldDescription, FieldGroup, FieldLabel } from "@/components/ui/field";
import { Input } from "@/components/ui/Input";
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/Select";
import { Textarea } from "@/components/ui/textarea";
import { useI18n } from "@/hooks/useI18n";
import { MOOD_TYPES } from "@/lib/constants";
import type { BotMoodStatus, MoodDraft } from "@/types";
import type { FieldErrors } from "@/types/editing";

type MoodValue = MoodDraft & Partial<Pick<BotMoodStatus, "is_active">> & { start_time?: number };
export interface MoodFormProps { value: MoodValue; onChange(value: MoodValue): void; fieldErrors: FieldErrors; formErrors?: readonly string[]; disabled?: boolean; mode: "create" | "edit"; }

export function MoodForm({ value, onChange, fieldErrors, formErrors = [], disabled = false }: MoodFormProps) {
  const { t } = useI18n();
  const [rangeErrors, setRangeErrors] = React.useState<Record<string, string>>({});
  const update = <K extends keyof MoodDraft>(field: K, next: MoodDraft[K]) => onChange({ ...value, [field]: next });
  const bounded = (field: "intensity" | "duration_hours", raw: string, min: number, max: number) => { const next = Number(raw); if (Number.isFinite(next) && next >= min && next <= max) { setRangeErrors((current) => { const rest = { ...current }; delete rest[field]; return rest; }); update(field, next); } else setRangeErrors((current) => ({ ...current, [field]: t(field === "intensity" ? "affection.intensityRange" : "affection.durationRange") })); };
  const validationErrors = { ...fieldErrors, ...rangeErrors };
  const moodItems = MOOD_TYPES.map((item) => ({ value: item.type.toLowerCase(), label: t(`mood.${item.type}`) }));
  return <EditFormLayout summaryLabel={t("edit.validationSummary")} fieldErrors={validationErrors} formErrors={formErrors} focusInvalid={Object.keys(validationErrors).length > 0 || formErrors.length > 0}>
    {({ getFieldError, registerField }) => {
      const groupIdError = getFieldError("group_id");
      const moodTypeError = getFieldError("mood_type");
      const intensityError = getFieldError("intensity");
      const durationError = getFieldError("duration_hours");
      const descriptionError = getFieldError("description");
      return <FieldGroup>
      <Field data-invalid={Boolean(groupIdError)} data-disabled={disabled}><FieldLabel htmlFor="mood-group-id">{t("affection.groupId")}</FieldLabel><Input ref={(element) => registerField("group_id", element)} id="mood-group-id" value={value.group_id} aria-invalid={Boolean(groupIdError)} aria-describedby={groupIdError?.id} disabled={disabled} onChange={(e) => update("group_id", e.currentTarget.value)} />{groupIdError ? <FieldError id={groupIdError.id}>{groupIdError.message}</FieldError> : null}</Field>
      <Field data-invalid={Boolean(moodTypeError)}><FieldLabel htmlFor="mood-type">{t("affection.moodType")}</FieldLabel><Select items={moodItems} value={value.mood_type} onValueChange={(next) => next && update("mood_type", next)} disabled={disabled}><SelectTrigger ref={(element) => registerField("mood_type", element)} id="mood-type" aria-label={t("affection.moodType")} aria-invalid={Boolean(moodTypeError)} aria-describedby={moodTypeError?.id}><SelectValue /></SelectTrigger><SelectContent><SelectGroup>{moodItems.map((item) => <SelectItem key={item.value} value={item.value}>{item.label}</SelectItem>)}</SelectGroup></SelectContent></Select>{moodTypeError ? <FieldError id={moodTypeError.id}>{moodTypeError.message}</FieldError> : null}</Field>
      <Field data-invalid={Boolean(intensityError)}><FieldLabel htmlFor="mood-intensity">{t("affection.moodIntensity")}</FieldLabel><Input ref={(element) => registerField("intensity", element)} id="mood-intensity" type="number" min="0.1" max="1" step="0.1" value={value.intensity} aria-invalid={Boolean(intensityError)} aria-describedby={intensityError?.id} onChange={(e) => bounded("intensity", e.currentTarget.value, 0.1, 1)} />{intensityError ? <FieldError id={intensityError.id}>{intensityError.message}</FieldError> : null}</Field>
      <Field data-invalid={Boolean(durationError)}><FieldLabel htmlFor="mood-duration">{t("affection.moodDuration")}</FieldLabel><Input ref={(element) => registerField("duration_hours", element)} id="mood-duration" type="number" min="0.25" max="168" step="0.25" value={value.duration_hours} aria-invalid={Boolean(durationError)} aria-describedby={durationError?.id} onChange={(e) => bounded("duration_hours", e.currentTarget.value, 0.25, 168)} />{durationError ? <FieldError id={durationError.id}>{durationError.message}</FieldError> : null}</Field>
      <Field data-invalid={Boolean(descriptionError)}><FieldLabel htmlFor="mood-description">{t("affection.moodDescription")}</FieldLabel><Textarea ref={(element) => registerField("description", element)} id="mood-description" value={value.description} aria-invalid={Boolean(descriptionError)} aria-describedby={descriptionError?.id} disabled={disabled} onChange={(e) => update("description", e.currentTarget.value)} />{descriptionError ? <FieldError id={descriptionError.id}>{descriptionError.message}</FieldError> : null}</Field>
      <div aria-label={t("affection.moodHistory")}><FieldLabel>{t("affection.history")}</FieldLabel><FieldDescription>{value.start_time ?? "—"} {value.is_active === undefined ? "" : String(value.is_active)}</FieldDescription></div>
    </FieldGroup>}}
  </EditFormLayout>;
}
