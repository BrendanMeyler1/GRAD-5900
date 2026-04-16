import { Routes, Route, Navigate } from "react-router-dom";
import Layout from "./components/Layout";
import OnboardView from "./components/onboard/OnboardView";
import DiscoverView from "./components/discover/DiscoverView";
import ApplyView from "./components/apply/ApplyView";
import ChatView from "./components/chat/ChatView";
import { useProfile } from "./hooks/useProfile";
import { ToastProvider } from "./hooks/useToast";

function LoadingScreen() {
  return (
    <div className="flex min-h-screen items-center justify-center bg-slate-950">
      <div className="h-8 w-8 animate-spin rounded-full border-2 border-indigo-500 border-t-transparent" />
    </div>
  );
}

function AppRoutes() {
  const { profile, isLoading } = useProfile();

  if (isLoading) return <LoadingScreen />;

  const hasProfile =
    profile !== null &&
    profile !== undefined &&
    (profile.profile?.first_name || profile.first_name);

  return (
    <Routes>
      {/* Onboarding — always accessible */}
      <Route path="/onboard" element={<OnboardView />} />

      {/* Root redirect */}
      <Route
        path="/"
        element={
          hasProfile ? (
            <Navigate to="/discover" replace />
          ) : (
            <Navigate to="/onboard" replace />
          )
        }
      />

      {/* Main app — gated behind profile */}
      <Route
        element={
          hasProfile ? <Layout /> : <Navigate to="/onboard" replace />
        }
      >
        <Route path="/discover" element={<DiscoverView />} />
        <Route path="/apply" element={<ApplyView />} />
        <Route path="/chat" element={<ChatView />} />
      </Route>

      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}

export default function App() {
  return (
    <ToastProvider>
      <AppRoutes />
    </ToastProvider>
  );
}
