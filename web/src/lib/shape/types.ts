/** 二維點。正規化座標是 x 向右、y 向上；來源座標（Hershey、圖片像素）是 y 向下。 */
export type Point = [number, number];

/** 經緯度，順序是 [經度, 緯度]，跟 GeoJSON、引擎 API 一致。 */
export type LonLat = [number, number];

/** 一筆。closed 為 true 時，最後一點「不」重複起點（引擎會自己補上）。 */
export type Stroke = { points: Point[]; closed: boolean };

export type Shape = { strokes: Stroke[]; kind: "text" | "image" };
