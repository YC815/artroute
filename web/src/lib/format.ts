/** 公尺 → 「1.23 km」；不到 1 km 顯示「850 m」。 */
export function formatDistance(meters: number): string {
  if (meters < 1000) return `${Math.round(meters)} m`;
  return `${(meters / 1000).toFixed(2)} km`;
}

/** 路網版本（build id，例如 20261010T145928Z）→「2026-10-10」；格式不符就原樣回傳。 */
export function formatDataVersion(buildId: string): string {
  const match = /^(\d{4})(\d{2})(\d{2})T/.exec(buildId);
  return match ? `${match[1]}-${match[2]}-${match[3]}` : buildId;
}

/** 毫秒 → 「18.3 秒」（一位小數）。 */
export function formatSeconds(ms: number): string {
  return `${(ms / 1000).toFixed(1)} 秒`;
}

/** 整數加千分位：270000 →「270,000」。 */
export function formatCount(n: number): string {
  return Math.round(n).toLocaleString("en-US");
}
