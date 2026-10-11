import { describe, expect, it } from "vitest";
import { haversineM } from "@/lib/geo/haversine";
import { fromLocal } from "@/lib/geo/local-plane";
import { metersPerPixel, pickRoutePosition } from "@/lib/map/route-pick";
import type { LonLat } from "@/lib/shape/types";

const O: LonLat = [121.5, 25.03];
/** 相對 O 往東、往北幾公尺的點。 */
const at = (east: number, north: number): LonLat => fromLocal(O, east, north);

const A = at(0, 0);
const B = at(100, 0);
const C = at(100, 100);
const D = at(200, 100);

describe("pickRoutePosition", () => {
  it("點在邊的旁邊：投影到邊上，offset 是沿折線的 haversine 公尺數", () => {
    const hit = pickRoutePosition([{ points: [A, B, C] }], at(40, 5), 12)!;
    expect(hit.segment).toBe(0);
    expect(hit.distanceM).toBeCloseTo(5, 3);
    expect(hit.offsetM).toBeCloseTo(0.4 * haversineM(A, B), 6);
    expect(hit.point[0]).toBeCloseTo(A[0] + 0.4 * (B[0] - A[0]), 12);
    expect(hit.point[1]).toBeCloseTo(A[1], 12);
  });

  it("第二條邊上的 offset：前面的邊全長＋t × 該邊長（與引擎 edit/position.py 同一把尺）", () => {
    const hit = pickRoutePosition([{ points: [A, B, C] }], at(103, 25), 12)!;
    expect(hit.offsetM).toBeCloseTo(haversineM(A, B) + 0.25 * haversineM(B, C), 6);
    // 引擎 resolve：在該邊上以 t = (offset − 前面的邊長) / 該邊長 線性內插經緯度，要回到同一點
    const t = (hit.offsetM - haversineM(A, B)) / haversineM(B, C);
    expect(hit.point[0]).toBeCloseTo(B[0] + t * (C[0] - B[0]), 12);
    expect(hit.point[1]).toBeCloseTo(B[1] + t * (C[1] - B[1]), 12);
  });

  it("超出邊的端點時夾到頂點", () => {
    const hit = pickRoutePosition([{ points: [A, B] }], at(105, -3), 12)!;
    expect(hit.point).toEqual(B);
    expect(hit.offsetM).toBe(haversineM(A, B));
    expect(hit.distanceM).toBeCloseTo(Math.hypot(5, 3), 3);
  });

  it("距離 > toleranceM 回 null；剛好等於還算點到", () => {
    expect(pickRoutePosition([{ points: [A, B] }], at(50, 12.5), 12)).toBeNull();
    const edge = pickRoutePosition([{ points: [A, B] }], at(50, 10), 10.001);
    expect(edge?.distanceM).toBeCloseTo(10, 3);
  });

  it("取所有段裡最近的", () => {
    const segments = [{ points: [A, B] }, { points: [B, C] }, { points: [C, D] }];
    const hit = pickRoutePosition(segments, at(150, 104), 12)!;
    expect(hit.segment).toBe(2);
    expect(hit.offsetM).toBeCloseTo(0.5 * haversineM(C, D), 6);
  });

  it("同距離時取較前面的段（來回騎的路兩趟重疊）", () => {
    const hit = pickRoutePosition([{ points: [A, B] }, { points: [B, A] }], at(30, 2), 12)!;
    expect(hit.segment).toBe(0);
    expect(hit.offsetM).toBeCloseTo(0.3 * haversineM(A, B), 6);
  });

  it("同一段裡兩趟重疊時取較前面的邊（offset 較小）", () => {
    const hit = pickRoutePosition([{ points: [A, B, A] }], at(30, 2), 12)!;
    expect(hit.offsetM).toBeCloseTo(0.3 * haversineM(A, B), 6);
  });

  it("段落交界上的點算前一段的段尾", () => {
    const hit = pickRoutePosition([{ points: [A, B] }, { points: [B, C] }], at(100, 0), 12)!;
    expect(hit.segment).toBe(0);
    expect(hit.offsetM).toBe(haversineM(A, B));
  });

  it("零長度的邊與只有一個點的段不會壞；沒有段時回 null", () => {
    const hit = pickRoutePosition([{ points: [A] }, { points: [B, B, C] }], at(98, 50), 12)!;
    expect(hit.segment).toBe(1);
    expect(hit.offsetM).toBeCloseTo(0.5 * haversineM(B, C), 6);
    expect(pickRoutePosition([{ points: [A] }], A, 12)).toEqual({ segment: 0, offsetM: 0, point: A, distanceM: 0 });
    expect(pickRoutePosition([], A, 12)).toBeNull();
  });
});

describe("metersPerPixel", () => {
  // MapLibre：512 px 的 tile、地球半徑 6371008.8 m（maplibre-gl src/geo/lng_lat.ts、transform_helper.ts）
  const equatorZoom0 = (2 * Math.PI * 6_371_008.8) / 512;

  it("赤道 zoom 0 是一圈 / 512 px；每加一級減半", () => {
    expect(metersPerPixel(0, 0)).toBeCloseTo(equatorZoom0, 6);
    expect(metersPerPixel(0, 1)).toBeCloseTo(equatorZoom0 / 2, 6);
    expect(metersPerPixel(0, 16.5)).toBeCloseTo(equatorZoom0 / 2 ** 16.5, 9);
  });

  it("乘上 cos(緯度)", () => {
    expect(metersPerPixel(60, 10)).toBeCloseTo(equatorZoom0 / 2 ** 10 / 2, 6);
    // 台北 zoom 16：12 px 約 13 m
    expect(12 * metersPerPixel(25.03, 16)).toBeCloseTo(12.97, 2);
  });
});
