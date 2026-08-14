import { Plus, Trash2 } from "lucide-react";

import { Button } from "@/components/ui/Button";
import {
  Field,
  FieldDescription,
  FieldError,
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
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/textarea";
import type {
  GateCompareOp,
  GateRuleField,
  GateRulePredicate,
} from "@/types/config";
import {
  GATE_AND_OR_MAX_DEPTH,
  isGroupPredicate,
  validateRuleRegex,
} from "./validation";

export type GateEditorLabel = (key: string, ...args: string[]) => string;

const FIELDS: GateRuleField[] = [
  "content",
  "summary",
  "key_facts",
  "topics",
  "participants",
  "importance",
];

const LEAF_OPS: GateRulePredicate["op"][] = [
  "regex",
  "contains",
  "exists",
  "length_cmp",
];
const NUMERIC_OPS: GateRulePredicate["op"][] = ["numeric_cmp"];
const CMP_OPS: GateCompareOp[] = ["gt", "gte", "lt", "lte", "eq"];

const CMP_LABEL_KEYS: Record<string, string> = {
  gt: "gate.rules.cmpGt",
  gte: "gate.rules.cmpGte",
  lt: "gate.rules.cmpLt",
  lte: "gate.rules.cmpLte",
  eq: "gate.rules.cmpEq",
};

const OP_LABEL_KEYS: Record<string, string> = {
  regex: "gate.rules.opRegex",
  contains: "gate.rules.opContains",
  exists: "gate.rules.opExists",
  length_cmp: "gate.rules.opLength",
  numeric_cmp: "gate.rules.opNumeric",
};

export function emptyLeaf(): GateRulePredicate {
  return { op: "regex", field: "content", pattern: "" };
}

export function emptyGroup(): GateRulePredicate {
  return { op: "and", children: [emptyLeaf()] };
}

function leafFor(field: GateRuleField, op: GateRulePredicate["op"]): GateRulePredicate {
  if (op === "regex") return { op, field, pattern: "" };
  if (op === "contains") return { op, field, values: [] };
  if (op === "exists") return { op, field };
  return { op, field, cmp: "gte", value: 0 };
}

interface LeafRowProps {
  node: GateRulePredicate;
  label: GateEditorLabel;
  disabled: boolean;
  onChange: (node: GateRulePredicate) => void;
  onRemove: () => void;
}

/** 叶谓词行：not 包裹以「取反」勾选呈现。 */
function LeafRow({ node, label, disabled, onChange, onRemove }: LeafRowProps) {
  const isNot = node.op === "not";
  const leaf: GateRulePredicate =
    isNot && node.child ? node.child : isNot ? emptyLeaf() : node;

  const setLeaf = (next: GateRulePredicate) => {
    onChange(isNot ? { op: "not", child: next } : next);
  };

  const ops = leaf.field === "importance" ? NUMERIC_OPS : LEAF_OPS;
  const regexIssue =
    leaf.op === "regex" && leaf.pattern
      ? validateRuleRegex(leaf.pattern)
      : null;

  return (
    <div className="flex min-w-0 flex-col gap-2 rounded-md border p-3">
      <div className="flex min-w-0 flex-wrap items-center gap-2">
        <Select
          items={FIELDS.map((field) => ({
            label: label(`gate.rules.field.${field}`),
            value: field,
          }))}
          value={leaf.field ?? null}
          disabled={disabled}
          onValueChange={(value) => {
            if (value) {
              const next = leafFor(value as GateRuleField, leaf.op);
              onChange(isNot ? { op: "not", child: next } : next);
            }
          }}
        >
          <SelectTrigger
            aria-label={label("gate.rules.fieldLabel")}
            size="sm"
            className="min-w-32"
          >
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectGroup>
              {FIELDS.map((field) => (
                <SelectItem key={field} value={field}>
                  {label(`gate.rules.field.${field}`)}
                </SelectItem>
              ))}
            </SelectGroup>
          </SelectContent>
        </Select>
        <Select
          items={ops.map((op) => ({
            label: label(OP_LABEL_KEYS[op]),
            value: op,
          }))}
          value={leaf.op}
          disabled={disabled}
          onValueChange={(value) => {
            if (value) {
              const next = leafFor(leaf.field ?? "content", value);
              onChange(isNot ? { op: "not", child: next } : next);
            }
          }}
        >
          <SelectTrigger
            aria-label={label("gate.rules.op")}
            size="sm"
            className="min-w-40"
          >
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectGroup>
              {ops.map((op) => (
                <SelectItem key={op} value={op}>
                  {label(OP_LABEL_KEYS[op])}
                </SelectItem>
              ))}
            </SelectGroup>
          </SelectContent>
        </Select>
        <Button
          type="button"
          variant="outline"
          size="icon"
          aria-label={label("gate.rules.conditionRemove")}
          disabled={disabled}
          onClick={onRemove}
        >
          <Trash2 aria-hidden="true" />
        </Button>
        <label className="flex min-w-0 items-center gap-1.5 text-xs text-muted-foreground">
          <input
            type="checkbox"
            checked={isNot}
            disabled={disabled}
            onChange={(event) => {
              if (event.target.checked) {
                onChange({ op: "not", child: leaf });
              } else {
                onChange(leaf);
              }
            }}
            className="accent-primary"
          />
          {label("gate.rules.conditionNegate")}
        </label>
      </div>
      {leaf.op === "regex" ? (
        <Field data-invalid={regexIssue !== null}>
          <Input
            aria-label={label("gate.rules.pattern")}
            value={leaf.pattern ?? ""}
            disabled={disabled}
            onChange={(event) =>
              setLeaf({ ...leaf, pattern: event.currentTarget.value })
            }
            className="h-8"
          />
          <FieldDescription>{label("gate.rules.patternHint")}</FieldDescription>
          {regexIssue ? (
            <FieldError>
              {label("gate.rules.regexError", regexIssue)}
            </FieldError>
          ) : null}
        </Field>
      ) : null}
      {leaf.op === "contains" ? (
        <Textarea
          aria-label={label("gate.rules.values")}
          value={(leaf.values ?? []).join("\n")}
          disabled={disabled}
          rows={3}
          onChange={(event) =>
            setLeaf({
              ...leaf,
              values: event.currentTarget.value
                .split("\n")
                .map((entry) => entry.trim())
                .filter(Boolean),
            })
          }
        />
      ) : null}
      {leaf.op === "length_cmp" || leaf.op === "numeric_cmp" ? (
        <div className="flex min-w-0 flex-wrap items-center gap-2">
          <Select
            items={CMP_OPS.map((cmp) => ({
              label: label(CMP_LABEL_KEYS[cmp]),
              value: cmp,
            }))}
            value={leaf.cmp ?? null}
            disabled={disabled}
            onValueChange={(value) => {
              if (value) setLeaf({ ...leaf, cmp: value as GateCompareOp });
            }}
          >
            <SelectTrigger
              aria-label={label("gate.rules.cmp")}
              size="sm"
              className="min-w-32"
            >
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectGroup>
                {CMP_OPS.map((cmp) => (
                  <SelectItem key={cmp} value={cmp}>
                    {label(CMP_LABEL_KEYS[cmp])}
                  </SelectItem>
                ))}
              </SelectGroup>
            </SelectContent>
          </Select>
          <Input
            aria-label={label("gate.rules.value")}
            type="number"
            value={leaf.value ?? 0}
            disabled={disabled}
            onChange={(event) => {
              const value = event.currentTarget.valueAsNumber;
              setLeaf({
                ...leaf,
                value: Number.isFinite(value) ? value : null,
              });
            }}
            className="h-8 w-28"
          />
        </div>
      ) : null}
    </div>
  );
}

interface GroupEditorProps {
  node: GateRulePredicate & { op: "and" | "or" };
  depth: number;
  root: boolean;
  label: GateEditorLabel;
  disabled: boolean;
  onChange: (node: GateRulePredicate) => void;
  onRemove?: () => void;
}

/** AND/OR 分组编辑器：最多嵌套 2 层，子节点为叶或嵌套分组。 */
function GroupEditor({
  node,
  depth,
  root,
  label,
  disabled,
  onChange,
  onRemove,
}: GroupEditorProps) {
  const children = node.children ?? [];
  const addGroupDisabled = depth >= GATE_AND_OR_MAX_DEPTH;

  const replaceChild = (index: number, next: GateRulePredicate) => {
    onChange({
      ...node,
      children: children.map((child, childIndex) =>
        childIndex === index ? next : child,
      ),
    });
  };

  const removeChild = (index: number) => {
    onChange({
      ...node,
      children: children.filter((_, childIndex) => childIndex !== index),
    });
  };

  return (
    <div className="flex min-w-0 flex-col gap-2 rounded-md border p-3">
      <div className="flex min-w-0 flex-wrap items-center gap-2">
        <span className="text-xs font-medium text-muted-foreground">
          {label("gate.rules.conditionGroup")}
        </span>
        <Tabs
          value={node.op}
          onValueChange={(value) => {
            if (value === "and" || value === "or") {
              onChange({ ...node, op: value });
            }
          }}
        >
          <TabsList aria-label={label("gate.rules.conditionGroup")}>
            <TabsTrigger value="and" disabled={disabled}>
              {label("gate.rules.conditionMatchAll")}
            </TabsTrigger>
            <TabsTrigger value="or" disabled={disabled}>
              {label("gate.rules.conditionMatchAny")}
            </TabsTrigger>
          </TabsList>
        </Tabs>
        <div className="flex min-w-0 items-center gap-1">
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={disabled || addGroupDisabled}
            onClick={() =>
              onChange({
                ...node,
                children: [...children, emptyGroup()],
              })
            }
          >
            <Plus data-icon="inline-start" />
            {label("gate.rules.conditionAddGroup")}
          </Button>
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={disabled}
            onClick={() =>
              onChange({ ...node, children: [...children, emptyLeaf()] })
            }
          >
            <Plus data-icon="inline-start" />
            {label("gate.rules.conditionAddLeaf")}
          </Button>
          {!root && onRemove ? (
            <Button
              type="button"
              variant="outline"
              size="icon"
              aria-label={label("gate.rules.conditionRemove")}
              disabled={disabled}
              onClick={onRemove}
            >
              <Trash2 aria-hidden="true" />
            </Button>
          ) : null}
        </div>
      </div>
      <div className="flex min-w-0 flex-col gap-2 pl-2">
        {children.map((child, index) =>
          isGroupPredicate(child) ? (
            <GroupEditor
              key={index}
              node={child}
              depth={depth + 1}
              root={false}
              label={label}
              disabled={disabled}
              onChange={(next) => replaceChild(index, next)}
              onRemove={() => removeChild(index)}
            />
          ) : (
            <LeafRow
              key={index}
              node={child}
              label={label}
              disabled={disabled}
              onChange={(next) => replaceChild(index, next)}
              onRemove={() => removeChild(index)}
            />
          ),
        )}
      </div>
    </div>
  );
}

export interface PredicateEditorProps {
  node: GateRulePredicate;
  label: GateEditorLabel;
  disabled: boolean;
  onChange: (node: GateRulePredicate) => void;
}

/** 谓词树编辑器：根为分组或单叶（叶根可移除并重置为空白分组）。 */
export function PredicateEditor({
  node,
  label,
  disabled,
  onChange,
}: PredicateEditorProps) {
  if (isGroupPredicate(node)) {
    return (
      <GroupEditor
        node={node}
        depth={1}
        root
        label={label}
        disabled={disabled}
        onChange={onChange}
      />
    );
  }
  return (
    <LeafRow
      node={node}
      label={label}
      disabled={disabled}
      onChange={onChange}
      onRemove={() => onChange(emptyGroup())}
    />
  );
}
