import {
  Field,
  FieldContent,
  FieldTitle,
} from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { Alert, AlertDescription } from "@/components/ui/alert";
import type {
  GateActionKind,
  GateRuleAction,
  GateRuleData,
} from "@/types/config";
import type { GateEditorLabel } from "./RuleConditionEditor";

const ACTION_LABEL_KEYS: Record<string, string> = {
  force_disposition: "gate.rules.actionForce",
  importance_delta: "gate.rules.actionImportanceDelta",
  set_importance: "gate.rules.actionSetImportance",
  add_topics: "gate.rules.actionAddTopics",
  set_privacy: "gate.rules.actionSetPrivacy",
  drop_atoms: "gate.rules.actionDropAtoms",
};

const FORCE_VALUES = ["allow", "quarantine", "discard", "mark_write"] as const;
const FORCE_LABEL_KEYS: Record<string, string> = {
  allow: "gate.rules.forceAllow",
  quarantine: "gate.disposition.quarantine",
  discard: "gate.disposition.discard",
  mark_write: "gate.disposition.markWrite",
};
const PRIVACY_LABEL_KEYS: Record<string, string> = {
  public: "gate.rules.privacyPublic",
  confidential: "gate.rules.privacyConfidential",
};

/** 按 kind 生成互斥且完整的动作 payload 默认值。 */
export function actionForKind(kind: GateActionKind): GateRuleAction {
  switch (kind) {
    case "force_disposition":
      return { kind, value: "discard" };
    case "importance_delta":
      return { kind, delta: 0 };
    case "set_importance":
      return { kind, value: 0.5 };
    case "add_topics":
      return { kind, values: [] };
    case "set_privacy":
      return { kind, value: "public" };
    case "drop_atoms":
      return { kind, value: true };
  }
}

/** 规则列表行的动作摘要文案。 */
export function actionSummary(
  label: GateEditorLabel,
  rule: GateRuleData,
): string {
  const kindLabel = label(ACTION_LABEL_KEYS[rule.action.kind]);
  switch (rule.action.kind) {
    case "force_disposition":
      return `${kindLabel}: ${label(FORCE_LABEL_KEYS[String(rule.action.value)])}`;
    case "importance_delta":
      return `${kindLabel}: ${String(rule.action.delta)}`;
    case "set_importance":
      return `${kindLabel}: ${String(rule.action.value)}`;
    case "add_topics":
      return `${kindLabel}: ${(rule.action.values ?? []).join(", ")}`;
    case "set_privacy":
      return `${kindLabel}: ${label(PRIVACY_LABEL_KEYS[String(rule.action.value)])}`;
    case "drop_atoms":
      return kindLabel;
  }
}

interface ActionEditorProps {
  action: GateRuleAction;
  label: GateEditorLabel;
  disabled: boolean;
  onChange: (action: GateRuleAction) => void;
}

