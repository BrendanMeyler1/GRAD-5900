import React, { createContext, useContext, useState, useCallback } from "react";

const ToastContext = createContext(null);

export function useToast() {
  const context = useContext(ToastContext);
  if (!context) {
    throw new Error("useToast must be used within a ToastProvider");
  }
  return context;
}

export function ToastProvider({ children }) {
  const [toasts, setToasts] = useState([]);

  const addToast = useCallback((message, type = "info", duration = 4000) => {
    const id = Date.now() + Math.random().toString();
    setToasts((prev) => [...prev, { id, message, type }]);

    if (duration > 0) {
      setTimeout(() => {
        setToasts((prev) => prev.filter((t) => t.id !== id));
      }, duration);
    }
  }, []);

  const removeToast = useCallback((id) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

  return (
    <ToastContext.Provider value={{ addToast }}>
      {children}
      <div
        style={{
          position: "fixed",
          bottom: "20px",
          right: "20px",
          display: "flex",
          flexDirection: "column",
          gap: "10px",
          zIndex: 9999,
        }}
      >
        {toasts.map((t) => (
          <div
            key={t.id}
            onClick={() => removeToast(t.id)}
            style={{
              minWidth: "250px",
              background: 
                t.type === "error" ? "#fee2e2" : 
                t.type === "success" ? "#dcfce7" : 
                t.type === "warning" ? "#fef3c7" : "#e0e7ff",
              color: 
                t.type === "error" ? "#991b1b" : 
                t.type === "success" ? "#166534" : 
                t.type === "warning" ? "#92400e" : "#3730a3",
              padding: "12px 16px",
              borderRadius: "8px",
              boxShadow: "0 4px 6px -1px rgba(0, 0, 0, 0.1)",
              border: "1px solid",
              borderColor:
                t.type === "error" ? "#f87171" : 
                t.type === "success" ? "#4ade80" : 
                t.type === "warning" ? "#fbbf24" : "#818cf8",
              cursor: "pointer",
              transition: "opacity 0.3s ease",
              animation: "slideIn 0.3s ease",
            }}
          >
            <style>
              {`
                @keyframes slideIn {
                  from { transform: translateX(100%); opacity: 0; }
                  to { transform: translateX(0); opacity: 1; }
                }
              `}
            </style>
            <strong>{t.type === "error" ? "❌ Error: " : t.type === "success" ? "✅ Success: " : t.type === "warning" ? "⚠️ Warning: " : "ℹ️ Info: "}</strong>
            {t.message}
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}
