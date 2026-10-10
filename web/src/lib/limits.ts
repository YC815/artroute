// /match 契約的上限（spec §4.7、§5）。網站端的檢查與 zod schema 都從這裡取值。
export const MAX_STROKES = 40;
export const MIN_STROKE_POINTS = 2;
export const MAX_STROKE_POINTS = 2000;
export const MIN_SEARCH_RADIUS_M = 30;
export const MAX_SEARCH_RADIUS_M = 150;
export const MAX_IMAGE_BYTES = 5 * 1024 * 1024;
