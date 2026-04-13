import { useEffect, useMemo, useState } from "react";

function normalizePriority(value) {
  const normalized = String(value || "IMPORTANT").toUpperCase();
  if (normalized === "BLOCKING" || normalized === "IMPORTANT" || normalized === "OPTIONAL") {
    return normalized;
  }
  return "IMPORTANT";
}

function toEscalationGroups(escalations, corrections) {
  const grouped = {
    BLOCKING: [],
    IMPORTANT: [],
    OPTIONAL: []
  };
  for (const item of escalations || []) {
    const priority = normalizePriority(item.priority);
    grouped[priority].push({
      key: `${item.field_id || "na"}-${item.type || "escalation"}`,
      fieldId: String(item.field_id || ""),
      label: item.label || item.field_id || "Escalation",
      message: item.message || "Needs review",
      source: item.type || "workflow",
      type: "escalation"
    });
  }
  for (const correction of corrections || []) {
    const severity = String(correction.severity || "moderate").toLowerCase();
    const priority = severity === "major" ? "BLOCKING" : "IMPORTANT";
    grouped[priority].push({
      key: `${correction.field_id || "na"}-correction`,
      fieldId: String(correction.field_id || ""),
      label: correction.field_id || "Correction",
      message: correction.reason || "Post-upload correction suggested",
      source: "post_upload_validator",
      type: "correction"
    });
  }
  return grouped;
}

export default function HumanAssistPanel({
  applicationDetail,
  onResolveEscalation,
  resolvingFieldId
}) {
  const state = applicationDetail?.state ?? {};
  const escalations = state.human_escalations ?? [];
  const corrections = state.post_upload_corrections ?? [];
  const [fieldValues, setFieldValues] = useState({});
  useEffect(() => {
    setFieldValues({});
  }, [applicationDetail?.application_id]);

  const groups = useMemo(
    () => toEscalationGroups(escalations, corrections),
    [escalations, corrections]
  );
  const totalItems = groups.BLOCKING.length + groups.IMPORTANT.length + groups.OPTIONAL.length;

  function updateFieldValue(fieldId, value) {
    setFieldValues((previous) => ({
      ...previous,
      [fieldId]: value
    }));
  }

  return (
    <section className="card">
      <h2>Human Assist Panel</h2>
      <p className="card-subtitle">
        Resolve blockers and high-importance items before continuing.
      </p>
      {totalItems === 0 ? (
        <div className="message">No escalations pending for the selected application.</div>
      ) : null}
      {["BLOCKING", "IMPORTANT", "OPTIONAL"].map((priority) => (
        <div key={priority} className="list">
          <p className="list-title">
            {priority} ({groups[priority].length})
          </p>
          {groups[priority].map((item) => {
            const currentValue = fieldValues[item.fieldId] ?? "";
            const canResolve = item.fieldId.length > 0 && currentValue.trim().length > 0;
            return (
              <article key={item.key} className="list-item">
                <div className="list-meta">
                  {item.label} · {item.source}
                </div>
                <p className="muted" style={{ margin: "6px 0 0", fontSize: 12 }}>
                  {item.message}
                </p>
                {item.fieldId ? (
                  <>
                    <input
                      className="escalation-input"
                      value={currentValue}
                      placeholder="Provide resolved value"
                      onChange={(event) => updateFieldValue(item.fieldId, event.target.value)}
                    />
                    <div className="toolbar">
                      <button
                        className="btn btn-primary"
                        disabled={!canResolve || resolvingFieldId === item.fieldId}
                        onClick={() => onResolveEscalation(item.fieldId, currentValue)}
                      >
                        {resolvingFieldId === item.fieldId ? "Resolving..." : "Resolve"}
                      </button>
                    </div>
                  </>
                ) : null}
              </article>
            );
          })}
        </div>
      ))}
    </section>
  );
}
