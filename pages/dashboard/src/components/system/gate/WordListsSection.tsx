import { Plus, Trash2 } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/Button";
import {
  Field,
  FieldContent,
  FieldDescription,
  FieldError,
  FieldGroup,
  FieldLegend,
  FieldSet,
  FieldTitle,
} from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Separator } from "@/components/ui/separator";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useI18n } from "@/hooks/useI18n";
import type {
  GateListMode,
  GateProfileData,
  GateSynonymPair,
  GateWordListConfig,
} from "@/types/config";
import {
  GATE_SYNONYM_ITEM_MAX,
  GATE_SYNONYM_PAIR_MAX,
  GATE_WORD_LIST_ITEM_MAX,
  GATE_WORD_LIST_MAX_ITEMS,
} from "./validation";

export interface WordListsSectionProps {
  profile: GateProfileData;
  disabled: boolean;
  onChange: (patch: Partial<GateProfileData>) => void;
}

interface ItemListEditorProps {
  id: string;
  label: string;
  hint?: string;
  items: string[];
  limit: number;
  disabled: boolean;
  onItems: (items: string[]) => void;
}

/** 词项列表编辑：追加输入 + 逐项移除，超上限禁用添加。 */
function ItemListEditor({
  id,
  label,
  hint,
  items,
  limit,
  disabled,
  onItems,
}: ItemListEditorProps) {
  const { t } = useI18n();
  const [draft, setDraft] = useState("");
  const [error, setError] = useState<string | null>(null);
  const reached = items.length >= limit;

  const add = () => {
    const value = draft.trim();
    if (!value) return;
    if (value.length > GATE_WORD_LIST_ITEM_MAX) {
      setError(t("gate.wordlists.itemLengthHint", String(GATE_WORD_LIST_ITEM_MAX)));
      return;
    }
    if (items.includes(value)) {
      setDraft("");
      return;
    }
    onItems([...items, value]);
    setDraft("");
    setError(null);
  };

  return (
    <Field>
      <FieldContent>
        <FieldTitle>{label}</FieldTitle>
        {hint ? <FieldDescription>{hint}</FieldDescription> : null}
      </FieldContent>
      <ul className="flex min-w-0 list-none flex-col gap-1">
        {items.map((item) => (
          <li
            key={item}
            className="flex min-w-0 items-center justify-between gap-2 rounded-md border px-2 py-1"
          >
            <span className="min-w-0 break-all text-sm">{item}</span>
            <Button
              type="button"
              variant="outline"
              size="icon"
              aria-label={`${t("gate.wordlists.remove")} ${item}`}
              disabled={disabled}
              onClick={() => onItems(items.filter((entry) => entry !== item))}
            >
              <Trash2 aria-hidden="true" />
            </Button>
          </li>
        ))}
      </ul>
      <div className="flex min-w-0 items-center gap-2">
        <Input
          id={id}
          aria-label={label}
          value={draft}
          disabled={disabled || reached}
          maxLength={GATE_WORD_LIST_ITEM_MAX + 1}
          onChange={(event) => {
            setDraft(event.currentTarget.value);
            setError(null);
          }}
          onKeyDown={(event) => {
            if (event.key === "Enter") add();
          }}
          className="h-8 min-w-0 flex-1"
        />
        <Button
          type="button"
          variant="outline"
          disabled={disabled || reached || !draft.trim()}
          onClick={add}
        >
          <Plus data-icon="inline-start" />
          {t("gate.wordlists.add")}
        </Button>
      </div>
      {reached ? (
        <p className="text-xs text-muted-foreground">
          {t("gate.wordlists.itemLimitHint", String(limit))}
        </p>
      ) : null}
      {error ? <FieldError>{error}</FieldError> : null}
    </Field>
  );
}

interface ModeListEditorProps {
  id: string;
  label: string;
  hint?: string;
  config: GateWordListConfig;
  disabled: boolean;
  onChange: (config: GateWordListConfig) => void;
}

/** 带 append/replace 模式的词表编辑。 */
function ModeListEditor({
  id,
  label,
  hint,
  config,
  disabled,
  onChange,
}: ModeListEditorProps) {
  const { t } = useI18n();
  return (
    <Field>
      <FieldContent>
        <FieldTitle>{label}</FieldTitle>
        {hint ? <FieldDescription>{hint}</FieldDescription> : null}
        <div className="flex min-w-0 items-center gap-2">
          <span className="text-xs text-muted-foreground">
            {t("gate.wordlists.modeLabel")}
          </span>
          <Tabs
            value={config.mode}
            onValueChange={(value) => {
              if (value) onChange({ ...config, mode: value as GateListMode });
            }}
          >
            <TabsList aria-label={`${label} ${t("gate.wordlists.modeLabel")}`}>
              <TabsTrigger value="append" disabled={disabled}>
                {t("gate.wordlists.modeAppend")}
              </TabsTrigger>
              <TabsTrigger value="replace" disabled={disabled}>
                {t("gate.wordlists.modeReplace")}
              </TabsTrigger>
            </TabsList>
          </Tabs>
        </div>
      </FieldContent>
      <ItemListEditor
        id={id}
        label={label}
        items={config.items}
        limit={GATE_WORD_LIST_MAX_ITEMS}
        disabled={disabled}
        onItems={(items) => onChange({ ...config, items })}
      />
    </Field>
  );
}

