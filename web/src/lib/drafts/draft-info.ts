/** build id 是 UTC 時間戳（20261011T095232Z）：同樣格式的兩個 id，字串順序就是時間順序。 */
const BUILD_ID = /^\d{8}T\d{6}Z$/;

/** a 比 b 新（兩個都是時間戳格式才比；格式不符不當成較新）。 */
function isNewer(a: string, b: string): boolean {
  return BUILD_ID.test(a) && BUILD_ID.test(b) && a > b;
}

/**
 * 路線的路網比引擎目前的舊（引擎版本未知——連不上——時不算舊，讓使用者照常看、下載）。
 * 比引擎目前已知的版本新的路線不算舊：頁面開著時引擎每週重建，/status 是開頁時讀的，新的吸附結果可能已經是新版。
 * 格式不是時間戳的 id（沒有順序可比）只看是否相同。
 */
export function isOutdatedRoute(routeVersion: string, engineVersion: string | null): boolean {
  if (engineVersion === null || routeVersion === engineVersion) return false;
  return BUILD_ID.test(routeVersion) && BUILD_ID.test(engineVersion) ? isNewer(engineVersion, routeVersion) : true;
}

/** 引擎回應裡看到的版本比 /status 已知的新：要重新讀 /status（已知版本未知時不讀，引擎本來就連不上）。 */
export function isNewerBuild(seen: string | null, known: string | null): boolean {
  return seen !== null && known !== null && isNewer(seen, known);
}

/** 這些版本裡最新的（沒有、或格式不符的略過）；都沒有是 null。 */
export function newestBuild(versions: readonly (string | null | undefined)[]): string | null {
  return versions.reduce<string | null>((newest, v) => (v && BUILD_ID.test(v) && (newest === null || v > newest) ? v : newest), null);
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
