import { useEffect, type RefObject } from "react";

/**
 * Закрывает всплывающий элемент по клику вне `ref` или по Escape.
 * `ref` должен оборачивать И триггер, И само меню — тогда клик по триггеру
 * (тоже внутри ref) не считается «вне» и не конфликтует с его onClick-тоглом.
 */
export function useDismiss(open: boolean, onClose: () => void, ref: RefObject<HTMLElement | null>) {
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) onClose();
    };
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open, onClose, ref]);
}
