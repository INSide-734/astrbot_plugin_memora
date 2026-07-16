import { useState } from "react";
import type { KeyboardEvent, Ref } from "react";
import { X } from "lucide-react";

import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";

export interface TagEditorProps {
  label: string;
  getRemoveLabel: (tag: string) => string;
  values: readonly string[];
  onChange: (values: string[]) => void;
  maxCount?: number;
  onLimitReached?: () => void;
  disabled?: boolean;
  ariaDescribedBy?: string;
  ariaInvalid?: boolean;
  inputRef?: Ref<HTMLInputElement>;
}

export function TagEditor({ label, getRemoveLabel, values, onChange, maxCount, onLimitReached, disabled = false, ariaDescribedBy, ariaInvalid = false, inputRef }: TagEditorProps) {
  const [pending, setPending] = useState("");
  const addPending = () => {
    const tag = pending.trim();
    if (!tag || values.includes(tag)) {
      setPending("");
      return;
    }
    if (maxCount !== undefined && values.length >= maxCount) {
      onLimitReached?.();
      return;
    }
    onChange([...values, tag]);
    setPending("");
  };
  const onKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "Enter") {
      event.preventDefault();
      addPending();
    } else if (event.key === "Backspace" && pending.length === 0 && values.length > 0) {
      onChange(values.slice(0, -1));
    }
  };

  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap gap-1.5" aria-label={label}>
        {values.map((tag, index) => (
          <Badge key={`${tag}-${index}`} variant="secondary" className="gap-0.5 pr-0.5">
            {tag}
            <Button type="button" variant="ghost" size="icon-xs" className="size-5 rounded-full" aria-label={getRemoveLabel(tag)} disabled={disabled} onClick={() => onChange(values.filter((_, valueIndex) => valueIndex !== index))}>
              <X aria-hidden="true" />
            </Button>
          </Badge>
        ))}
      </div>
      <Input ref={inputRef} aria-label={label} aria-invalid={ariaInvalid} aria-describedby={ariaDescribedBy} value={pending} disabled={disabled} onChange={(event) => setPending(event.target.value)} onKeyDown={onKeyDown} />
    </div>
  );
}