/** 动作六选一表单：按 kind 互斥携带 payload，allow/set_privacy/drop_atoms 附警示。 */
export function ActionEditor({
  action,
  label,
  disabled,
  onChange,
}: ActionEditorProps) {
  const kinds = Object.keys(ACTION_LABEL_KEYS) as GateActionKind[];

  return (
    <div className="flex min-w-0 flex-col gap-2">
      <Field>
        <FieldContent>
          <FieldTitle>{label("gate.rules.action")}</FieldTitle>
        </FieldContent>
        <Select
          items={kinds.map((kind) => ({
            label: label(ACTION_LABEL_KEYS[kind]),
            value: kind,
          }))}
          value={action.kind}
          disabled={disabled}
          onValueChange={(value) => {
            if (value) onChange(actionForKind(value as GateActionKind));
          }}
        >
          <SelectTrigger
            aria-label={label("gate.rules.action")}
            size="sm"
            className="w-full max-w-72"
          >
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectGroup>
              {kinds.map((kind) => (
                <SelectItem key={kind} value={kind}>
                  {label(ACTION_LABEL_KEYS[kind])}
                </SelectItem>
              ))}
            </SelectGroup>
          </SelectContent>
        </Select>
      </Field>
      {action.kind === "force_disposition" ? (
        <Field>
          <Select
            items={FORCE_VALUES.map((value) => ({
              label: label(FORCE_LABEL_KEYS[value]),
              value,
            }))}
            value={String(action.value ?? "discard")}
            disabled={disabled}
            onValueChange={(value) => {
              if (value) onChange({ kind: action.kind, value });
            }}
          >
            <SelectTrigger
              aria-label={label("gate.rules.actionValue")}
              size="sm"
              className="w-full max-w-72"
            >
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectGroup>
                {FORCE_VALUES.map((value) => (
                  <SelectItem key={value} value={value}>
                    {label(FORCE_LABEL_KEYS[value])}
                  </SelectItem>
                ))}
              </SelectGroup>
            </SelectContent>
          </Select>
          {action.value === "allow" ? (
            <Alert variant="destructive" className="rounded-md">
              <AlertDescription>
                {label("gate.rules.warningAllow")}
              </AlertDescription>
            </Alert>
          ) : null}
        </Field>
      ) : null}
      {action.kind === "importance_delta" ? (
        <Input
          aria-label={label("gate.rules.actionDelta")}
          type="number"
          min={-1}
          max={1}
          step={0.1}
          value={typeof action.delta === "number" ? action.delta : ""}
          disabled={disabled}
          onChange={(event) => {
            const value = event.currentTarget.valueAsNumber;
            onChange({
              kind: action.kind,
              delta: Number.isFinite(value) ? value : null,
            });
          }}
          className="h-8 w-32"
        />
      ) : null}
      {action.kind === "set_importance" ? (
        <Input
          aria-label={label("gate.rules.actionValue")}
          type="number"
          min={0}
          max={1}
          step={0.05}
          value={typeof action.value === "number" ? action.value : ""}
          disabled={disabled}
          onChange={(event) => {
            const value = event.currentTarget.valueAsNumber;
            onChange({
              kind: action.kind,
              value: Number.isFinite(value) ? value : null,
            });
          }}
          className="h-8 w-32"
        />
      ) : null}
      {action.kind === "add_topics" ? (
        <Textarea
          aria-label={label("gate.rules.actionValues")}
          value={(action.values ?? []).join("\n")}
          disabled={disabled}
          rows={3}
          onChange={(event) =>
            onChange({
              kind: action.kind,
              values: event.currentTarget.value
                .split("\n")
                .map((entry) => entry.trim())
                .filter(Boolean),
            })
          }
        />
      ) : null}
      {action.kind === "set_privacy" ? (
        <Field>
          <Select
            items={Object.keys(PRIVACY_LABEL_KEYS).map((value) => ({
              label: label(PRIVACY_LABEL_KEYS[value]),
              value,
            }))}
            value={String(action.value ?? "public")}
            disabled={disabled}
            onValueChange={(value) => {
              if (value) onChange({ kind: action.kind, value });
            }}
          >
            <SelectTrigger
              aria-label={label("gate.rules.actionValue")}
              size="sm"
              className="w-full max-w-72"
            >
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectGroup>
                {Object.keys(PRIVACY_LABEL_KEYS).map((value) => (
                  <SelectItem key={value} value={value}>
                    {label(PRIVACY_LABEL_KEYS[value])}
                  </SelectItem>
                ))}
              </SelectGroup>
            </SelectContent>
          </Select>
          <Alert variant="destructive" className="rounded-md">
            <AlertDescription>
              {label("gate.rules.warningSetPrivacy")}
            </AlertDescription>
          </Alert>
        </Field>
      ) : null}
      {action.kind === "drop_atoms" ? (
        <Alert variant="destructive" className="rounded-md">
          <AlertDescription>
            {label("gate.rules.warningDropAtoms")}
          </AlertDescription>
        </Alert>
      ) : null}
    </div>
  );
}
