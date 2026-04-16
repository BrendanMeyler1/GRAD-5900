import { useState } from "react";
import { NavLink, Outlet, useLocation } from "react-router-dom";
import { Search, ClipboardList, MessageSquare, UploadCloud } from "lucide-react";
import clsx from "clsx";
import { usePendingApplications } from "../hooks/useApplications";
import ProfileCompletionBar from "./profile/ProfileCompletionBar";
import ToastContainer from "./shared/ToastContainer";

const NAV_ITEMS = [
  { to: "/discover", label: "Discover", icon: Search },
  { to: "/apply", label: "Apply", icon: ClipboardList, showBadge: true },
  { to: "/chat", label: "Chat", icon: MessageSquare },
  { to: "/onboard", label: "Upload Resume", icon: UploadCloud },
];

export default function Layout() {
  const [expanded, setExpanded] = useState(false);
  const location = useLocation();
  const { applications } = usePendingApplications();

  const pendingCount = Array.isArray(applications) ? applications.length : 0;

  return (
    <div className="flex min-h-screen bg-slate-900">
      <ToastContainer />

      {/* Sidebar */}
      <nav
        onMouseEnter={() => setExpanded(true)}
        onMouseLeave={() => setExpanded(false)}
        className={clsx(
          "fixed inset-y-0 left-0 z-40 flex flex-col border-r border-slate-700/50 bg-slate-900 py-6 transition-all duration-200",
          expanded ? "w-48" : "w-16"
        )}
      >
        {/* Nav items */}
        <div className="flex flex-1 flex-col gap-1 px-2">
          {NAV_ITEMS.map((item) => {
            const Icon = item.icon;
            const isActive = location.pathname.startsWith(item.to);

            return (
              <NavLink
                key={item.to}
                to={item.to}
                className={clsx(
                  "relative flex items-center gap-3 rounded-lg px-3 py-2.5 text-sm font-medium transition-colors",
                  isActive
                    ? "border-l-2 border-indigo-500 bg-slate-800 text-slate-100"
                    : "border-l-2 border-transparent text-slate-400 hover:bg-slate-800 hover:text-slate-200"
                )}
              >
                <Icon className="h-5 w-5 shrink-0" />
                <span
                  className={clsx(
                    "whitespace-nowrap transition-opacity duration-200",
                    expanded ? "opacity-100" : "pointer-events-none opacity-0"
                  )}
                >
                  {item.label}
                </span>

                {/* Pending review badge */}
                {item.showBadge && pendingCount > 0 && (
                  <span
                    className={clsx(
                      "flex h-5 min-w-[20px] items-center justify-center rounded-full bg-indigo-500 px-1.5 text-[10px] font-bold text-white",
                      expanded
                        ? "ml-auto"
                        : "absolute -right-1 -top-1"
                    )}
                  >
                    {pendingCount > 99 ? "99+" : pendingCount}
                  </span>
                )}
              </NavLink>
            );
          })}
        </div>

        {/* Profile completion at bottom */}
        <div className="mt-auto px-3">
          <ProfileCompletionBar />
        </div>
      </nav>

      {/* Main content */}
      <main
        className={clsx(
          "flex-1 overflow-y-auto bg-slate-950 transition-all duration-200",
          expanded ? "ml-48" : "ml-16"
        )}
      >
        <Outlet />
      </main>
    </div>
  );
}
