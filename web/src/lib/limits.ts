// /match、/search 契約的上限（spec §4.6、§4.7、§5）。網站端的檢查與 zod schema 都從這裡取值。
export const MAX_STROKES = 40;
export const MIN_STROKE_POINTS = 2;
export const MAX_STROKE_POINTS = 2000;
export const MIN_SEARCH_RADIUS_M = 30;
export const MAX_SEARCH_RADIUS_M = 150;
export const MAX_IMAGE_BYTES = 5 * 1024 * 1024;

// 自動搜尋（/search）與 /match 的 start_circle：圓的半徑、路線本身的公里數、旋轉範圍
export const MIN_SEARCH_AREA_RADIUS_M = 300;
export const MAX_SEARCH_AREA_RADIUS_M = 10_000;
export const MIN_SEARCH_KM = 1;
export const MAX_SEARCH_KM = 300;
export const MAX_SEARCH_ROTATION_DEG = 180;
/** /search 的筆畫是正規化座標，每個座標都在 [-1, 1] 內（normalizeStrokes 實際只到 ±0.5）。 */
export const MAX_NORM_COORD = 1;
/** /search 的結果最多幾個候選。 */
export const MAX_SEARCH_CANDIDATES = 5;

// /edit、/verify：整條路線原封不動送回引擎（segments 與簽章的上限，引擎的 EditRequest／VerifyRequest／SegmentIn）。
export const MAX_ROUTE_SEGMENTS = 2000;
export const MAX_SEGMENT_POINTS = 20_000;
export const MAX_DATA_VERSION_CHARS = 64;
export const MAX_ROUTE_SIG_CHARS = 128;
