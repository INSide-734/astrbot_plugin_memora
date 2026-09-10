import { useRef, useState } from "react";

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/Button";

export interface ActionConfirmDialogProps {
  open: boolean;
  title: string;
  description: string;
  cancelLabel: string;
  actionLabel: string;
  pendingLabel: string;
  destructive?: boolean;
  pending: boolean;
  error?: string | null;
  onCancel(): void;
  onConfirm(): Promise<void> | void;
}

export function ActionConfirmDialog({
  open,
  title,
  description,
  cancelLabel,
  actionLabel,
  pendingLabel,
  destructive = false,
  pending,
  error,
  onCancel,
  onConfirm,
}: ActionConfirmDialogProps) {
  const confirmingRef = useRef(false);
  const [confirming, setConfirming] = useState(false);
  const locked = pending || confirming;

  const isLocked = () => pending || confirmingRef.current;

  const cancel = () => {
    if (!isLocked()) onCancel();
  };

  const confirm = () => {
    if (isLocked()) return;
    confirmingRef.current = true;
    setConfirming(true);

    let settlement: Promise<void>;
    try {
      settlement = Promise.resolve(onConfirm());
    } catch {
      settlement = Promise.resolve();
    }
    void settlement.catch(() => undefined).finally(() => {
      confirmingRef.current = false;
      setConfirming(false);
    });
  };

  return (
    <Dialog
      open={open}
      disablePointerDismissal={locked}
      onOpenChange={(nextOpen, eventDetails) => {
        if (!nextOpen && isLocked()) {
          eventDetails.cancel();
          return;
        }
        if (!nextOpen) onCancel();
      }}
    >
      <DialogContent showCloseButton={false}>
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>{description}</DialogDescription>
        </DialogHeader>
        {error ? <p role="alert" className="text-sm text-destructive">{error}</p> : null}
        <DialogFooter>
          <Button type="button" variant="outline" disabled={locked} onClick={cancel}>
            {cancelLabel}
          </Button>
          <Button type="button" variant={destructive ? "destructive" : "default"} disabled={locked} onClick={confirm}>
            {locked ? pendingLabel : actionLabel}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
