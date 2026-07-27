import { useRef } from "react";
import type { KeyboardEvent } from "react";
import { X } from "lucide-react";

import { Button } from "@/components/ui/Button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

export interface EntityCreateLabels {
  close: string;
  cancel: string;
  submit: string;
  submitting: string;
}

export interface EntityCreateDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  description: string;
  isDirty: boolean;
  isSubmitting: boolean;
  canSubmit: boolean;
  onCancel: () => void;
  onSubmit: () => void | Promise<void>;
  form: React.ReactNode;
  labels: EntityCreateLabels;
}

export function EntityCreateDialog({
  open,
  onOpenChange,
  title,
  description,
  isDirty,
  isSubmitting,
  canSubmit,
  onCancel,
  onSubmit,
  form,
  labels,
}: EntityCreateDialogProps) {
  const submitInFlight = useRef(false);
  const maySubmit = isDirty && canSubmit && !isSubmitting && !submitInFlight.current;
  const requestSubmit = () => {
    if (!isDirty || !canSubmit || isSubmitting || submitInFlight.current) return;
    submitInFlight.current = true;
    Promise.resolve()
      .then(() => onSubmit())
      .catch(() => undefined)
      .finally(() => {
        submitInFlight.current = false;
      });
  };
  const handleKeyDown = (event: KeyboardEvent<HTMLFormElement>) => {
    if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
      event.preventDefault();
      requestSubmit();
    }
  };
  const requestClose = () => {
    if (!isSubmitting) onOpenChange(false);
  };

  return (
    <Dialog open={open} onOpenChange={(nextOpen) => {
      if (nextOpen || !isSubmitting) onOpenChange(nextOpen);
    }}>
      <DialogContent showCloseButton={false} className="flex max-h-[calc(100vh-2rem)] min-h-0 w-full max-w-[calc(100%-2rem)] flex-col overflow-hidden p-0 sm:max-w-lg">
        <DialogHeader className="shrink-0 border-b px-5 py-4 pr-12">
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>{description}</DialogDescription>
          <Button type="button" variant="ghost" size="icon-sm" className="absolute right-3 top-3" aria-label={labels.close} disabled={isSubmitting} onClick={requestClose}>
            <X aria-hidden="true" />
          </Button>
        </DialogHeader>
        <form data-testid="entity-create-content" className="min-h-0 flex-1 overflow-y-auto px-5 py-4" onKeyDown={handleKeyDown}>
          {form}
        </form>
        <div className="flex shrink-0 flex-wrap justify-end gap-2 border-t bg-muted/30 px-5 py-4">
          <Button type="button" variant="outline" onClick={onCancel} disabled={isSubmitting}>{labels.cancel}</Button>
          <Button type="button" disabled={!maySubmit} onClick={requestSubmit}>{isSubmitting ? labels.submitting : labels.submit}</Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
