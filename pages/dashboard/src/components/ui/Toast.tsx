import { cn } from "@/lib/utils";
import type { ToastState } from "@/hooks/useToast";

interface ToastProps {
  toast: ToastState;
}

export function Toast({ toast }: ToastProps) {
  return (
    <div
      className={cn(
        "fixed bottom-6 right-6 z-50 max-w-sm rounded-xl px-4 py-3 text-sm font-medium shadow-modal transition-all duration-300",
        toast.visible ? "translate-y-0 opacity-100" : "translate-y-2 opacity-0 pointer-events-none",
        toast.isError
          ? "bg-[var(--color-danger)] text-white"
          : "bg-[var(--color-surface-elevated)] text-[var(--text-primary)] border border-[var(--color-border)]"
      )}
      role="alert"
    >
      {toast.message}
    </div>
  );
}
