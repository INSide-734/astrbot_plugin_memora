import { useState, useCallback, useRef } from "react";

export interface ToastState {
  message: string;
  isError: boolean;
  visible: boolean;
}

export function useToast() {
  const [toast, setToast] = useState<ToastState>({ message: "", isError: false, visible: false });
  const timerRef = useRef<ReturnType<typeof setTimeout>>();

  const showToast = useCallback((message: string, isError = false) => {
    clearTimeout(timerRef.current);
    setToast({ message, isError, visible: true });
    timerRef.current = setTimeout(() => setToast((prev) => ({ ...prev, visible: false })), 2500);
  }, []);

  return { toast, showToast };
}
