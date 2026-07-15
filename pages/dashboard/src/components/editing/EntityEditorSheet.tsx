import { useRef } from "react";
import type { KeyboardEvent } from "react";

import { Button } from "@/components/ui/Button";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/Sheet";

export interface EntityEditorLabels {
  edit: string;
  close: string;
  cancel: string;
  save: string;
  saving: string;
}

export interface EntityEditorSheetProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  description: string;
  mode: "view" | "edit";
  isDirty: boolean;
  isSubmitting: boolean;
  canSave: boolean;
  onBeginEdit: () => void;
  onCancel: () => void;
  onSave: () => void | Promise<void>;
  view: React.ReactNode;
  form: React.ReactNode;
  labels: EntityEditorLabels;
}

export function EntityEditorSheet({
  open,
  onOpenChange,
  title,
  description,
  mode,
  isDirty,
  isSubmitting,
  canSave,
  onBeginEdit,
  onCancel,
  onSave,
  view,
  form,
  labels,
}: EntityEditorSheetProps) {
  const saveInFlight = useRef(false);
  const maySave = mode === "edit" && isDirty && canSave && !isSubmitting && !saveInFlight.current;
  const requestSave = () => {
    if (mode !== "edit" || !isDirty || !canSave || isSubmitting || saveInFlight.current) return;
    saveInFlight.current = true;
    Promise.resolve()
      .then(() => onSave())
      .catch(() => undefined)
      .finally(() => {
        saveInFlight.current = false;
      });
  };
  const handleKeyDown = (event: KeyboardEvent<HTMLFormElement>) => {
    if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
      event.preventDefault();
      requestSave();
    }
  };
  const requestClose = () => {
    if (!isSubmitting) onOpenChange(false);
  };

  return (
    <Sheet open={open} onOpenChange={(nextOpen) => {
      if (nextOpen || !isSubmitting) onOpenChange(nextOpen);
    }}>
      <SheetContent showCloseButton={false} className="w-[min(100vw,32rem)]">
        <SheetHeader className="shrink-0">
          <SheetTitle>{title}</SheetTitle>
          <SheetDescription>{description}</SheetDescription>
          <Button type="button" variant="ghost" size="icon-sm" className="absolute right-3 top-3" aria-label={labels.close} disabled={isSubmitting} onClick={requestClose}>
            <span aria-hidden="true">×</span>
          </Button>
        </SheetHeader>
        <form className="min-h-0 flex-1 overflow-y-auto px-5 py-4" onKeyDown={handleKeyDown}>
          {mode === "view" ? view : form}
        </form>
        <SheetFooter data-testid="entity-editor-footer" className="shrink-0">
          {mode === "view" ? (
            <Button type="button" onClick={onBeginEdit} disabled={isSubmitting}>{labels.edit}</Button>
          ) : (
            <>
              <Button type="button" variant="outline" onClick={onCancel} disabled={isSubmitting}>{labels.cancel}</Button>
              <Button type="button" disabled={!maySave} onClick={requestSave}>{isSubmitting ? labels.saving : labels.save}</Button>
            </>
          )}
        </SheetFooter>
      </SheetContent>
    </Sheet>
  );
}
