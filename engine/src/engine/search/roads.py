"""可騎道路的空間索引（建圖流程產生，見 engine.data.rideable）。暫定介面，實作待補。"""

from pathlib import Path

import numpy as np

RIDEABLE_META = "rideable.json"
RIDEABLE_SEGMENTS = "rideable_segments.npy"
RIDEABLE_OFFSETS = "rideable_offsets.npy"


class RoadDataMissing(Exception):
    pass


def has_road_data(build_dir: Path) -> bool:
    return all((build_dir / name).is_file() for name in (RIDEABLE_META, RIDEABLE_SEGMENTS, RIDEABLE_OFFSETS))


class RoadIndex:
    @classmethod
    def load(cls, build_dir: Path) -> "RoadIndex":
        raise RoadDataMissing(str(build_dir))

    def segments(self, min_lon: float, min_lat: float, max_lon: float, max_lat: float) -> np.ndarray:
        raise NotImplementedError
