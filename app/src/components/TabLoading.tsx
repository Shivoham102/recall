export function TabLoading() {
  return (
    <div className="tab-loading" role="status" aria-live="polite">
      <div className="tl-dots" aria-hidden="true">
        <span className="tl-dot" />
        <span className="tl-dot" />
        <span className="tl-dot" />
      </div>
      <span className="sr-only">Loading…</span>
    </div>
  );
}
