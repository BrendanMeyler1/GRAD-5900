import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import clsx from "clsx";

export default function MessageBubble({ role, content, timestamp }) {
  const isUser = role === "user";

  return (
    <div
      className={clsx("flex w-full", isUser ? "justify-end" : "justify-start")}
    >
      <div className={clsx("max-w-[75%] space-y-1", isUser && "text-right")}>
        <div
          className={clsx(
            "inline-block rounded-lg px-4 py-2.5 text-sm leading-relaxed",
            isUser
              ? "rounded-br-sm bg-indigo-600 text-white"
              : "rounded-bl-sm bg-slate-800 text-slate-100"
          )}
        >
          {isUser ? (
            <p className="whitespace-pre-wrap">{content}</p>
          ) : (
            <ReactMarkdown
              remarkPlugins={[remarkGfm]}
              className="prose prose-invert prose-sm max-w-none"
              components={{
                code({ inline, className, children, ...props }) {
                  if (inline) {
                    return (
                      <code
                        className="rounded bg-slate-900 px-1.5 py-0.5 text-xs text-slate-200"
                        {...props}
                      >
                        {children}
                      </code>
                    );
                  }
                  return (
                    <code
                      className={clsx(
                        "block overflow-x-auto rounded bg-slate-900 p-3 text-xs text-slate-200",
                        className
                      )}
                      {...props}
                    >
                      {children}
                    </code>
                  );
                },
                pre({ children }) {
                  return <pre className="my-2 overflow-x-auto">{children}</pre>;
                },
                ul({ children }) {
                  return (
                    <ul className="my-1.5 list-disc space-y-0.5 pl-4 text-slate-200">
                      {children}
                    </ul>
                  );
                },
                ol({ children }) {
                  return (
                    <ol className="my-1.5 list-decimal space-y-0.5 pl-4 text-slate-200">
                      {children}
                    </ol>
                  );
                },
                li({ children }) {
                  return <li className="text-slate-200">{children}</li>;
                },
                a({ href, children }) {
                  return (
                    <a
                      href={href}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-indigo-400 underline hover:text-indigo-300"
                    >
                      {children}
                    </a>
                  );
                },
                p({ children }) {
                  return <p className="my-1 text-slate-100">{children}</p>;
                },
                strong({ children }) {
                  return (
                    <strong className="font-semibold text-slate-50">
                      {children}
                    </strong>
                  );
                },
                table({ children }) {
                  return (
                    <div className="my-2 overflow-x-auto">
                      <table className="min-w-full text-sm">{children}</table>
                    </div>
                  );
                },
                th({ children }) {
                  return (
                    <th className="border-b border-slate-600 px-3 py-1.5 text-left text-xs font-semibold text-slate-300">
                      {children}
                    </th>
                  );
                },
                td({ children }) {
                  return (
                    <td className="border-b border-slate-700 px-3 py-1.5 text-slate-300">
                      {children}
                    </td>
                  );
                },
              }}
            >
              {content}
            </ReactMarkdown>
          )}
        </div>

        {timestamp && (
          <p
            className={clsx(
              "text-xs text-slate-500",
              isUser ? "pr-1 text-right" : "pl-1 text-left"
            )}
          >
            {timestamp}
          </p>
        )}
      </div>
    </div>
  );
}
