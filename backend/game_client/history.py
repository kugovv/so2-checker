"""Conservative history pagination: no inferred game modes or statistic keys."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Awaitable, Callable

from .client import ResolvedPlayer
from .errors import PaginationError

MAX_HISTORY_BYTES = 8 * 1024 * 1024


@dataclass(frozen=True)
class HistoryBatch:
    matches: tuple = field(repr=False)
    requested: int
    pages: int
    raw_records: int
    excluded: dict[str, int]
    stop_reason: str
    source_records_beyond_20: bool
    overlap_found: bool | None
    possible_gap: bool
    mode_mapping_verified: bool = False
    complete_account_history: bool = False

    def summary(self) -> dict:
        return {"requested_matches": self.requested, "returned_finished_matches": len(self.matches),
                "pages_received": self.pages, "raw_records_received": self.raw_records,
                "excluded": self.excluded, "stop_reason": self.stop_reason,
                "records_beyond_20_observed": self.source_records_beyond_20,
                "overlap_found": self.overlap_found, "possible_history_gap": self.possible_gap,
                "mode_mapping_verified": False, "complete_account_history": False}


def _belongs_to_player(match, player: ResolvedPlayer) -> bool:
    participants = list(match.players)
    for clan in match.clans:
        participants.extend(clan.players)
    # A missing public UID is allowed only when a matching internal ID is present.
    # Contradictory identifiers are never silently accepted.
    matched = False
    for participant in participants:
        if participant.gpid == player.gpid or participant.uid == player.uid:
            if (participant.uid and participant.uid != player.uid) or (participant.gpid and participant.gpid != player.gpid):
                return False
            matched = True
    return matched


async def collect_history(
    fetch_page: Callable[..., Awaitable], player: ResolvedPlayer, *, max_matches: int = 50,
    page_size: int = 20, max_pages: int = 10, previous_match_ids: frozenset[str] = frozenset(),
) -> HistoryBatch:
    """Fetch up to 50 validated finished records without calling them mode-specific.

    Offset advances by actual raw rows, not accepted rows. Duplicate/annulled
    records cannot inflate the sample. A short page is not proof of a server cap:
    request the next offset until an empty/repeated page or the configured bound.
    """
    if not 1 <= max_matches <= 50 or not 1 <= page_size <= 50 or not 1 <= max_pages <= 100:
        raise ValueError("invalid history limits")
    accepted, raw_seen = {}, set()
    excluded = Counter()
    offset = pages = retained_bytes = 0
    overlap = False
    stop = "page_limit"
    for _ in range(max_pages):
        page = tuple(await fetch_page(player, offset=offset, length=page_size))
        pages += 1
        if len(page) > page_size:
            raise PaginationError()
        if not page:
            stop = "empty_page"
            break
        offset += len(page)
        new_raw = 0
        for match in page:
            if not match.matchId:
                excluded["missing_match_id"] += 1
                continue
            if len(match.matchId) > 256:
                # Keep deduplication keys bounded even for excluded malformed
                # rows; they must not bypass the retained protobuf byte budget.
                excluded["oversized_match_id"] += 1
                continue
            if match.matchId in previous_match_ids:
                overlap = True
            if match.matchId in raw_seen:
                excluded["duplicate"] += 1
                continue
            raw_seen.add(match.matchId)
            new_raw += 1
            if match.state != 0:
                excluded["canceled_or_annulled"] += 1
                continue
            if match.finishDate <= 0 or match.startDate > match.finishDate:
                excluded["invalid_finished_date"] += 1
                continue
            if not _belongs_to_player(match, player):
                excluded["target_participant_missing_or_inconsistent"] += 1
                continue
            if len(accepted) < max_matches:
                retained_bytes += match.ByteSize()
                if retained_bytes > MAX_HISTORY_BYTES:
                    raise PaginationError()
                accepted[match.matchId] = match
        if len(accepted) >= max_matches:
            stop = "requested_limit"
            break
        if not new_raw:
            stop = "no_new_match_ids"
            break
    records = tuple(sorted(accepted.values(), key=lambda item: (-item.finishDate, item.matchId)))
    return HistoryBatch(records, max_matches, pages, offset, dict(excluded), stop,
                        len(raw_seen) > 20, overlap if previous_match_ids else None,
                        bool(previous_match_ids) and not overlap)
