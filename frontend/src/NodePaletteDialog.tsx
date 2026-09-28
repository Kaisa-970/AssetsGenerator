import { useEffect, useRef, type ReactNode } from "react";

/** Native modality keeps keyboard focus and graph shortcuts out of the canvas. */
export function NodePaletteDialog({
  children,
  onClose,
}: {
  children: ReactNode;
  onClose: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const close = useRef(onClose);
  close.current = onClose;
  useEffect(() => {
    const element = dialog.current!;
    const previous = document.activeElement;
    element.showModal();
    element.querySelector<HTMLInputElement>('input[aria-label="搜索节点"]')?.focus();
    return () => {
      element.close();
      if (previous instanceof HTMLElement && previous.isConnected) previous.focus();
    };
  }, []);
  return (
    <dialog
      ref={dialog}
      className="node-palette-backdrop"
      aria-label="添加节点"
      onCancel={(event) => {
        event.preventDefault();
        close.current();
      }}
      onKeyDown={(event) => event.stopPropagation()}
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) close.current();
      }}
    >
      {children}
    </dialog>
  );
}
