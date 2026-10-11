# 第二輪標註（P2.5 Q3，2026-10-11）

2026-10-11 在 m1 上以 `python -m engine.bench.likeness_samples samples` 產生（主 spec §8 第 45、53 列），build `20261011T095232Z`，引擎程式是 PR #4 的 `ba0baa1`。格式與上一層的 `README.md` 相同，變體換成：

| variant | 怎麼產生 |
|---|---|
| Q2 | 現行預設（fast，轉彎懲罰 500、剪小刺與小圈） |
| P2 | 改前（不帶轉彎懲罰、不剪） |
| F | `quality: "fine"`（描形規劃器） |

每個搜尋 bench 案例取前 3 名擺放（`rank` 0–2）。配對只留 v3 rank 差距 ≥ 3 的 Q2 vs P2、Q2 第 1 名 vs 第 3 名，以及全部的 Q2 vs F；安全斷言沒過的路線不進配對（`heart@taichung_west` 的 Q2、P2 第 2 名）。Q2 vs P2 沒有一組達到門檻。

`labels.json`：41 組全標，有表態 10 組（Q2 vs F 35 組裡選 Q2 5、選 F 3、差不多 27；不同擺放 6 組裡選第 1 名 1、第 3 名 1、差不多 4）。判讀見 `docs/p2-5-likeness-findings.md` §4。

路線幾何來自 OpenStreetMap 資料，© OpenStreetMap contributors，以 ODbL 授權（https://www.openstreetmap.org/copyright），經 Valhalla 產生。
