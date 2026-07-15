import * as React from "react";
import { EditFormLayout } from "@/components/editing/EditFormLayout";
import { Field, FieldDescription, FieldError, FieldGroup, FieldLabel } from "@/components/ui/field";
import { Input } from "@/components/ui/Input";
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/Select";
import { Textarea } from "@/components/ui/textarea";
import { useI18n } from "@/hooks/useI18n";
import { MOOD_TYPES } from "@/lib/constants";
import type { BotMoodStatus, MoodDraft } from "@/types";
import type { FieldErrors } from "@/types/editing";

type MoodValue = MoodDraft & Partial<Pick<BotMoodStatus, "is_active">> & { start_time?: number };
export interface MoodFormProps { value: MoodValue; onChange(value: MoodValue): void; fieldErrors: FieldErrors; disabled?: boolean; mode: "create" | "edit"; }

export function MoodForm({ value, onChange, fieldErrors, disabled = false }: MoodFormProps) {
  const { t } = useI18n();
  const [rangeErrors, setRangeErrors] = React.useState<Record<string, string>>({});
  const update = <K extends keyof MoodDraft>(field: K, next: MoodDraft[K]) => onChange({ ...value, [field]: next });
  const error = (field: keyof MoodDraft) => fieldErrors[field] ?? rangeErrors[field];
  const bounded = (field: "intensity" | "duration_hours", raw: string, min: number, max: number) => { const next = Number(raw); if (Number.isFinite(next) && next >= min && next <= max) { setRangeErrors((current) => { const rest = { ...current }; delete rest[field]; return rest; }); update(field, next); } else setRangeErrors((current) => ({ ...current, [field]: t(field === "intensity" ? "affection.intensityRange" : "affection.durationRange") })); };
  return <EditFormLayout summaryLabel={t("edit.validationSummary")} fieldErrors={{ ...fieldErrors, ...rangeErrors }} focusInvalid={Object.keys(fieldErrors).length > 0 || Object.keys(rangeErrors).length > 0}>
    {({ getFieldError }) => <FieldGroup>
      <Field data-disabled={disabled}><FieldLabel htmlFor="mood-group-id">{t("affection.groupId")}</FieldLabel><Input id="mood-group-id" value={value.group_id} disabled={disabled} onChange={(e) => update("group_id", e.currentTarget.value)} /></Field>
      <Field data-invalid={Boolean(error("mood_type"))}><FieldLabel htmlFor="mood-type">{t("affection.moodType")}</FieldLabel><Select value={value.mood_type} onValueChange={(next) => next && update("mood_type", next)} disabled={disabled}><SelectTrigger id="mood-type" aria-label={t("affection.moodType")} aria-invalid={Boolean(error("mood_type"))}><SelectValue /></SelectTrigger><SelectContent><SelectGroup>{MOOD_TYPES.map((item) => <SelectItem key={item.type} value={item.type.toLowerCase()}>{t(`mood.${item.type}`)}</SelectItem>)}</SelectGroup></SelectContent></Select></Field>
      <Field data-invalid={Boolean(error("intensity"))}><FieldLabel htmlFor="mood-intensity">{t("affection.moodIntensity")}</FieldLabel><Input id="mood-intensity" type="number" min="0.1" max="1" step="0.1" value={value.intensity} aria-invalid={Boolean(error("intensity"))} onChange={(e) => bounded("intensity", e.currentTarget.value, 0.1, 1)} />{error("intensity") ? <FieldError id="mood-intensity-error">{error("intensity")}</FieldError> : null}</Field>
      <Field data-invalid={Boolean(error("duration_hours"))}><FieldLabel htmlFor="mood-duration">{t("affection.moodDuration")}</FieldLabel><Input id="mood-duration" type="number" min="0.25" max="168" step="0.25" value={value.duration_hours} aria-invalid={Boolean(error("duration_hours"))} onChange={(e) => bounded("duration_hours", e.currentTarget.value, 0.25, 168)} />{error("duration_hours") ? <FieldError id="mood-duration-error">{error("duration_hours")}</FieldError> : null}</Field>
      <Field><FieldLabel htmlFor="mood-description">{t("affection.moodDescription")}</FieldLabel><Textarea id="mood-description" value={value.description} disabled={disabled} onChange={(e) => update("description", e.currentTarget.value)} /></Field>
      <div aria-label={t("affection.moodHistory")}><FieldLabel>{t("affection.history")}</FieldLabel><FieldDescription>{value.start_time ?? "—"} {value.is_active === undefined ? "" : String(value.is_active)}</FieldDescription></div>
    </FieldGroup>}
  </EditFormLayout>;
}
