export function ChatSkeleton() {
  return (
    <div className="chat-skeleton" aria-hidden="true">
      {Array.from({ length: 4 }).map((_, index) => (
        <div className="chat-skeleton-row" data-user={index % 3 === 1} key={index}>
          <div className="skeleton-avatar" />
          <div className="skeleton-message">
            <span />
            <span />
            <span />
          </div>
        </div>
      ))}
    </div>
  );
}

export function SidebarSkeleton() {
  return (
    <div className="sidebar-skeleton" aria-hidden="true">
      <div className="skeleton-search" />
      {Array.from({ length: 8 }).map((_, index) => (
        <div className="skeleton-session" key={index}>
          <span />
          <span />
        </div>
      ))}
    </div>
  );
}
