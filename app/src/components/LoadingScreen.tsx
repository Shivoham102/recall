export function LoadingScreen() {
  return (
    <div className="ls-root" role="status" aria-live="polite" aria-label="Loading Recall">
      <span className="sr-only">Loading…</span>
      <div className="ls-wordmark" aria-hidden="true">
        {"RECALL".split("").map((ch, i) => (
          <span
            key={i}
            className="ls-letter"
            style={{ animationDelay: `${i * 50}ms, ${i * 50 + 650}ms` }}
          >
            {ch}
          </span>
        ))}
      </div>
      <div className="ls-line" aria-hidden="true" />
      <div className="ls-tagline" aria-hidden="true">initializing memory...</div>
    </div>
  );
}
