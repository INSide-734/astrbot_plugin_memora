import { Button } from "@/components/ui/Button";
import { TagEditor } from "@/components/editing/TagEditor";
import { EditFormLayout, InlineFieldError as FieldError } from "@/components/editing/EditFormLayout";
import { Field, FieldLabel } from "@/components/ui/field";
import { Input } from "@/components/ui/Input";
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger } from "@/components/ui/Select";
import { useI18n } from "@/hooks/useI18n";
import type { FieldErrors } from "@/types/editing";
import type { ProfileDraft } from "@/types";

export interface ProfileFormProps {
  value: ProfileDraft;
  onChange(value: ProfileDraft): void;
  fieldErrors: FieldErrors;
  disabled?: boolean;
  mode: "create" | "edit";
}

export function ProfileForm({ value, onChange, fieldErrors, disabled = false, mode }: ProfileFormProps) {
  const { t } = useI18n();
  const [rangeErrors, setRangeErrors] = useState<Record<string, string>>({});
  const label = (key: string, fallback: string, ...args: string[]) => {
    const translated = t(key, ...args);
    return translated === key ? fallback : translated;
  };
  const update = <K extends keyof ProfileDraft>(field: K, next: ProfileDraft[K]) => onChange({ ...value, [field]: next });
  const preferences = value.preferences;
  const updatePreferences = <K extends keyof ProfileDraft["preferences"]>(field: K, next: ProfileDraft["preferences"][K]) => update("preferences", { ...preferences, [field]: next });
  const updateTag = (index: number, field: "category" | "value" | "confidence", next: string | number) => {
    const tags = value.tags.map((tag, tagIndex) => tagIndex === index ? { ...tag, [field]: next } : tag);
    update("tags", tags);
  };
  const updateBoundedNumber = (errorKey: string, updateValue: (value: number) => void, raw: string, min: number, max: number, message: string) => {
    const next = Number(raw);
    if (Number.isFinite(next) && next >= min && next <= max) {
      setRangeErrors((current) => {
        if (!(errorKey in current)) return current;
        const { [errorKey]: _removed, ...rest } = current;
        return rest;
      });
      updateValue(next);
    } else {
      setRangeErrors((current) => ({ ...current, [errorKey]: message }));
    }
  };

  const validationErrors = { ...fieldErrors, ...rangeErrors };
  return <EditFormLayout summaryLabel={t("edit.validationSummary")} fieldErrors={validationErrors} focusInvalid={Object.keys(validationErrors).length > 0}>
    {({ getFieldError }) => {
      const userIdError = getFieldError("user_id");
      const nameError = getFieldError("display_name");
      const activeStartError = getFieldError("preferences.active_hours.0");
      const activeEndError = getFieldError("preferences.active_hours.1");
      const tagConfidenceErrors = value.tags.map((_, index) => getFieldError(`tags.${index}.confidence`));
      const fallbackTagError = getFieldError("tags");
      const hasTagConfidenceError = Boolean(fallbackTagError) || tagConfidenceErrors.some(Boolean);
      return <>
        <Field data-invalid={Boolean(userIdError)} data-disabled={disabled || mode === "edit"}><FieldLabel htmlFor="profile-user-id">{t("table.userId")}</FieldLabel><Input id="profile-user-id" aria-invalid={Boolean(userIdError)} aria-describedby={userIdError?.id} disabled={disabled || mode === "edit"} value={value.user_id} onChange={(event) => update("user_id", event.currentTarget.value)} />{userIdError ? <FieldError id={userIdError.id}>{userIdError.message}</FieldError> : null}</Field>
        <Field data-invalid={Boolean(nameError)} data-disabled={disabled}><FieldLabel htmlFor="profile-display-name">{t("table.name")}</FieldLabel><Input id="profile-display-name" aria-invalid={Boolean(nameError)} aria-describedby={nameError?.id} disabled={disabled} value={value.display_name} onChange={(event) => update("display_name", event.currentTarget.value)} />{nameError ? <FieldError id={nameError.id}>{nameError.message}</FieldError> : null}</Field>
        <Field data-disabled={disabled}><FieldLabel>{label("profile.replyStyle", "Reply style")}</FieldLabel><Input className="sr-only" aria-label={label("profile.replyStyle", "Reply style")} disabled={disabled} value={preferences.reply_style} onChange={(event) => updatePreferences("reply_style", event.currentTarget.value)} /><Select value={preferences.reply_style ? `profile-${preferences.reply_style}` : ""} onValueChange={(next) => next && updatePreferences("reply_style", next.replace(/^profile-/, ""))} disabled={disabled}><SelectTrigger aria-label={`${label("profile.replyStyle", "Reply style")} selector`}><span>{preferences.reply_style || label("profile.replyStyle", "Reply style")}</span></SelectTrigger><SelectContent><SelectGroup>{["concise", "casual", "detailed"].map((style) => <SelectItem key={style} value={`profile-${style}`}>{label(`profile.replyStyle.${style}`, style)}</SelectItem>)}</SelectGroup></SelectContent></Select></Field>
        <Field data-disabled={disabled}><FieldLabel>{label("profile.preferredTopics", "Preferred topics")}</FieldLabel><TagEditor label={label("profile.preferredTopics", "Preferred topics")} getRemoveLabel={(tag) => label("tags.remove", `Remove ${tag}`, tag)} values={preferences.preferred_topics} onChange={(next) => updatePreferences("preferred_topics", next)} disabled={disabled} /></Field>
        <Field data-disabled={disabled}><FieldLabel>{label("profile.avoidedTopics", "Avoided topics")}</FieldLabel><TagEditor label={label("profile.avoidedTopics", "Avoided topics")} getRemoveLabel={(tag) => label("tags.remove", `Remove ${tag}`, tag)} values={preferences.avoided_topics} onChange={(next) => updatePreferences("avoided_topics", next)} disabled={disabled} /></Field>
        <Field data-invalid={Boolean(activeStartError || activeEndError)} data-disabled={disabled}><FieldLabel htmlFor="profile-active-start">{label("profile.activeHours", "Active hours")}</FieldLabel><div className="flex gap-2"><Input id="profile-active-start" aria-label={label("profile.activeHoursStart", "Active hours start")} type="number" min="0" max="23" aria-invalid={Boolean(activeStartError)} aria-describedby={activeStartError?.id} disabled={disabled} value={preferences.active_hours[0] ?? ""} onChange={(event) => updateBoundedNumber("preferences.active_hours.0", (next) => updatePreferences("active_hours", [next, preferences.active_hours[1] ?? next]), event.currentTarget.value, 0, 23, label("profile.activeHoursRange", "Must be between 0 and 23"))} /><Input aria-label={label("profile.activeHoursEnd", "Active hours end")} type="number" min="0" max="23" aria-invalid={Boolean(activeEndError)} aria-describedby={activeEndError?.id} disabled={disabled} value={preferences.active_hours[1] ?? ""} onChange={(event) => updateBoundedNumber("preferences.active_hours.1", (next) => updatePreferences("active_hours", [preferences.active_hours[0] ?? next, next]), event.currentTarget.value, 0, 23, label("profile.activeHoursRange", "Must be between 0 and 23"))} /></div>{activeStartError ? <FieldError id={activeStartError.id}>{activeStartError.message}</FieldError> : null}{activeEndError ? <FieldError id={activeEndError.id}>{activeEndError.message}</FieldError> : null}</Field>
        <Field data-invalid={hasTagConfidenceError} data-disabled={disabled}><FieldLabel>{t("field.tags")}</FieldLabel><div className="flex flex-col gap-3">{value.tags.map((tag, index) => { const error = tagConfidenceErrors[index] ?? (index === 0 ? fallbackTagError : undefined); return <div key={`${tag.category}-${tag.value}-${index}`} className="flex flex-wrap gap-2"><Input aria-label={index === 0 ? label("profile.tagCategory", "Tag category") : `${label("profile.tagCategory", "Tag category")} ${index + 1}`} disabled={disabled} value={tag.category} onChange={(event) => updateTag(index, "category", event.currentTarget.value)} /><Input aria-label={index === 0 ? label("profile.tagValue", "Tag value") : `${label("profile.tagValue", "Tag value")} ${index + 1}`} disabled={disabled} value={tag.value} onChange={(event) => updateTag(index, "value", event.currentTarget.value)} /><Input aria-label={index === 0 ? label("profile.tagConfidence", "Tag confidence") : `${label("profile.tagConfidence", "Tag confidence")} ${index + 1}`} type="number" min="0" max="1" step="0.01" aria-invalid={Boolean(error)} aria-describedby={error?.id} disabled={disabled} value={tag.confidence} onChange={(event) => updateBoundedNumber(`tags.${index}.confidence`, (next) => updateTag(index, "confidence", next), event.currentTarget.value, 0, 1, label("profile.tagConfidenceRange", "Must be between 0 and 1"))} /></div>; })}</div><Button type="button" variant="outline" size="sm" disabled={disabled} onClick={() => update("tags", [...value.tags, { category: "interest", value: "", confidence: 0.5 }])}>{label("profile.addTag", "Add tag")}</Button>{fallbackTagError ? <FieldError id={fallbackTagError.id}>{fallbackTagError.message}</FieldError> : null}{tagConfidenceErrors.map((error, index) => error ? <FieldError key={index} id={error.id}>{error.message}</FieldError> : null)}</Field>
      </>;
    }}
  </EditFormLayout>;
}
import { useState } from "react";
