"""不需要 Valhalla 的 Router 純函式測試：回應合併與吸附結果驗證。"""

import pytest

from engine.routing.polyline import encode
from engine.routing.router import RouteResult, RoutingError, _ensure_matched, _route_result

A, B, C = (121.5, 25.05), (121.502, 25.05), (121.504, 25.05)
FAR = (121.52, 25.05)  # 離 C 約 1.8 km


def _response(*legs: list[tuple[float, float]]) -> dict:
    return {
        "trip": {
            "legs": [{"shape": encode(leg)} for leg in legs],
            "summary": {"length": 0.5},
        }
    }


def test_route_result_merges_continuous_legs_without_duplicating_joint() -> None:
    result = _route_result(_response([A, B], [B, C]))
    assert result.points == pytest.approx([A, B, C])


def test_route_result_rejects_gap_between_legs() -> None:
    with pytest.raises(RoutingError, match="不連續"):
        _route_result(_response([A, B], [FAR, C]))


def test_ensure_matched_accepts_good_match() -> None:
    _ensure_matched(RouteResult([A, B, C], 0.402), [A, B, C], search_radius_m=50.0)


def test_ensure_matched_rejects_single_distinct_point() -> None:
    with pytest.raises(RoutingError, match="不同的點"):
        _ensure_matched(RouteResult([C, C], 0.0), [C, B, A], search_radius_m=50.0)


def test_ensure_matched_rejects_truncated_match() -> None:
    # 輸入約 0.4 km，只匹配到一半
    with pytest.raises(RoutingError, match="覆蓋"):
        _ensure_matched(RouteResult([A, B], 0.2), [A, B, C], search_radius_m=50.0)


def test_ensure_matched_rejects_far_endpoints() -> None:
    # 長度夠，但起點離輸入起點太遠
    with pytest.raises(RoutingError, match="起點"):
        _ensure_matched(RouteResult([FAR, C], 0.402), [A, B, C], search_radius_m=50.0)
    with pytest.raises(RoutingError, match="終點"):
        _ensure_matched(RouteResult([A, FAR], 0.402), [A, B, C], search_radius_m=50.0)
