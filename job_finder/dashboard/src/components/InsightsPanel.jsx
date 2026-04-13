function toRate(numerator, denominator) {
  if (!denominator) {
    return "0%";
  }
  return `${Math.round((numerator / denominator) * 100)}%`;
}

function fallbackOverview(applications, queueCount) {
  const totalApps = applications.length;
  const submittedLike = applications.filter((app) =>
    ["SUBMITTED", "RECEIVED", "INTERVIEW_SCHEDULED", "OFFER", "REJECTED"].includes(
      String(app.status || "").toUpperCase()
    )
  ).length;
  const interviews = applications.filter(
    (app) => String(app.status || "").toUpperCase() === "INTERVIEW_SCHEDULED"
  ).length;
  const offers = applications.filter(
    (app) => String(app.status || "").toUpperCase() === "OFFER"
  ).length;
  const rejected = applications.filter(
    (app) => String(app.status || "").toUpperCase() === "REJECTED"
  ).length;
  const failed = applications.filter(
    (app) => String(app.status || "").toUpperCase() === "FAILED"
  ).length;

  return {
    queue_count: queueCount,
    application_count: totalApps,
    submitted_like_count: submittedLike,
    interview_count: interviews,
    offer_count: offers,
    rejected_count: rejected,
    failed_count: failed
  };
}

export default function InsightsPanel({
  applications,
  queueCount,
  overview,
  statusBreakdown,
  failurePatterns,
  loading
}) {
  const effective = overview || fallbackOverview(applications, queueCount);
  const submittedLike = Number(effective.submitted_like_count || 0);
  const totalApps = Number(effective.application_count || 0);
  const interviews = Number(effective.interview_count || 0);
  const offers = Number(effective.offer_count || 0);
  const rejected = Number(effective.rejected_count || 0);
  const failed = Number(effective.failed_count || 0);
  const queue = Number(effective.queue_count || queueCount || 0);
  const breakdownEntries = Object.entries(statusBreakdown || {});

  return (
    <section className="card">
      <h2>Insights Snapshot</h2>
      <p className="card-subtitle">
        Funnel metrics and failure patterns from the Phase 3 insights APIs.
      </p>
      {loading ? <div className="message">Refreshing insights...</div> : null}
      <div className="kpi-grid">
        <div className="kpi">
          <div className="kpi-label">Queue Size</div>
          <div className="kpi-value">{queue}</div>
        </div>
        <div className="kpi">
          <div className="kpi-label">Applications</div>
          <div className="kpi-value">{totalApps}</div>
        </div>
        <div className="kpi">
          <div className="kpi-label">Submit Rate</div>
          <div className="kpi-value">{toRate(submittedLike, totalApps)}</div>
        </div>
        <div className="kpi">
          <div className="kpi-label">Interview Rate</div>
          <div className="kpi-value">{toRate(interviews, submittedLike)}</div>
        </div>
        <div className="kpi">
          <div className="kpi-label">Offer Rate</div>
          <div className="kpi-value">{toRate(offers, submittedLike)}</div>
        </div>
        <div className="kpi">
          <div className="kpi-label">Rejections</div>
          <div className="kpi-value">{rejected}</div>
        </div>
        <div className="kpi">
          <div className="kpi-label">Failed Runs</div>
          <div className="kpi-value">{failed}</div>
        </div>
      </div>
      <div className="grid-2" style={{ marginTop: 16 }}>
        <div>
          <p className="list-title">Status Breakdown</p>
          {breakdownEntries.length === 0 ? (
            <div className="message">No status breakdown available yet.</div>
          ) : (
            <div className="list">
              {breakdownEntries.map(([status, count]) => (
                <div key={status} className="list-item">
                  <div className="list-meta">
                    {status} | {count}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
        <div>
          <p className="list-title">Top Failure Patterns</p>
          {!failurePatterns || failurePatterns.length === 0 ? (
            <div className="message">No failure pattern data yet.</div>
          ) : (
            <div className="list">
              {failurePatterns.map((pattern, index) => (
                <div key={`${pattern.error_type || "failure"}-${index}`} className="list-item">
                  <div className="list-meta">
                    {pattern.error_type || "UnknownError"} | {pattern.failure_step || "unknown_step"} |
                    {` ${pattern.count || 0}`}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </section>
  );
}
