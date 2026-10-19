"""Shared temporal policy. IDs are exact strings; all boundaries are exclusive."""

from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from datetime import UTC, datetime
from itertools import groupby
from uuid import UUID

from cmdl.data.profile import ID_PATTERN

PROTOCOL_ID = "deskdrop-temporal-v1"
TRAIN_END = datetime(2017, 1, 1, tzinfo=UTC)
VALIDATION_END = datetime(2017, 2, 1, tzinfo=UTC)
TEST_END = datetime(2017, 3, 1, tzinfo=UTC)
PERIODS = {"validation": (TRAIN_END, VALIDATION_END), "test": (VALIDATION_END, TEST_END)}


class TemporalError(ValueError):
    """A temporal input or snapshot cannot safely satisfy the shared policy."""


def utc_boundary(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise TemporalError("Cutoffs must include an explicit timezone")
    return value.astimezone(UTC)


def source_id(value: str) -> str:
    if not isinstance(value, str) or not ID_PATTERN.fullmatch(value):
        raise TemporalError("Source identifiers must be exact signed-digit strings")
    return value


def partition(event_at: datetime) -> str:
    at = utc_boundary(event_at)
    if at < TRAIN_END:
        return "train"
    if at < VALIDATION_END:
        return "validation"
    if at < TEST_END:
        return "test"
    return "after_test"


@dataclass(frozen=True)
class Metadata:
    metadata_version_id: UUID
    content_id: str
    share_event_id: UUID
    shared_at: datetime
    title: str
    text: str
    url: str
    lang: str
    content_type: str
    payload_sha256: str


@dataclass(frozen=True)
class LifecycleEvent:
    article_event_id: UUID
    content_id: str
    event_at: datetime
    event_type: str
    row_number: int
    duplicate_row_number: int | None = None


@dataclass(frozen=True)
class SourceInteraction:
    interaction_id: UUID
    person_id: str
    content_id: str
    event_type: str
    event_at: datetime
    row_number: int
    duplicate_of_interaction_id: UUID | None = None


@dataclass(frozen=True)
class ArticleAtCutoff:
    content_id: str
    availability: str
    metadata: Metadata | None


@dataclass(frozen=True)
class TrainingInteraction:
    interaction_id: UUID
    person_id: str
    content_id: str
    event_type: str
    event_at: datetime
    metadata: Metadata


class _Timeline:
    """Publisher/evaluator internals; full-source events never escape in model views."""

    def __init__(self, events: list[LifecycleEvent], metadata: dict[UUID, Metadata]):
        self.groups = {}
        self.times = {}
        self.metadata = metadata
        self.first_share = {}
        by_item = sorted(events, key=lambda e: (e.content_id, e.event_at, e.row_number))
        for item, item_events in groupby(by_item, key=lambda e: e.content_id):
            groups = [
                (at, tuple(group)) for at, group in groupby(item_events, key=lambda e: e.event_at)
            ]
            self.groups[item] = groups
            self.times[item] = [at for at, _ in groups]
            shares = [
                at for at, group in groups if any(e.event_type == "CONTENT SHARED" for e in group)
            ]
            if shares:
                self.first_share[item] = min(shares)

    def availability(self, content_id: str, at: datetime) -> str:
        times = self.times.get(content_id, ())
        pos = bisect_right(times, at) - 1
        if pos < 0:
            return "not_shared"
        if times[pos] == at:
            return "ambiguous"
        kinds = {e.event_type for e in self.groups[content_id][pos][1]}
        if len(kinds) != 1:
            return "ambiguous"
        return "available" if kinds == {"CONTENT SHARED"} else "removed"

    def metadata_at(self, content_id: str, at: datetime) -> Metadata | None:
        pos = bisect_left(self.times.get(content_id, ()), at) - 1
        for _, group in reversed(self.groups.get(content_id, ())[: pos + 1]):
            if {e.event_type for e in group} != {"CONTENT SHARED"}:
                continue
            shares = [e for e in group if e.duplicate_row_number is None]
            versions = [self.metadata.get(e.article_event_id) for e in shares]
            if not versions or any(v is None for v in versions):
                raise TemporalError(
                    "Selected metadata variant is missing a historical share version"
                )
            if len({v.payload_sha256 for v in versions}) != 1:
                raise TemporalError("Distinct share metadata at the same second has no known order")
            # Equivalent same-second shares use the earliest canonical source row.
            return versions[0]
        return None

    def exclusion(self, interaction: SourceInteraction) -> str | None:
        item, at = interaction.content_id, interaction.event_at
        if item not in self.groups:
            return "unresolved"
        if item not in self.first_share:
            return "removal_only"
        if at < self.first_share[item]:
            return "pre_share"
        state = self.availability(item, at)
        if state == "ambiguous":
            return "ambiguous"
        if state == "removed":
            return "removed"
        return None


@dataclass(frozen=True)
class PublishedSnapshot:
    """Model input surface: frozen request catalog and strictly earlier eligible history."""

    snapshot_id: UUID
    protocol_id: str
    as_of_at: datetime
    metadata_variant_id: str
    articles: tuple[ArticleAtCutoff, ...]
    history: tuple[TrainingInteraction, ...]
    consumed_pairs: frozenset[tuple[str, str]]

    def __post_init__(self):
        cutoff = utc_boundary(self.as_of_at)
        object.__setattr__(self, "as_of_at", cutoff)
        for article in self.articles:
            if article.availability == "available" and article.metadata is None:
                raise TemporalError("Available candidates require historical metadata")
            if article.metadata and (
                article.metadata.shared_at >= cutoff
                or article.metadata.content_id != article.content_id
            ):
                raise TemporalError("Candidate metadata violates the request boundary/version")
        for event in self.history:
            if (
                event.event_at >= cutoff
                or event.metadata.shared_at >= event.event_at
                or event.content_id != event.metadata.content_id
            ):
                raise TemporalError("Training metadata/history violates the exclusive cutoff")

    @property
    def known_users(self) -> frozenset[str]:
        return frozenset(person for person, _ in self.consumed_pairs)

    def candidates(self, person_id: str | None = None, reference_content_id: str | None = None):
        if person_id is not None:
            source_id(person_id)
        if reference_content_id is not None:
            source_id(reference_content_id)
            if not any(
                a.content_id == reference_content_id and a.availability == "available"
                for a in self.articles
            ):
                raise TemporalError("Similar-article reference is unavailable in this snapshot")
        return tuple(
            a
            for a in self.articles
            if a.availability == "available"
            and a.content_id != reference_content_id
            and (person_id is None or (person_id, a.content_id) not in self.consumed_pairs)
        )

    def user_history(self, person_id: str):
        source_id(person_id)
        return tuple(i for i in self.history if i.person_id == person_id)
