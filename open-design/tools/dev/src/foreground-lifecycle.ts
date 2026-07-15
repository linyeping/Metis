export function foregroundTargetsAreIdle(statuses: Array<{ state?: string | null } | null>): boolean {
  return statuses.length > 0 && statuses.every(
    (status) => status == null || ["idle", "stopped"].includes(String(status.state || "idle")),
  );
}
