export async function fetchPaths(apiBase, fetchImpl = globalThis.fetch) {
  try {
    const res = await fetchImpl(`${apiBase}/v3/paths/list`);
    if (!res.ok) return { items: [] };
    const data = await res.json();
    return { items: Array.isArray(data.items) ? data.items : [] };
  } catch {
    return { items: [] };
  }
}
