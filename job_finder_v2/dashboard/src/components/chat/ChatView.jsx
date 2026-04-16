import { useState, useEffect, useRef, useCallback } from "react";
import { useQueryClient } from "@tanstack/react-query";
import MessageBubble from "./MessageBubble";
import ChatInput from "./ChatInput";
import ContextPanel from "./ContextPanel";
import { useToast } from "../../hooks/useToast";
import api from "../../api/client";

const EMPTY_HINT =
  "What would you like to do? Try: 'Find machine learning jobs in Seattle' or 'Tailor my resume for the Stripe job.'";

export default function ChatView() {
  const [messages, setMessages] = useState([]);
  const [isLoading, setIsLoading] = useState(false);
  const [contextType, setContextType] = useState(null);
  const [contextId, setContextId] = useState(null);
  const [contextData, setContextData] = useState(null);
  const [panelCollapsed, setPanelCollapsed] = useState(false);

  const bottomRef = useRef(null);
  const inputRef = useRef(null);
  const queryClient = useQueryClient();
  const { addToast } = useToast();

  // Load history on mount
  useEffect(() => {
    api
      .get("/api/chat/history?limit=50")
      .then((data) => {
        if (Array.isArray(data)) {
          setMessages(
            data.map((m) => ({
              role: m.role,
              content: m.content,
              timestamp: m.created_at,
            }))
          );
        }
      })
      .catch(() => {});
  }, []);

  // Auto-scroll to bottom
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  // "/" key focuses input from anywhere
  useEffect(() => {
    const handleKey = (e) => {
      if (
        e.key === "/" &&
        document.activeElement?.tagName !== "TEXTAREA" &&
        document.activeElement?.tagName !== "INPUT"
      ) {
        e.preventDefault();
        inputRef.current?.focus?.();
      }
    };
    document.addEventListener("keydown", handleKey);
    return () => document.removeEventListener("keydown", handleKey);
  }, []);

  const handleSend = useCallback(
    async (message) => {
      if (!message.trim() || isLoading) return;

      // Optimistically add user message
      const userMsg = {
        role: "user",
        content: message,
        timestamp: new Date().toISOString(),
      };
      setMessages((prev) => [...prev, userMsg]);
      setIsLoading(true);

      // Streaming assistant message placeholder
      const assistantMsgId = Date.now();
      setMessages((prev) => [
        ...prev,
        { id: assistantMsgId, role: "assistant", content: "", timestamp: null, streaming: true },
      ]);

      try {
        await api.stream(
          "/api/chat",
          {
            message,
            context_type: contextType,
            context_id: contextId,
            use_tools: true,
          },
          (event) => {
            if (event.type === "chunk") {
              setMessages((prev) =>
                prev.map((m) =>
                  m.id === assistantMsgId
                    ? { ...m, content: m.content + (event.text || "") }
                    : m
                )
              );
            } else if (event.type === "action") {
              if (event.action === "jobs_updated") {
                queryClient.invalidateQueries({ queryKey: ["jobs"] });
              }
              if (event.action === "applications_updated") {
                queryClient.invalidateQueries({ queryKey: ["applications"] });
              }
            } else if (event.type === "context") {
              setContextType(event.context_type);
              setContextId(event.context_id);
            } else if (event.type === "error") {
              addToast({ message: event.message || "Chat error", type: "error" });
            } else if (event.type === "done") {
              setMessages((prev) =>
                prev.map((m) =>
                  m.id === assistantMsgId
                    ? {
                        ...m,
                        streaming: false,
                        timestamp: new Date().toISOString(),
                      }
                    : m
                )
              );
            }
          }
        );
      } catch (err) {
        addToast({ message: "Failed to send message", type: "error" });
        // Remove the empty assistant message
        setMessages((prev) => prev.filter((m) => m.id !== assistantMsgId));
      } finally {
        setIsLoading(false);
      }
    },
    [isLoading, contextType, contextId, queryClient, addToast]
  );

  return (
    <div className="flex h-full">
      {/* Chat column */}
      <div className="flex min-w-0 flex-1 flex-col">
        {/* Message list */}
        <div className="flex-1 overflow-y-auto px-4 py-6">
          <div className="mx-auto max-w-3xl space-y-4">
            {messages.length === 0 ? (
              <div className="flex h-full items-center justify-center py-20">
                <p className="max-w-md text-center text-sm text-slate-500">
                  {EMPTY_HINT}
                </p>
              </div>
            ) : (
              messages.map((msg, i) => (
                <MessageBubble
                  key={msg.id || i}
                  role={msg.role}
                  content={msg.content}
                  timestamp={msg.timestamp}
                  streaming={msg.streaming}
                />
              ))
            )}
            <div ref={bottomRef} />
          </div>
        </div>

        {/* Input */}
        <ChatInput
          ref={inputRef}
          onSend={handleSend}
          isLoading={isLoading}
        />
      </div>

      {/* Context panel */}
      {!panelCollapsed && (
        <ContextPanel
          contextType={contextType}
          contextId={contextId}
          contextData={contextData}
          collapsed={panelCollapsed}
          onToggle={() => setPanelCollapsed((v) => !v)}
        />
      )}
      {panelCollapsed && (
        <button
          onClick={() => setPanelCollapsed(false)}
          className="flex w-6 cursor-pointer items-center justify-center border-l border-slate-700 bg-slate-900 text-slate-500 hover:text-slate-300"
          aria-label="Expand context panel"
        >
          <span className="rotate-90 text-xs">▲</span>
        </button>
      )}
    </div>
  );
}
