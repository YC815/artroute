"""自動搜尋（spec §4.6）。暫定介面，實作待補。"""

from collections.abc import Callable, Sequence

from engine.matching.deadline import Deadline
from engine.matching.types import MatchRouter
from engine.search.roads import RoadIndex
from engine.search.types import SearchEventOut, SearchOutcome, SearchParams


def run_search(
    params: SearchParams,
    routers: Sequence[MatchRouter],
    roads: RoadIndex,
    deadline: Deadline,
    emit: Callable[[SearchEventOut], None],
) -> SearchOutcome:
    raise NotImplementedError
