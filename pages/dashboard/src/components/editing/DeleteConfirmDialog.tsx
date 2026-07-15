import { useState } from "react";

import { Button } from "@/components/ui/Button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/Dialog";
import { Input } from "@/components/ui/Input";

export interface ConfirmationRequirement {
  label: string;
  expectedText: string;
}

export interface DeleteConfirmDialogProps {
  open: boolean;
  title: string;
  description: string;
  cancelLabel: string;
  confirmLabel: string;
  confirmationRequirement?: ConfirmationRequirement;
  onCancel: () => void;
  onConfirm: () => void;
}

export function DeleteConfirmDialog({
  open,
  title,
  description,
  cancelLabel,
  confirmLabel,
  confirmationRequirement,
  onCancel,
  onConfirm,
}: DeleteConfirmDialogProps) {
  const [confirmation, setConfirmation] = useState("");
  const confirmationSatisfied = !confirmationRequirement || confirmation === confirmationRequirement.expectedText;

  return (
    <Dialog open={open} onOpenChange={(nextOpen) => !nextOpen && onCancel()}>
      <DialogContent showCloseButton={false} className="min-w-0 sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>{description}</DialogDescription>
        </DialogHeader>
        {confirmationRequirement ? (
          <label className="flex flex-col gap-2 text-sm font-medium">
            {confirmationRequirement.label}
            <Input value={confirmation} onChange={(event) => setConfirmation(event.target.value)} />
          </label>
        ) : null}
        <DialogFooter className="rounded-b-lg sm:flex-wrap">
          <Button type="button" variant="outline" onClick={onCancel}>
            {cancelLabel}
          </Button>
          <Button type="button" variant="destructive" disabled={!confirmationSatisfied} onClick={onConfirm}>
            {confirmLabel}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
