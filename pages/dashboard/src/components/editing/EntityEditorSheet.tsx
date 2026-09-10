import { useRef } from "react";
import type { KeyboardEvent, ReactNode } from "react";
import { X } from "lucide-react";

import { Button } from "@/components/ui/Button";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";

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
  status?: ReactNode;
  viewActions?: ReactNode;
  editStatus?: ReactNode;
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
  status,
  viewActions,
  editStatus,
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
      <SheetContent showCloseButton={false} className="w-full sm:max-w-[42rem]">
        <SheetHeader data-testid="entity-editor-header" className="shrink-0">
          <SheetTitle>{title}</SheetTitle>
          <SheetDescription>{description}</SheetDescription>
          {status ? <div className="text-sm text-muted-foreground">{status}</div> : null}
          <Button type="button" variant="ghost" size="icon-sm" className="absolute right-3 top-3" aria-label={labels.close} disabled={isSubmitting} onClick={requestClose}>
            <X aria-hidden="true" />
          </Button>
        </SheetHeader>
        <div data-testid="entity-editor-body" className="min-h-0 flex-1 overflow-y-auto">
          <form className="px-5 py-4" onKeyDown={handleKeyDown}>
            {mode === "view" ? view : form}
          </form>
        </div>
        <SheetFooter
          data-testid="entity-editor-footer"
          className="shrink-0 justify-between gap-3 border-t bg-background px-5 py-3 pb-[max(0.75rem,env(safe-area-inset-bottom))] sm:flex-row"
        >
          <div className="flex min-w-0 flex-wrap items-center gap-2">
            {mode === "view" ? viewActions : editStatus}
          </div>
          <div className="ml-auto flex flex-wrap items-center justify-end gap-2">
            {mode === "view" ? (
              <Button type="button" onClick={onBeginEdit} disabled={isSubmitting}>{labels.edit}</Button>
            ) : (
              <>
                <Button type="button" variant="outline" onClick={onCancel} disabled={isSubmitting}>{labels.cancel}</Button>
                <Button type="button" disabled={!maySave} onClick={requestSave}>{isSubmitting ? labels.saving : labels.save}</Button>
              </>
            )}
          </div>
        </SheetFooter>
      </SheetContent>
    </Sheet>
  );
}
