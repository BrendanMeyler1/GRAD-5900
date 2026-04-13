function formatPercent(value) {
  const numeric = Number(value);
  if (Number.isNaN(numeric)) {
    return "n/a";
  }
  const normalized = numeric > 0 && numeric <= 1 ? numeric * 100 : numeric;
  return `${Math.round(normalized)}%`;
}

function parseAliveScore(listing) {
  const composite = listing?.alive_score?.composite;
  const maybe = Number(composite);
  if (!Number.isNaN(maybe) && maybe >= 0 && maybe <= 1) {
    return `${Math.round(maybe * 100)}%`;
  }
  return "n/a";
}

export default function DecisionQueue({
  listings,
  selectedListingId,
  onSelectListing,
  onStartApplication,
  onSkip,
  onFlag,
  busyListingId
}) {
  return (
    <section className="card">
      <h2>Decision Queue</h2>
      <p className="card-subtitle">
        Prioritized roles waiting for review and approval.
      </p>
      {listings.length === 0 ? (
        <div className="message">No queue items available. Run a jobs scan to populate this view.</div>
      ) : null}
      <div className="list">
        {listings.map((listing) => {
          const listingId = String(listing.listing_id ?? "");
          const company =
            typeof listing.company === "object" ? listing.company?.name : listing.company;
          const roleTitle =
            typeof listing.role === "object" ? listing.role?.title : listing.role_title;
          const fitScore =
            listing?.fit_score?.overall_score ??
            listing?.fit_score ??
            listing?.priority_score ??
            0;
          const alive = parseAliveScore(listing);
          return (
            <article
              key={listingId}
              className={`list-item${selectedListingId === listingId ? " selected" : ""}`}
            >
              <p className="list-title">{roleTitle || "Untitled role"}</p>
              <div className="list-meta">
                <span>{company || "Unknown company"}</span>
                <span> · Fit {formatPercent(fitScore)}</span>
                <span> · Alive {alive}</span>
              </div>
              <div className="toolbar">
                <button className="btn" onClick={() => onSelectListing(listingId)}>
                  Review
                </button>
                <button
                  className="btn btn-primary"
                  disabled={busyListingId === listingId}
                  onClick={() => onStartApplication(listingId)}
                >
                  {busyListingId === listingId ? "Starting..." : "Start Shadow Run"}
                </button>
                <button className="btn btn-warm" onClick={() => onFlag(listingId)}>
                  Flag
                </button>
                <button className="btn btn-danger" onClick={() => onSkip(listingId)}>
                  Skip
                </button>
              </div>
            </article>
          );
        })}
      </div>
    </section>
  );
}
