"use client";
import { useRef, useState, type CSSProperties, type ReactNode } from "react";

/** Edit a text layer in its own bounds. Enter/blur commits; Escape cancels. */
export function InlineText({ value, label, buttonLabel = value, title, onCommit, onBegin, onEditing, onDraft, formatValue, editorStyle, onMoveStart, onMove, onNudge, className, style, children, multiline = false, maxLength }: {
  value: string;
  label: string;
  buttonLabel?: string;
  title?: string;
  onCommit: (value: string) => void;
  onBegin?: () => void;
  onEditing?: (editing: boolean) => void;
  onDraft?: (text: string | null) => void;
  formatValue?: (text: string) => string;
  editorStyle?: CSSProperties;
  onMoveStart?: () => void;
  onMove?: (dx: number, dy: number) => void;
  onNudge?: (dx: number, dy: number) => void;
  className?: string;
  style?: CSSProperties;
  children?: ReactNode;
  multiline?: boolean;
  maxLength?: number;
}) {
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(value);
  const finished = useRef(false);
  const drag = useRef<{ x: number; y: number; moved: boolean } | null>(null);
  const dragged = useRef(false);
  const finish = (commit: boolean) => {
    if (finished.current) return;
    finished.current = true;
    if (commit) onCommit(text.trim().replace(/\s+/g, " "));
    onDraft?.(null);
    setEditing(false);
    onEditing?.(false);
  };
  if (!editing) return (
    <button type="button" aria-label={buttonLabel} title={title} className={`${className ?? ""} ${onMove ? "touch-none" : ""}`} style={style}
      onKeyDown={event => {
        if (!onNudge || !["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(event.key)) return;
        event.preventDefault();
        event.stopPropagation();
        const step = event.shiftKey ? 10 : 1;
        onNudge(event.key === "ArrowLeft" ? -step : event.key === "ArrowRight" ? step : 0, event.key === "ArrowUp" ? -step : event.key === "ArrowDown" ? step : 0);
      }}
      onPointerDown={event => {
        if (!onMove) return;
        dragged.current = false;
        drag.current = { x: event.clientX, y: event.clientY, moved: false };
        onMoveStart?.();
        event.currentTarget.setPointerCapture(event.pointerId);
      }}
      onPointerMove={event => {
        const from = drag.current;
        if (!from || !(event.buttons & 1)) return;
        const dx = event.clientX - from.x, dy = event.clientY - from.y;
        if (Math.hypot(dx, dy) > 3) from.moved = true;
        if (from.moved) onMove?.(dx, dy);
      }}
      onPointerUp={() => { dragged.current = drag.current?.moved ?? false; drag.current = null; }}
      onPointerCancel={() => { drag.current = null; }}
      onClick={() => {
      if (dragged.current) { dragged.current = false; return; }
      finished.current = false;
      const initial = formatValue?.(value) ?? value;
      setText(initial);
      setEditing(true);
      onEditing?.(true);
      onBegin?.();
      onDraft?.(initial);
    }}>{children}</button>
  );
  const props = {
    autoFocus: true,
    "aria-label": label,
    value: text,
    maxLength,
    className,
    style: { ...style, ...editorStyle },
    spellCheck: false,
    onChange: (event: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) => {
      const input = event.target, raw = input.value;
      const formatted = (event.nativeEvent as InputEvent).isComposing ? raw : formatValue?.(raw) ?? raw;
      const start = raw.slice(0, input.selectionStart ?? raw.length).replace(/\s+/g, " ").trimStart().length;
      const end = raw.slice(0, input.selectionEnd ?? raw.length).replace(/\s+/g, " ").trimStart().length;
      setText(formatted); onDraft?.(formatted);
      if (formatted !== raw) requestAnimationFrame(() => {
        if (document.activeElement === input) input.setSelectionRange(start, end);
      });
    },
    onBlur: () => finish(true),
    onKeyDown: (event: React.KeyboardEvent<HTMLInputElement | HTMLTextAreaElement>) => {
      event.stopPropagation();
      if (event.nativeEvent.isComposing) return;
      if (event.key === "Escape" || (event.key === "Enter" && !event.shiftKey)) {
        event.preventDefault();
        finish(event.key === "Enter");
      }
    },
  };
  return multiline ? <textarea {...props} /> : <input {...props} />;
}
