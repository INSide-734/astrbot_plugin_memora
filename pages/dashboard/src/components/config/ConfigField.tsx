import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import {
  Field,
  FieldContent,
  FieldDescription,
  FieldError,
  FieldGroup,
  FieldLabel,
} from "@/components/ui/field";
import { useI18n } from "@/hooks/useI18n";
import { cn } from "@/lib/utils";
import { validatePromptTemplate } from "./promptTemplateValidation";
import type {
  ConfigProviderOptions,
  ConfigSchemaNode,
  ConfigValue,
  PromptDefaults,
} from "@/types/config";

export interface ConfigFieldProps {
  path: string;
  node: ConfigSchemaNode;
  value: ConfigValue;
  onChange: (path: string, value: ConfigValue) => void;
  providerOptions: ConfigProviderOptions;
  disabled?: boolean;
  fieldErrors?: Record<string, string>;
  defaultProviderLabel?: string;
  targetPath?: string | null;
  promptDefaults?: PromptDefaults | null;
}

interface SelectOption {
  label: string;
  value: string | null;
}

function configObject(value: ConfigValue): Record<string, ConfigValue> {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? value
    : {};
}

function pathId(path: string): string {
  const slug =
    path
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-+|-+$/g, "") || "field";
  let hash = 2166136261;
  for (let index = 0; index < path.length; index += 1) {
    hash ^= path.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  return `config-${slug}-${(hash >>> 0).toString(36)}`;
}

function displayLabel(path: string, description?: string): string {
  if (description?.trim()) return description.trim();
  const segments = path.split(".");
  return segments[segments.length - 1] || path;
}

function providerKind(path: string, node: ConfigSchemaNode) {
  if (path === "provider_settings.embedding_provider_id") return "embedding";
  if (
    path === "provider_settings.llm_provider_id" ||
    node._special === "select_provider"
  ) {
    return "llm";
  }
  return null;
}

function selectOptions(
  path: string,
  node: ConfigSchemaNode,
  providers: ConfigProviderOptions,
  defaultProviderLabel: string
): SelectOption[] | null {
  const kind = providerKind(path, node);
  if (kind) {
    return [
      { label: defaultProviderLabel, value: null },
      ...providers[kind].map(({ id, label }) => ({ label, value: id })),
    ];
  }
  if (!node.options) return null;
  return node.options.map((option) => ({
    label: String(option),
    value: String(option),
  }));
}

