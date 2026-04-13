function normalizeFields(applicationDetail) {
  const state = applicationDetail?.state ?? {};
  const fromFilled = (state.fields_filled ?? []).map((field) => ({
    id: String(field.field_id || ""),
    label: field.label || field.field_id || "Unknown field",
    confidence: Number(field.confidence ?? 0),
    strategy: field.selector_strategy || "unknown",
    value: field.value
  }));

  if (fromFilled.length > 0) {
    return fromFilled;
  }

  return (state.fill_plan?.fields ?? [])
    .filter((field) => typeof field === "object" && field !== null)
    .map((field) => ({
      id: String(field.field_id || ""),
      label: field.label || field.field_id || "Unknown field",
      confidence: Number(field.confidence ?? 0),
      strategy: field.selector_strategy || "unknown",
      value: field.value
    }));
}

function confidenceClass(value) {
  if (value >= 0.8) {
    return "high";
  }
  if (value >= 0.5) {
    return "mid";
  }
  return "low";
}

export default function ConfidenceView({ applicationDetail }) {
  const rows = normalizeFields(applicationDetail);

  return (
    <section className="card">
      <h2>Confidence View</h2>
      <p className="card-subtitle">
        Hybrid confidence by field, based on selector and template evidence.
      </p>
      {rows.length === 0 ? (
        <div className="message">No field confidence data yet for this application.</div>
      ) : null}
      {rows.map((row) => {
        const clamped = Number.isFinite(row.confidence)
          ? Math.max(0, Math.min(1, row.confidence))
          : 0;
        return (
          <div key={row.id || row.label} className="confidence-row">
            <div className="confidence-head">
              <span>{row.label}</span>
              <span className="mono">
                {(clamped * 100).toFixed(0)}% · {row.strategy}
              </span>
            </div>
            <div className="confidence-track">
              <div
                className={`confidence-fill ${confidenceClass(clamped)}`}
                style={{ width: `${clamped * 100}%` }}
              />
            </div>
          </div>
        );
      })}
    </section>
  );
}
