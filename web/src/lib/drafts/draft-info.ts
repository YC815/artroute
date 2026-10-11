/** 草稿的路網版本和引擎目前的不同（引擎版本未知——連不上——時不算舊，讓使用者照常看、下載）。 */
export function isOutdatedRoute(routeVersion: string, engineVersion: string | null): boolean {
  return engineVersion !== null && routeVersion !== engineVersion;
}

/** 「這是 10 月 10 日路網上的路線」；build id 的格式不符時不帶日期。 */
export function outdatedLabel(buildId: string): string {
  const match = /^\d{4}(\d{2})(\d{2})T/.exec(buildId);
  return match ? `這是 ${Number(match[1])} 月 ${Number(match[2])} 日路網上的路線` : "這是舊路網上的路線";
}

/** 最後修改：「10/11 20:05」（本地時間）。 */
export function formatModified(ms: number): string {
  const d = new Date(ms);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getMonth() + 1}/${d.getDate()} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}
