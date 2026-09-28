import { useRef } from "react";

export function HistoryResizeHandle({
  height,
  onChange,
}: {
  height: number;
  onChange: (height: number) => void;
}) {
  const drag = useRef<{ y: number; height: number } | undefined>(undefined);
  const resize = (value: number) =>
    onChange(Math.min(560, Math.max(140, value)));
  return (
    <div
      className="history-resize-handle"
      role="separator"
      aria-label="调整运行记录高度"
      aria-orientation="horizontal"
      aria-valuemin={140}
      aria-valuemax={560}
      aria-valuenow={height}
      tabIndex={0}
      title="拖动调整运行记录高度；方向键微调；双击恢复默认"
      onPointerDown={(event) => {
        if (event.button !== 0) return;
        event.preventDefault();
        drag.current = { y: event.clientY, height };
        event.currentTarget.setPointerCapture(event.pointerId);
      }}
      onPointerMove={(event) => {
        if (drag.current)
          resize(drag.current.height + drag.current.y - event.clientY);
      }}
      onPointerUp={(event) => {
        drag.current = undefined;
        if (event.currentTarget.hasPointerCapture(event.pointerId))
          event.currentTarget.releasePointerCapture(event.pointerId);
      }}
      onLostPointerCapture={() => {
        drag.current = undefined;
      }}
      onDoubleClick={() => onChange(300)}
      onKeyDown={(event) => {
        if (!["ArrowUp", "ArrowDown", "Home", "End"].includes(event.key))
          return;
        event.preventDefault();
        event.stopPropagation();
        resize(
          event.key === "Home"
            ? 140
            : event.key === "End"
              ? 560
              : height + (event.key === "ArrowUp" ? 20 : -20),
        );
      }}
    />
  );
}
