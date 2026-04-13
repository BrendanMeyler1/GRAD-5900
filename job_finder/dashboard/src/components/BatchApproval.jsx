function groupListings(listings) {
  const groups = new Map();
  for (const listing of listings) {
    const company =
      typeof listing.company === "object" ? listing.company?.name : listing.company;
    const roleTitle =
      typeof listing.role === "object" ? listing.role?.title : listing.role_title;
    const key = `${company || "Unknown company"}::${roleTitle || "Untitled role"}`;
    if (!groups.has(key)) {
      groups.set(key, {
        key,
        company: company || "Unknown company",
        roleTitle: roleTitle || "Untitled role",
        listingIds: []
      });
    }
    groups.get(key).listingIds.push(String(listing.listing_id || ""));
  }
  return Array.from(groups.values()).sort((a, b) => b.listingIds.length - a.listingIds.length);
}

export default function BatchApproval({
  listings,
  candidates,
  onRefreshCandidates,
  onBatchStart,
  runningGroupKey
}) {
  const groups = (candidates && candidates.length > 0
    ? candidates.map((group) => ({
      key: group.group_key,
      company: group.company,
      roleTitle: group.role_title,
      listingIds: group.listing_ids || []
    }))
    : groupListings(listings).filter((group) => group.listingIds.length > 1));

  return (
    <section className="card">
      <h2>Batch Mode</h2>
      <p className="card-subtitle">
        Group similar listings and launch shadow runs together.
      </p>
      <div className="toolbar">
        <button className="btn" onClick={onRefreshCandidates}>
          Refresh Batch Candidates
        </button>
      </div>
      {groups.length === 0 ? (
        <div className="message">
          No batch groups yet. Groups appear when at least two listings share company and role title.
        </div>
      ) : null}
      <div className="list">
        {groups.map((group) => (
          <article key={group.key} className="list-item">
            <p className="list-title">{group.roleTitle}</p>
            <div className="list-meta">
              {group.company} · {group.listingIds.length} listings
            </div>
            <div className="toolbar">
              <button
                className="btn btn-primary"
                onClick={() => onBatchStart(group.key, group.listingIds)}
                disabled={runningGroupKey === group.key}
              >
                {runningGroupKey === group.key ? "Running..." : "Start Batch Shadow"}
              </button>
            </div>
          </article>
        ))}
      </div>
    </section>
  );
}
