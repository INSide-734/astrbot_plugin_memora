import { useState } from "react";

import { TagEditor } from "@/components/editing/TagEditor";
import { EditFormLayout, InlineFieldError as FieldError } from "@/components/editing/EditFormLayout";
import { Field, FieldLabel } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { useI18n } from "@/hooks/useI18n";
import type { FieldErrors } from "@/types/editing";
import type { SocialRelationDraft } from "@/types";

export interface SocialRelationFormProps {
  value: SocialRelationDraft;
  onChange(value: SocialRelationDraft): void;
  fieldErrors: FieldErrors;
  formErrors?: readonly string[];
  disabled?: boolean;
  mode: "create" | "edit";
}

const RELATION_TYPES = [
  "parent_child", "siblings", "relatives",
  "neighbor", "fellow_town", "fellow_passenger",
  "colleague", "mentor_mentee", "classmate",
  "lover", "best_friend", "ambiguous", "rival",
  "board_game_friend", "gaming_teammate",
  "core_intimate", "daily_normal", "stranger",
];

export function SocialRelationForm({ value, onChange, fieldErrors, formErrors = [], disabled = false, mode }: SocialRelationFormProps) {
  const { t } = useI18n();
  const [rangeErrors, setRangeErrors] = useState<Record<string, string>>({});
  const label = (key: string, fallback: string) => {
    const translated = t(key);
    return translated === key ? fallback : translated;
  };
  const update = <K extends keyof SocialRelationDraft>(field: K, next: SocialRelationDraft[K]) => onChange({ ...value, [field]: next });
  const updateBoundedNumber = (raw: string) => {
    const next = Number(raw);
    if (Number.isFinite(next) && next >= 0 && next <= 1) {
      setRangeErrors((current) => {
        if (!("strength" in current)) return current;
        const { strength: _removed, ...rest } = current;
        return rest;
      });
      update("strength", next);
    } else {
      setRangeErrors((current) => ({ ...current, strength: label("social.strengthRange", "Must be between 0 and 1") }));
    }
  };
  const identityDisabled = disabled || mode === "edit";
  const validationErrors = { ...fieldErrors, ...rangeErrors };
  return <EditFormLayout summaryLabel={t("edit.validationSummary")} fieldErrors={validationErrors} formErrors={formErrors} focusInvalid={Object.keys(validationErrors).length > 0 || formErrors.length > 0}>
    {({ getFieldError, registerField }) => {
      const fromUserError = getFieldError("from_user");
      const toUserError = getFieldError("to_user");
      const groupIdError = getFieldError("group_id");
      const relationTypeError = getFieldError("relation_type");
      const strengthError = getFieldError("strength");
      const tagsError = getFieldError("tags");
      const relationTypes = RELATION_TYPES.includes(value.relation_type) ? RELATION_TYPES : [...RELATION_TYPES, value.relation_type];
      const relationLabel = (type: string) => label(`relation.${type}`, type);
      const relationItems = relationTypes.map((type) => ({ value: type, label: relationLabel(type) }));
      return <>
      <Field data-invalid={Boolean(fromUserError)} data-disabled={identityDisabled}><FieldLabel htmlFor="social-from-user">{label("social.fromUser", "From user")}</FieldLabel><Input ref={(element) => registerField("from_user", element)} id="social-from-user" aria-invalid={Boolean(fromUserError)} aria-describedby={fromUserError?.id} disabled={identityDisabled} value={value.from_user} onChange={(event) => update("from_user", event.currentTarget.value)} />{fromUserError ? <FieldError id={fromUserError.id}>{fromUserError.message}</FieldError> : null}</Field>
      <Field data-invalid={Boolean(toUserError)} data-disabled={identityDisabled}><FieldLabel htmlFor="social-to-user">{label("social.toUser", "To user")}</FieldLabel><Input ref={(element) => registerField("to_user", element)} id="social-to-user" aria-invalid={Boolean(toUserError)} aria-describedby={toUserError?.id} disabled={identityDisabled} value={value.to_user} onChange={(event) => update("to_user", event.currentTarget.value)} />{toUserError ? <FieldError id={toUserError.id}>{toUserError.message}</FieldError> : null}</Field>
      <Field data-invalid={Boolean(groupIdError)} data-disabled={identityDisabled}><FieldLabel htmlFor="social-group-id">{label("social.groupId", "Group ID")}</FieldLabel><Input ref={(element) => registerField("group_id", element)} id="social-group-id" aria-invalid={Boolean(groupIdError)} aria-describedby={groupIdError?.id} disabled={identityDisabled} value={value.group_id} onChange={(event) => update("group_id", event.currentTarget.value)} />{groupIdError ? <FieldError id={groupIdError.id}>{groupIdError.message}</FieldError> : null}</Field>
      <Field data-invalid={Boolean(relationTypeError)} data-disabled={disabled}><FieldLabel htmlFor="social-relation-type">{label("social.relationType", "Relation type")}</FieldLabel><Select items={relationItems} value={value.relation_type} onValueChange={(next) => next && update("relation_type", next)} disabled={disabled}><SelectTrigger ref={(element) => registerField("relation_type", element)} id="social-relation-type" aria-label={label("social.relationType", "Relation type")} aria-invalid={Boolean(relationTypeError)} aria-describedby={relationTypeError?.id}><SelectValue /></SelectTrigger><SelectContent><SelectGroup>{relationItems.map((item) => <SelectItem key={item.value} value={item.value}>{item.label}</SelectItem>)}</SelectGroup></SelectContent></Select>{relationTypeError ? <FieldError id={relationTypeError.id}>{relationTypeError.message}</FieldError> : null}</Field>
      <Field data-invalid={Boolean(strengthError)} data-disabled={disabled}><FieldLabel htmlFor="social-strength">{label("social.strength", "Strength")}</FieldLabel><Input ref={(element) => registerField("strength", element)} id="social-strength" type="number" min="0" max="1" step="0.01" aria-invalid={Boolean(strengthError)} aria-describedby={strengthError?.id} disabled={disabled} value={value.strength} onChange={(event) => updateBoundedNumber(event.currentTarget.value)} />{strengthError ? <FieldError id={strengthError.id}>{strengthError.message}</FieldError> : null}</Field>
      <Field data-invalid={Boolean(tagsError)} data-disabled={disabled}><FieldLabel>{label("field.tags", "Tags")}</FieldLabel><TagEditor inputRef={(element) => registerField("tags", element)} label={label("field.tags", "Tags")} getRemoveLabel={(tag) => t("tags.remove", tag)} values={value.tags} onChange={(next) => update("tags", next)} disabled={disabled} ariaInvalid={Boolean(tagsError)} ariaDescribedBy={tagsError?.id} />{tagsError ? <FieldError id={tagsError.id}>{tagsError.message}</FieldError> : null}</Field>
    </>;
    }}
  </EditFormLayout>;
}