/** 词表：否定白名单、否定标记集、泛化词与同义替换对。 */
export function WordListsSection({
  profile,
  disabled,
  onChange,
}: WordListsSectionProps) {
  const { t } = useI18n();
  const [synonymDraft, setSynonymDraft] = useState<GateSynonymPair>({
    source: "",
    target: "",
  });
  const [synonymError, setSynonymError] = useState<string | null>(null);
  const wordLists = profile.word_lists;
  const synonymReached = wordLists.synonym_pairs.length >= GATE_SYNONYM_PAIR_MAX;

  const addSynonym = () => {
    const source = synonymDraft.source.trim();
    const target = synonymDraft.target.trim();
    if (!source || !target) return;
    if (
      source.length > GATE_SYNONYM_ITEM_MAX ||
      target.length > GATE_SYNONYM_ITEM_MAX
    ) {
      setSynonymError(
        t("gate.wordlists.itemLengthHint", String(GATE_SYNONYM_ITEM_MAX)),
      );
      return;
    }
    onChange({
      word_lists: {
        ...wordLists,
        synonym_pairs: [...wordLists.synonym_pairs, { source, target }],
      },
    });
    setSynonymDraft({ source: "", target: "" });
    setSynonymError(null);
  };

  return (
    <FieldSet className="rounded-lg border p-4">
      <FieldLegend>{t("gate.wordlists.title")}</FieldLegend>
      <p className="text-sm text-muted-foreground">{t("gate.help.wordlists")}</p>
      <FieldGroup>
        <ItemListEditor
          id="gate-wordlist-whitelist"
          label={t("gate.wordlists.whitelist")}
          items={wordLists.negation_whitelist}
          limit={GATE_WORD_LIST_MAX_ITEMS}
          disabled={disabled}
          onItems={(items) =>
            onChange({
              word_lists: { ...wordLists, negation_whitelist: items },
            })
          }
        />
        <Separator />
        <ModeListEditor
          id="gate-wordlist-markers"
          label={t("gate.wordlists.markers")}
          hint={t("gate.wordlists.markersHint")}
          config={wordLists.negation_markers}
          disabled={disabled}
          onChange={(negation_markers) =>
            onChange({ word_lists: { ...wordLists, negation_markers } })
          }
        />
        <Separator />
        <ModeListEditor
          id="gate-wordlist-generic"
          label={t("gate.wordlists.generic")}
          config={wordLists.generic_terms}
          disabled={disabled}
          onChange={(generic_terms) =>
            onChange({ word_lists: { ...wordLists, generic_terms } })
          }
        />
        <Separator />
        <Field>
          <FieldContent>
            <FieldTitle>{t("gate.wordlists.synonyms")}</FieldTitle>
          </FieldContent>
          <ul className="flex min-w-0 list-none flex-col gap-1">
            {wordLists.synonym_pairs.map((pair, index) => (
              <li
                key={`${pair.source}-${pair.target}-${index}`}
                className="flex min-w-0 items-center gap-2 rounded-md border px-2 py-1"
              >
                <span className="min-w-0 flex-1 break-all text-sm">
                  {pair.source}
                </span>
                <span className="shrink-0 text-xs text-muted-foreground">→</span>
                <span className="min-w-0 flex-1 break-all text-sm">
                  {pair.target}
                </span>
                <Button
                  type="button"
                  variant="outline"
                  size="icon"
                  aria-label={t("gate.wordlists.remove")}
                  disabled={disabled}
                  onClick={() =>
                    onChange({
                      word_lists: {
                        ...wordLists,
                        synonym_pairs: wordLists.synonym_pairs.filter(
                          (_, pairIndex) => pairIndex !== index,
                        ),
                      },
                    })
                  }
                >
                  <Trash2 aria-hidden="true" />
                </Button>
              </li>
            ))}
          </ul>
          <div className="flex min-w-0 flex-wrap items-end gap-2">
            <div className="min-w-0 flex-1">
              <Label
                htmlFor="gate-synonym-source"
                className="text-xs font-medium text-muted-foreground"
              >
                {t("gate.wordlists.source")}
              </Label>
              <Input
                id="gate-synonym-source"
                aria-label={t("gate.wordlists.source")}
                value={synonymDraft.source}
                disabled={disabled || synonymReached}
                maxLength={GATE_SYNONYM_ITEM_MAX}
                onChange={(event) => {
                  setSynonymDraft((previous) => ({
                    ...previous,
                    source: event.currentTarget.value,
                  }));
                  setSynonymError(null);
                }}
                className="h-8"
              />
            </div>
            <div className="min-w-0 flex-1">
              <Label
                htmlFor="gate-synonym-target"
                className="text-xs font-medium text-muted-foreground"
              >
                {t("gate.wordlists.target")}
              </Label>
              <Input
                id="gate-synonym-target"
                aria-label={t("gate.wordlists.target")}
                value={synonymDraft.target}
                disabled={disabled || synonymReached}
                maxLength={GATE_SYNONYM_ITEM_MAX}
                onChange={(event) => {
                  setSynonymDraft((previous) => ({
                    ...previous,
                    target: event.currentTarget.value,
                  }));
                  setSynonymError(null);
                }}
                className="h-8"
              />
            </div>
            <Button
              type="button"
              variant="outline"
              disabled={
                disabled ||
                synonymReached ||
                !synonymDraft.source.trim() ||
                !synonymDraft.target.trim()
              }
              onClick={addSynonym}
            >
              <Plus data-icon="inline-start" />
              {t("gate.wordlists.add")}
            </Button>
          </div>
          {synonymReached ? (
            <p className="text-xs text-muted-foreground">
              {t("gate.wordlists.itemLimitHint", String(GATE_SYNONYM_PAIR_MAX))}
            </p>
          ) : null}
          {synonymError ? <FieldError>{synonymError}</FieldError> : null}
        </Field>
      </FieldGroup>
    </FieldSet>
  );
}