export function ConfigField({
  path,
  node,
  value,
  onChange,
  providerOptions,
  disabled = false,
  fieldErrors = {},
  defaultProviderLabel = "Use AstrBot default",
  targetPath,
  promptDefaults = null,
}: ConfigFieldProps) {
  const { t } = useI18n();
  if (node.invisible) return null;

  const promptTemplateIssue =
    path === "prompt_templates.group_chat_template" ||
    path === "prompt_templates.private_chat_template"
      ? validatePromptTemplate(typeof value === "string" ? value : "")
      : null;
  const promptPlaceholder =
    path === "prompt_templates.group_chat_template"
      ? promptDefaults?.group_chat
      : path === "prompt_templates.private_chat_template"
        ? promptDefaults?.private_chat
        : undefined;
  const id = pathId(path);
  const label = displayLabel(path, node.description);
  const highlighted = targetPath === path;

  if (node.type === "object") {
    const values = configObject(value);
    return (
      <section
        aria-labelledby={`${id}-heading`}
        className={cn(
          "flex scroll-mt-4 flex-col gap-3 transition-shadow motion-reduce:transition-none",
          highlighted &&
            "rounded-lg ring-2 ring-ring ring-offset-2 ring-offset-background",
        )}
        data-config-highlighted={highlighted ? "true" : undefined}
        data-config-path={path}
        data-slot="config-group"
      >
        <div className="flex min-w-0 flex-col gap-1">
          <div
            data-slot="config-group-heading"
            className="flex min-w-0 flex-wrap items-baseline justify-between gap-x-2 gap-y-0.5"
          >
            <h2 id={`${id}-heading`} className="text-sm font-medium">
              {label}
            </h2>
            <code className="ml-auto min-w-0 max-w-full break-all text-right text-xs text-muted-foreground">
              {path}
            </code>
          </div>
          {path === "prompt_templates" || node.hint ? (
            <p className="text-sm text-muted-foreground">
              {path === "prompt_templates"
                ? t("promptTemplates.help.section")
                : node.hint}
            </p>
          ) : null}
        </div>
        <FieldGroup>
          {Object.entries(node.items).map(([key, childNode]) => {
            const childPath = `${path}.${key}`;
            return (
              <ConfigField
                key={childPath}
                path={childPath}
                node={childNode}
                value={values[key]}
                onChange={onChange}
                providerOptions={providerOptions}
                disabled={disabled}
                fieldErrors={fieldErrors}
                defaultProviderLabel={defaultProviderLabel}
                targetPath={targetPath}
              />
            );
          })}
        </FieldGroup>
      </section>
    );
  }

  const error = fieldErrors[path];
  const describedBy = [node.hint ? `${id}-hint` : null, error ? `${id}-error` : null]
    .filter(Boolean)
    .join(" ") || undefined;
  const options = selectOptions(
    path,
    node,
    providerOptions,
    defaultProviderLabel
  );

  const commonControlProps = {
    id,
    disabled,
    "aria-invalid": error ? true : undefined,
    "aria-describedby": describedBy,
  };

  let control;
  if (options) {
    const isProvider = providerKind(path, node) !== null;
    const selectedValue =
      isProvider && (value === "" || value == null)
        ? null
        : typeof value === "number"
          ? String(value)
          : value;
    control = (
      <Select
        items={options}
        value={selectedValue as string | null}
        onValueChange={(nextValue) => {
          if (isProvider) {
            onChange(path, nextValue ?? "");
          } else if (node.type === "int" || node.type === "float") {
            onChange(path, nextValue == null ? undefined : Number(nextValue));
          } else {
            onChange(path, nextValue ?? undefined);
          }
        }}
        disabled={disabled}
      >
        <SelectTrigger
          {...commonControlProps}
          className="w-full"
        >
          <SelectValue />
        </SelectTrigger>
        <SelectContent
          align="start"
          alignItemWithTrigger={false}
          className="w-[var(--anchor-width)] min-w-[var(--anchor-width)]"
        >
          <SelectGroup>
            {options.map((option) => (
              <SelectItem
                key={option.value === null ? "__default__" : String(option.value)}
                value={option.value}
              >
                {option.label}
              </SelectItem>
            ))}
          </SelectGroup>
        </SelectContent>
      </Select>
    );
  } else if (node.type === "bool") {
    control = (
      <Switch
        {...commonControlProps}
        checked={Boolean(value)}
        onCheckedChange={(checked) => onChange(path, checked)}
      />
    );
  } else if (node.type === "text") {
    control = (
      <Textarea
        {...commonControlProps}
        value={typeof value === "string" ? value : ""}
        placeholder={promptPlaceholder || undefined}
        onChange={(event) => onChange(path, event.currentTarget.value)}
      />
    );
  } else if (node.type === "int" || node.type === "float") {
    control = (
      <Input
        {...commonControlProps}
        type="number"
        value={typeof value === "number" && Number.isFinite(value) ? value : ""}
        min={node.min}
        max={node.max}
        step={node.step ?? (node.type === "int" ? 1 : "any")}
        onChange={(event) => {
          const nextValue = event.currentTarget.value;
          onChange(path, nextValue === "" ? null : Number(nextValue));
        }}
      />
    );
  } else {
    control = (
      <Input
        {...commonControlProps}
        value={typeof value === "string" ? value : ""}
        onChange={(event) => onChange(path, event.currentTarget.value)}
      />
    );
  }

  return (
    <Field
      orientation={node.type === "bool" ? "horizontal" : "vertical"}
      className={cn(
        "scroll-mt-4 transition-shadow motion-reduce:transition-none",
        node.type === "bool" &&
          "flex-wrap items-start justify-between gap-x-4 gap-y-2",
        highlighted &&
          "rounded-lg ring-2 ring-ring ring-offset-2 ring-offset-background",
      )}
      data-config-highlighted={highlighted ? "true" : undefined}
      data-config-path={path}
      data-disabled={disabled ? true : undefined}
      data-invalid={error ? true : undefined}
    >
      <FieldContent className="min-w-0">
        <div
          data-slot="config-field-heading"
          className="flex min-w-0 flex-wrap items-baseline justify-between gap-x-2 gap-y-0.5"
        >
          <FieldLabel htmlFor={id}>{label}</FieldLabel>
          <code className="ml-auto min-w-0 max-w-full break-all text-right text-xs text-muted-foreground">
            {path}
          </code>
        </div>
        {path === "prompt_templates.group_chat_template" ||
        path === "prompt_templates.private_chat_template" ? (
          <FieldDescription id={`${id}-hint`}>
            {t("promptTemplates.help.templateHint")}
          </FieldDescription>
        ) : node.hint ? (
          <FieldDescription id={`${id}-hint`}>{node.hint}</FieldDescription>
        ) : null}
      </FieldContent>
      {control}
      {error ? (
        <FieldError
          id={`${id}-error`}
          className={node.type === "bool" ? "basis-full" : undefined}
        >
          {error}
        </FieldError>
      ) : null}
      {promptTemplateIssue?.code === "missing_conversation" ? (
        <FieldError id={`${id}-template-error`}>
          {t("promptTemplates.error.missingConversation")}
        </FieldError>
      ) : null}
      {promptTemplateIssue?.code === "unknown_placeholders" ? (
        <FieldError id={`${id}-template-error`}>
          {t(
            "promptTemplates.error.unknownPlaceholders",
            promptTemplateIssue.placeholders.join(", "),
          )}
        </FieldError>
      ) : null}
      {promptTemplateIssue?.code === "unclosed_brace" ? (
        <FieldError id={`${id}-template-error`}>
          {t("promptTemplates.error.unclosedBrace")}
        </FieldError>
      ) : null}
    </Field>
  );
}
