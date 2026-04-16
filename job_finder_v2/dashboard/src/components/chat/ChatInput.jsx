import { useState, useRef, useCallback, useEffect, forwardRef, useImperativeHandle } from "react";
import { ArrowUp, Loader2 } from "lucide-react";
import clsx from "clsx";

const ChatInput = forwardRef(function ChatInput({ onSend, isLoading }, ref) {
  const [value, setValue] = useState("");
  const textareaRef = useRef(null);

  // Expose focus() via ref so parent (ChatView) can focus the input with "/"
  useImperativeHandle(ref, () => ({
    focus: () => textareaRef.current?.focus(),
  }));

  const isEmpty = value.trim().length === 0;
  const disabled = isEmpty || isLoading;

  /* ---------- auto-resize textarea ---------- */
  const resize = useCallback(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    const lineHeight = parseInt(getComputedStyle(el).lineHeight, 10) || 24;
    const maxHeight = lineHeight * 6;
    el.style.height = `${Math.min(el.scrollHeight, maxHeight)}px`;
  }, []);

  useEffect(() => {
    resize();
  }, [value, resize]);

  /* ---------- send handler ---------- */
  const handleSend = useCallback(async () => {
    const trimmed = value.trim();
    if (!trimmed || isLoading) return;
    setValue("");
    await onSend(trimmed);
  }, [value, isLoading, onSend]);

  /* ---------- keyboard ---------- */
  const handleKeyDown = useCallback(
    (e) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        handleSend();
      }
    },
    [handleSend]
  );

  return (
    <div className="border-t border-slate-700 bg-slate-900 px-4 py-3">
      <div className="mx-auto flex max-w-3xl items-end gap-2">
        <textarea
          ref={textareaRef}
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={handleKeyDown}
          disabled={isLoading}
          placeholder="Type a message..."
          rows={1}
          className={clsx(
            "flex-1 resize-none rounded-lg border bg-slate-800 px-4 py-2.5 text-sm text-slate-100",
            "placeholder:text-slate-500 focus:outline-none focus:ring-2 focus:ring-indigo-500/50",
            "scrollbar-thin scrollbar-track-transparent scrollbar-thumb-slate-600",
            isLoading
              ? "cursor-not-allowed border-slate-700 opacity-60"
              : "border-slate-700"
          )}
        />

        <button
          onClick={handleSend}
          disabled={disabled}
          aria-label="Send message"
          className={clsx(
            "flex h-10 w-10 shrink-0 items-center justify-center rounded-lg transition-colors",
            disabled
              ? "cursor-not-allowed bg-slate-700 text-slate-500"
              : "bg-indigo-600 text-white hover:bg-indigo-500"
          )}
        >
          {isLoading ? (
            <Loader2 className="h-5 w-5 animate-spin" />
          ) : (
            <ArrowUp className="h-5 w-5" />
          )}
        </button>
      </div>
    </div>
  );
});

export default ChatInput;
