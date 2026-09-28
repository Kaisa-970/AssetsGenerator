import { useRef } from "react";

/** Workspace-only sizing; never part of a pipeline or execution snapshot. */
export function PanelResizeHandle({
  width,
  onChange,
}: {
  width: number;
  onChange: (width: number) => void;
}) {
  const drag = useRef<{ x: number; width: number } | undefined>(undefined);
  const resize = (value: number) =>
    onChange(Math.min(600, Math.max(260, value)));
  return (
    <div
      className="panel-resize-handle"
      role="separator"
      aria-label="调整属性面板宽度"
      aria-orientation="vertical"
      aria-valuemin={260}
      aria-valuemax={600}
      aria-valuenow={width}
      tabIndex={0}
      title="拖动调整宽度；方向键微调；双击恢复默认"
      onPointerDown={(event) => {
        if (event.button !== 0) return;
        event.preventDefault();
        drag.current = { x: event.clientX, width };
        event.currentTarget.setPointerCapture(event.pointerId);
      }}
      onPointerMove={(event) => {
        if (drag.current)
          resize(drag.current.width + drag.current.x - event.clientX);
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
        if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key))
          return;
        event.preventDefault();
        event.stopPropagation();
        resize(
          event.key === "Home"
            ? 260
            : event.key === "End"
              ? 600
              : width + (event.key === "ArrowLeft" ? 20 : -20),
        );
      }}
    />
  );
}
