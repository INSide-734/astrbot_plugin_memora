import { Button } from "@/components/ui/Button";
import { TagEditor } from "@/components/editing/TagEditor";
import { EditFormLayout } from "@/components/editing/EditFormLayout";
import { Field, FieldError, FieldLabel } from "@/components/ui/field";
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

  return <EditFormLayout summaryLabel={t("edit.validationSummary")} fieldErrors={fieldErrors} focusInvalid={Object.keys(fieldErrors).length > 0}>
    {({ getFieldError }) => {
      const userIdError = getFieldError("user_id");
      const nameError = getFieldError("display_name");
      const serverConfidenceError = getFieldError("tags.0.confidence") ?? getFieldError("tags");
      const activeStartError = rangeErrors.active_start;
      const activeEndError = rangeErrors.active_end;
      const tagConfidenceErrors = value.tags.map((_, index) => rangeErrors[`tag_confidence_${index}`]);
      const hasTagConfidenceError = Boolean(serverConfidenceError) || tagConfidenceErrors.some(Boolean);
      return <>
        <Field data-invalid={Boolean(userIdError)} data-disabled={disabled || mode === "edit"}><FieldLabel htmlFor="profile-user-id">{t("table.userId")}</FieldLabel><Input id="profile-user-id" aria-invalid={Boolean(userIdError)} aria-describedby={userIdError?.id} disabled={disabled || mode === "edit"} value={value.user_id} onChange={(event) => update("user_id", event.currentTarget.value)} />{userIdError ? <FieldError id={userIdError.id}>{userIdError.message}</FieldError> : null}</Field>
        <Field data-invalid={Boolean(nameError)} data-disabled={disabled}><FieldLabel htmlFor="profile-display-name">{t("table.name")}</FieldLabel><Input id="profile-display-name" aria-invalid={Boolean(nameError)} aria-describedby={nameError?.id} disabled={disabled} value={value.display_name} onChange={(event) => update("display_name", event.currentTarget.value)} />{nameError ? <FieldError id={nameError.id}>{nameError.message}</FieldError> : null}</Field>
        <Field data-disabled={disabled}><FieldLabel>{label("profile.replyStyle", "Reply style")}</FieldLabel><Input className="sr-only" aria-label={label("profile.replyStyle", "Reply style")} disabled={disabled} value={preferences.reply_style} onChange={(event) => updatePreferences("reply_style", event.currentTarget.value)} /><Select value={preferences.reply_style ? `profile-${preferences.reply_style}` : ""} onValueChange={(next) => next && updatePreferences("reply_style", next.replace(/^profile-/, ""))} disabled={disabled}><SelectTrigger aria-label={`${label("profile.replyStyle", "Reply style")} selector`}><span>{preferences.reply_style || label("profile.replyStyle", "Reply style")}</span></SelectTrigger><SelectContent><SelectGroup>{["concise", "casual", "detailed"].map((style) => <SelectItem key={style} value={`profile-${style}`}>{label(`profile.replyStyle.${style}`, style)}</SelectItem>)}</SelectGroup></SelectContent></Select></Field>
        <Field data-disabled={disabled}><FieldLabel>{label("profile.preferredTopics", "Preferred topics")}</FieldLabel><TagEditor label={label("profile.preferredTopics", "Preferred topics")} getRemoveLabel={(tag) => label("tags.remove", `Remove ${tag}`, tag)} values={preferences.preferred_topics} onChange={(next) => updatePreferences("preferred_topics", next)} disabled={disabled} /></Field>
        <Field data-disabled={disabled}><FieldLabel>{label("profile.avoidedTopics", "Avoided topics")}</FieldLabel><TagEditor label={label("profile.avoidedTopics", "Avoided topics")} getRemoveLabel={(tag) => label("tags.remove", `Remove ${tag}`, tag)} values={preferences.avoided_topics} onChange={(next) => updatePreferences("avoided_topics", next)} disabled={disabled} /></Field>
        <Field data-invalid={Boolean(activeStartError || activeEndError)} data-disabled={disabled}><FieldLabel htmlFor="profile-active-start">{label("profile.activeHours", "Active hours")}</FieldLabel><div className="flex gap-2"><Input id="profile-active-start" aria-label={label("profile.activeHoursStart", "Active hours start")} type="number" min="0" max="23" aria-invalid={Boolean(activeStartError)} aria-describedby={activeStartError ? "profile-active-start-range-error" : undefined} disabled={disabled} value={preferences.active_hours[0] ?? ""} onChange={(event) => updateBoundedNumber("active_start", (next) => updatePreferences("active_hours", [next, preferences.active_hours[1] ?? next]), event.currentTarget.value, 0, 23, label("profile.activeHoursRange", "Must be between 0 and 23"))} /><Input aria-label={label("profile.activeHoursEnd", "Active hours end")} type="number" min="0" max="23" aria-invalid={Boolean(activeEndError)} aria-describedby={activeEndError ? "profile-active-end-range-error" : undefined} disabled={disabled} value={preferences.active_hours[1] ?? ""} onChange={(event) => updateBoundedNumber("active_end", (next) => updatePreferences("active_hours", [preferences.active_hours[0] ?? next, next]), event.currentTarget.value, 0, 23, label("profile.activeHoursRange", "Must be between 0 and 23"))} /></div>{activeStartError ? <FieldError id="profile-active-start-range-error">{activeStartError}</FieldError> : null}{activeEndError ? <FieldError id="profile-active-end-range-error">{activeEndError}</FieldError> : null}</Field>
        <Field data-invalid={hasTagConfidenceError} data-disabled={disabled}><FieldLabel>{t("field.tags")}</FieldLabel><div className="flex flex-col gap-3">{value.tags.map((tag, index) => { const rangeError = tagConfidenceErrors[index]; const error = index === 0 && serverConfidenceError ? serverConfidenceError : rangeError ? { id: `profile-tag-confidence-${index}-range-error`, message: rangeError } : undefined; return <div key={`${tag.category}-${tag.value}-${index}`} className="flex flex-wrap gap-2"><Input aria-label={index === 0 ? label("profile.tagCategory", "Tag category") : `${label("profile.tagCategory", "Tag category")} ${index + 1}`} disabled={disabled} value={tag.category} onChange={(event) => updateTag(index, "category", event.currentTarget.value)} /><Input aria-label={index === 0 ? label("profile.tagValue", "Tag value") : `${label("profile.tagValue", "Tag value")} ${index + 1}`} disabled={disabled} value={tag.value} onChange={(event) => updateTag(index, "value", event.currentTarget.value)} /><Input aria-label={index === 0 ? label("profile.tagConfidence", "Tag confidence") : `${label("profile.tagConfidence", "Tag confidence")} ${index + 1}`} type="number" min="0" max="1" step="0.01" aria-invalid={Boolean(error)} aria-describedby={error?.id} disabled={disabled} value={tag.confidence} onChange={(event) => updateBoundedNumber(`tag_confidence_${index}`, (next) => updateTag(index, "confidence", next), event.currentTarget.value, 0, 1, label("profile.tagConfidenceRange", "Must be between 0 and 1"))} /></div>; })}</div><Button type="button" variant="outline" size="sm" disabled={disabled} onClick={() => update("tags", [...value.tags, { category: "interest", value: "", confidence: 0.5 }])}>{label("profile.addTag", "Add tag")}</Button>{serverConfidenceError ? <FieldError id={serverConfidenceError.id}>{serverConfidenceError.message}</FieldError> : null}{tagConfidenceErrors.map((error, index) => error ? <FieldError key={index} id={`profile-tag-confidence-${index}-range-error`}>{error}</FieldError> : null)}</Field>
      </>;
    }}
  </EditFormLayout>;
}
import { useState } from "react";
