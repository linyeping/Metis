export function parseUser(raw) {
  return {
    id: String(raw.id),
    name: raw.name || 'anonymous',
  };
}

export function displayUser(raw) {
  const user = parseUser(raw);
  return `${user.id}:${user.name}`;
}
