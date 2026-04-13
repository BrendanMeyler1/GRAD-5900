import { startTransition, useDeferredValue, useEffect, useMemo, useState } from "react";
import ApplicationTracker from "./components/ApplicationTracker";
import BatchApproval from "./components/BatchApproval";
import ConfidenceView from "./components/ConfidenceView";
import DecisionQueue from "./components/DecisionQueue";
import HumanAssistPanel from "./components/HumanAssistPanel";
import InsightsPanel from "./components/InsightsPanel";
import ResumePreview from "./components/ResumePreview";
import NewApplicationPanel from "./components/NewApplicationPanel";
import { useToast } from "./components/Toast";

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000/api";

async function apiRequest(path, options = {}) {
  const response = await fetch(`${API_BASE}${path}`, {
    headers: {
      "Content-Type": "application/json"
    },
    ...options
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = payload?.detail || payload?.message || response.statusText;
    throw new Error(String(detail));
  }
  return payload;
}

function getWebSocketBase(apiBase) {
  try {
    const url = new URL(apiBase);
    url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
    url.pathname = url.pathname.replace(/\/api\/?$/, "");
    url.search = "";
    url.hash = "";
    return url.toString().replace(/\/$/, "");
  } catch {
    return "ws://localhost:8000";
  }
}

function parseWsMessage(event) {
  try {
    return JSON.parse(event.data);
  } catch {
    return null;
  }
}

function filterQueue(listings, query) {
  const normalized = query.trim().toLowerCase();
  if (!normalized) {
    return listings;
  }
  return listings.filter((listing) => {
    const company =
      typeof listing.company === "object" ? listing.company?.name : listing.company;
    const roleTitle =
      typeof listing.role === "object" ? listing.role?.title : listing.role_title;
    return `${company || ""} ${roleTitle || ""}`.toLowerCase().includes(normalized);
  });
}

const WS_BASE = import.meta.env.VITE_WS_BASE_URL ?? getWebSocketBase(API_BASE);

export default function App() {
  const [queue, setQueue] = useState([]);
  const [applications, setApplications] = useState([]);
  const [batchCandidates, setBatchCandidates] = useState([]);
  const [selectedListingId, setSelectedListingId] = useState("");
  const [selectedAppId, setSelectedAppId] = useState("");
  const [applicationDetail, setApplicationDetail] = useState(null);
  const [insightsOverview, setInsightsOverview] = useState(null);
  const [insightsBreakdown, setInsightsBreakdown] = useState({});
  const [insightsFailures, setInsightsFailures] = useState([]);
  const [searchQuery, setSearchQuery] = useState("");
  const deferredQuery = useDeferredValue(searchQuery);

  const [loadingQueue, setLoadingQueue] = useState(false);
  const [loadingApplications, setLoadingApplications] = useState(false);
  const [busyListingId, setBusyListingId] = useState("");
  const [runningGroupKey, setRunningGroupKey] = useState("");
  const [resolvingFieldId, setResolvingFieldId] = useState("");
  const [trackerActionBusy, setTrackerActionBusy] = useState(false);
  const [loadingInsights, setLoadingInsights] = useState(false);
  const { addToast } = useToast();

  const filteredQueue = useMemo(
    () => filterQueue(queue, deferredQuery),
    [queue, deferredQuery]
  );

  const statusCounts = useMemo(() => {
    const counts = new Map();
    for (const app of applications) {
      const status = String(app.status || "UNKNOWN").toUpperCase();
      counts.set(status, (counts.get(status) || 0) + 1);
    }
    return counts;
  }, [applications]);

  async function loadQueue() {
    setLoadingQueue(true);
    try {
      const payload = await apiRequest("/jobs/queue");
      setQueue(payload.queue || []);
      if (!selectedListingId && payload.queue?.[0]?.listing_id) {
        setSelectedListingId(String(payload.queue[0].listing_id));
      }
    } catch (error) {
      addToast(`Queue load failed: ${error.message}`, "error");
    } finally {
      setLoadingQueue(false);
    }
  }

  async function loadBatchCandidates() {
    try {
      const payload = await apiRequest("/batch/candidates");
      setBatchCandidates(payload.groups || []);
    } catch (error) {
      setBatchCandidates([]);
      addToast(`Batch candidates failed: ${error.message}`, "error");
    }
  }

  async function loadApplications() {
    setLoadingApplications(true);
    try {
      const payload = await apiRequest("/applications");
      const rows = payload.applications || [];
      setApplications(rows);
      if (!selectedAppId && rows[0]?.application_id) {
        startTransition(() => setSelectedAppId(String(rows[0].application_id)));
      }
    } catch (error) {
      addToast(`Application load failed: ${error.message}`, "error");
    } finally {
      setLoadingApplications(false);
    }
  }

  async function loadInsights() {
    setLoadingInsights(true);
    try {
      const [overviewPayload, failuresPayload] = await Promise.all([
        apiRequest("/insights/overview"),
        apiRequest("/insights/failures?limit=5")
      ]);
      setInsightsOverview(overviewPayload.overview || null);
      setInsightsBreakdown(overviewPayload.status_breakdown || {});
      setInsightsFailures(failuresPayload.patterns || []);
    } catch (error) {
      setInsightsOverview(null);
      setInsightsBreakdown({});
      setInsightsFailures([]);
      addToast(`Insights load failed: ${error.message}`, "error");
    } finally {
      setLoadingInsights(false);
    }
  }

  async function loadApplicationDetail(appId) {
    if (!appId) {
      setApplicationDetail(null);
      return;
    }
    try {
      const payload = await apiRequest(`/applications/${appId}`);
      setApplicationDetail(payload.application || null);
    } catch (error) {
      addToast(`Application detail failed: ${error.message}`, "error");
    }
  }

  useEffect(() => {
    loadQueue();
    loadApplications();
    loadBatchCandidates();
    loadInsights();
  }, []);

  useEffect(() => {
    loadApplicationDetail(selectedAppId);
  }, [selectedAppId]);

  useEffect(() => {
    let socket;
    let retryTimer;
    let closed = false;

    function connect() {
      if (closed) {
        return;
      }
      socket = new WebSocket(`${WS_BASE}/ws/queue`);

      socket.onmessage = (event) => {
        const payload = parseWsMessage(event);
        if (!payload || payload.type !== "queue_snapshot") {
          return;
        }
        const nextQueue = payload.queue || [];
        setQueue(nextQueue);
        setSelectedListingId((current) => {
          if (current) {
            return current;
          }
          const first = nextQueue[0]?.listing_id;
          return first ? String(first) : "";
        });
      };

      socket.onerror = () => {
        if (socket && socket.readyState !== WebSocket.CLOSED) {
          socket.close();
        }
      };

      socket.onclose = () => {
        if (closed) {
          return;
        }
        retryTimer = setTimeout(connect, 1500);
      };
    }

    connect();
    return () => {
      closed = true;
      if (retryTimer) {
        clearTimeout(retryTimer);
      }
      if (socket && socket.readyState !== WebSocket.CLOSED) {
        socket.close();
      }
    };
  }, []);

  useEffect(() => {
    if (!selectedAppId) {
      return undefined;
    }

    let socket;
    let retryTimer;
    let closed = false;

    function connect() {
      if (closed) {
        return;
      }
      socket = new WebSocket(`${WS_BASE}/ws/application/${selectedAppId}`);

      socket.onmessage = (event) => {
        const payload = parseWsMessage(event);
        if (!payload || payload.type !== "application_snapshot") {
          return;
        }
        const nextStatus = String(payload.status || "UNKNOWN").toUpperCase();
        setApplications((current) =>
          current.map((item) =>
            String(item.application_id) === String(selectedAppId)
              ? { ...item, status: nextStatus, updated_at: payload.timestamp }
              : item
          )
        );
        setApplicationDetail((current) => {
          if (!current || String(current.application_id) !== String(selectedAppId)) {
            return current;
          }
          const prevState = current.state || {};
          return {
            ...current,
            status: nextStatus,
            status_history: payload.status_history || current.status_history || [],
            state: {
              ...prevState,
              human_escalations: payload.human_escalations || [],
              post_upload_corrections: payload.post_upload_corrections || [],
              failure_record: payload.failure_record || null,
              // Merge generated docs so ResumePreview updates live
              tailored_resume_final: payload.tailored_resume_final || prevState.tailored_resume_final,
              tailored_resume_tokenized: payload.tailored_resume_tokenized || prevState.tailored_resume_tokenized,
              cover_letter_final: payload.cover_letter_final || prevState.cover_letter_final,
              cover_letter_tokenized: payload.cover_letter_tokenized || prevState.cover_letter_tokenized,
              fields_filled: payload.fields_filled || prevState.fields_filled || [],
              screenshot_path: payload.screenshot_path || prevState.screenshot_path,
            }
          };
        });
      };

      socket.onerror = () => {
        if (socket && socket.readyState !== WebSocket.CLOSED) {
          socket.close();
        }
      };

      socket.onclose = () => {
        if (closed) {
          return;
        }
        retryTimer = setTimeout(connect, 1500);
      };
    }

    connect();
    return () => {
      closed = true;
      if (retryTimer) {
        clearTimeout(retryTimer);
      }
      if (socket && socket.readyState !== WebSocket.CLOSED) {
        socket.close();
      }
    };
  }, [selectedAppId]);

  async function startApplication(listingId, { mode = "shadow", silent = false } = {}) {
    if (!listingId) {
      return null;
    }
    setBusyListingId(listingId);
    try {
      const payload = await apiRequest(`/apply/${listingId}`, {
        method: "POST",
        body: JSON.stringify({
          submission_mode: mode,
          run_now: true,
          use_browser_automation: mode === "live"
        })
      });
      const appId = payload.application_id;
      if (appId) {
        startTransition(() => setSelectedAppId(String(appId)));
      }
      if (!silent) {
        addToast(`${mode.toUpperCase()} run started for ${listingId}.`, "info");
      }
      await loadApplications();
      await loadInsights();
      return payload;
    } catch (error) {
      if (!silent) {
        addToast(`Start failed: ${error.message}`, "error");
      }
      return null;
    } finally {
      setBusyListingId("");
    }
  }

  async function startShadowRun(listingId, { silent = false } = {}) {
    return startApplication(listingId, { mode: "shadow", silent });
  }

  async function markSkipped(listingId) {
    try {
      await apiRequest(`/jobs/${listingId}/skip`, { method: "POST" });
      addToast(`Listing ${listingId} skipped.`, "info");
      await loadQueue();
      await loadInsights();
    } catch (error) {
      addToast(`Skip failed: ${error.message}`, "error");
    }
  }

  async function flagListing(listingId) {
    try {
      await apiRequest(`/jobs/${listingId}/flag`, {
        method: "POST",
        body: JSON.stringify({ reason: "needs_manual_review" })
      });
      addToast(`Listing ${listingId} flagged.`, "info");
      await loadQueue();
      await loadInsights();
    } catch (error) {
      addToast(`Flag failed: ${error.message}`, "error");
    }
  }

  async function handleResolveEscalation(fieldId, value) {
    if (!selectedAppId) {
      addToast("Select an application first.", "error");
      return;
    }
    setResolvingFieldId(fieldId);
    try {
      await apiRequest(`/apply/${selectedAppId}/escalation/${fieldId}/resolve`, {
        method: "POST",
        body: JSON.stringify({ value, note: "Resolved from dashboard Human Assist Panel" })
      });
      addToast(`Escalation ${fieldId} resolved.`, "info");
      await loadApplicationDetail(selectedAppId);
    } catch (error) {
      addToast(`Resolve failed: ${error.message}`, "error");
    } finally {
      setResolvingFieldId("");
    }
  }

  async function handleBatchStart(groupKey, listingIds) {
    setRunningGroupKey(groupKey);
    try {
      const payload = await apiRequest("/batch/approve", {
        method: "POST",
        body: JSON.stringify({
          listing_ids: listingIds,
          submission_mode: "shadow",
          run_now: true
        })
      });
      addToast(`Batch complete: ${payload.started}/${payload.requested} started.`, "info");
      await loadApplications();
      await loadBatchCandidates();
      await loadInsights();
    } catch (error) {
      addToast(`Batch run failed: ${error.message}`, "error");
    } finally {
      setRunningGroupKey("");
    }
  }

  async function approveAndSubmitLive(appId) {
    if (!appId) {
      return;
    }
    setTrackerActionBusy(true);
    try {
      const payload = await apiRequest(`/apply/${appId}/approve`, {
        method: "POST",
        body: JSON.stringify({
          run_now: true,
          submission_mode: "live",
          use_browser_automation: true,
          headless: false
        })
      });
      addToast(`Approval processed. Workflow status: ${payload.workflow_status}.`, "info");
      await loadApplications();
      await loadApplicationDetail(appId);
      await loadInsights();
    } catch (error) {
      addToast(`Approve failed: ${error.message}`, "error");
    } finally {
      setTrackerActionBusy(false);
    }
  }

  async function resumeApplication(appId) {
    if (!appId) {
      return;
    }
    setTrackerActionBusy(true);
    try {
      const payload = await apiRequest(`/apply/${appId}/resume`, { method: "POST" });
      addToast(`Resume complete: ${payload.workflow_status}.`, "info");
      await loadApplications();
      await loadApplicationDetail(appId);
      await loadInsights();
    } catch (error) {
      addToast(`Resume failed: ${error.message}`, "error");
    } finally {
      setTrackerActionBusy(false);
    }
  }

  async function abortApplication(appId) {
    if (!appId) {
      return;
    }
    setTrackerActionBusy(true);
    try {
      const payload = await apiRequest(`/apply/${appId}/abort`, { method: "POST" });
      addToast(`Application aborted: ${payload.workflow_status}.`, "info");
      await loadApplications();
      await loadApplicationDetail(appId);
      await loadInsights();
    } catch (error) {
      addToast(`Abort failed: ${error.message}`, "error");
    } finally {
      setTrackerActionBusy(false);
    }
  }

  async function syncStatuses() {
    try {
      const payload = await apiRequest("/applications/status-sync", {
        method: "POST",
        body: JSON.stringify({
          since_days: 30,
          include_no_response: true,
          persist: true
        })
      });
      addToast(`Status sync scanned ${payload.scanned} apps and found ${payload.updates.length} updates.`, "info");
      await loadApplications();
      if (selectedAppId) {
        await loadApplicationDetail(selectedAppId);
      }
      await loadInsights();
      addToast("Status Tracker Synced with Email Inbox", "success");
    } catch (error) {
      addToast(`Status sync failed: ${error.message}`, "error");
    }
  }

  return (
    <main className="app-shell">
      <header className="hero">
        <h1>job_finder Decision Queue</h1>
        <p>
          Phase 3 dashboard for queue review, human assist, application tracking, and insights.
          This UI is aligned with the current API surface so the earlier Phase 3 route work stays
          consistent with frontend behavior.
        </p>
        <div className="chip-row">
          <span className="chip">Queue: {queue.length}</span>
          <span className="chip">Applications: {applications.length}</span>
          <span className="chip">Submitted: {statusCounts.get("SUBMITTED") || 0}</span>
          <span className="chip">Interviews: {statusCounts.get("INTERVIEW_SCHEDULED") || 0}</span>
          <span className="chip">Offers: {statusCounts.get("OFFER") || 0}</span>
        </div>
        <div className="toolbar">
          <input
            className="escalation-input"
            value={searchQuery}
            placeholder="Filter queue by company or role"
            onChange={(event) => setSearchQuery(event.target.value)}
          />
          <button className="btn" onClick={loadQueue} disabled={loadingQueue}>
            {loadingQueue ? "Refreshing queue..." : "Refresh Queue"}
          </button>
          <button className="btn" onClick={loadApplications} disabled={loadingApplications}>
            {loadingApplications ? "Refreshing applications..." : "Refresh Applications"}
          </button>
          <button className="btn" onClick={loadBatchCandidates}>
            Refresh Batch Groups
          </button>
          <button className="btn" onClick={loadInsights} disabled={loadingInsights}>
            {loadingInsights ? "Refreshing Insights..." : "Refresh Insights"}
          </button>
          <button className="btn" onClick={syncStatuses}>
            Sync Email Status
          </button>
        </div>
      </header>

      <section className="layout">
        <div className="stack">
          <DecisionQueue
            listings={filteredQueue}
            selectedListingId={selectedListingId}
            onSelectListing={setSelectedListingId}
            onStartApplication={startShadowRun}
            onSkip={markSkipped}
            onFlag={flagListing}
            busyListingId={busyListingId}
          />

          <BatchApproval
            listings={filteredQueue}
            candidates={batchCandidates}
            onRefreshCandidates={loadBatchCandidates}
            onBatchStart={handleBatchStart}
            runningGroupKey={runningGroupKey}
          />

          <ApplicationTracker
            applications={applications}
            selectedAppId={selectedAppId}
            onSelectApp={(appId) => startTransition(() => setSelectedAppId(appId))}
            onRefresh={loadApplications}
            loading={loadingApplications}
            onApproveLive={approveAndSubmitLive}
            onResume={resumeApplication}
            onAbort={abortApplication}
            actionBusy={trackerActionBusy}
          />
        </div>

        <div className="stack">
          <NewApplicationPanel onRefreshQueue={loadQueue} />
          <ResumePreview applicationDetail={applicationDetail} />
          <HumanAssistPanel
            applicationDetail={applicationDetail}
            onResolveEscalation={handleResolveEscalation}
            resolvingFieldId={resolvingFieldId}
          />
          <ConfidenceView applicationDetail={applicationDetail} />
          <InsightsPanel
            applications={applications}
            queueCount={queue.length}
            overview={insightsOverview}
            statusBreakdown={insightsBreakdown}
            failurePatterns={insightsFailures}
            loading={loadingInsights}
          />
        </div>
      </section>
    </main>
  );
}
