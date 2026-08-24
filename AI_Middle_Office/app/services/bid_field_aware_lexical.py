"""Deterministic field-aware lexical projection and BM25F for RQ1-D.

The projection is a read-only derivative of an immutable C3 RetrievalIndex.
It never changes evidence roles: Search still ranks Retrieval Children and
Read still returns only citable Evidence Atoms.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import threading
import unicodedata
from collections import Counter, OrderedDict
from dataclasses import dataclass
from typing import Iterable, Mapping, Sequence


LEXICAL_SEARCH_CONTRACT_VERSION = "bid.evidence.lexical-search.v1"
LEGACY_LEXICAL_SEARCH_PROFILE_VERSION = (
    "bid-evidence-lexical-profile-v0-single-field"
)
FIELD_AWARE_LEXICAL_PROFILE_VERSION = (
    "bid-evidence-lexical-profile-v1-rq1d"
)

CHANNEL_ORDER = (
    "section_heading",
    "table_key",
    "table_value",
    "table_row",
    "body",
)
BASE_CHANNEL_WEIGHTS = {
    "section_heading": 1.35,
    "table_key": 1.80,
    "table_value": 1.35,
    "table_row": 1.10,
    "body": 1.00,
}
VALUE_ANSWER_SHAPES = frozenset(
    {
        "area",
        "boolean",
        "boolean_or_ratio",
        "condition",
        "count",
        "date",
        "datetime",
        "duration",
        "duration_and_start",
        "entity_name",
        "location",
        "money_and_method",
        "qualification",
        "ratio",
        "ratio_and_condition",
        "ratio_and_duration",
        "responsible_party",
    }
)
TEXT_ANSWER_SHAPES = frozenset({"list", "method", "standard", "text"})
BM25_K1 = 1.2
BM25_B = 0.75
BOILERPLATE_DF_RATIO = 0.65
SEVERE_BOILERPLATE_DF_RATIO = 0.85
ORIGINAL_QUERY_ANCHOR_WEIGHT = 0.45
LEGACY_CHILD_RRF_WEIGHT = 1.00
# RQ1-D v1 is intentionally a bounded tie-breaker over the proven RQ1-C
# Child BM25 baseline.  The single-document Silver ablation showed that a
# replacement-strength field ranker over-promotes generic headings/tables.
FIELD_AWARE_CHILD_RRF_WEIGHT = 0.005
STRUCTURED_FIELD_RRF_WEIGHT = 0.10
FIELD_AWARE_PARENT_WEIGHT = 0.20
MAX_CHILDREN_PER_PARENT = 2
_CACHE_LIMIT = 8

_TABLE_CELL_SPLIT = re.compile(r"\s*\|\s*")
_ASCII_TERM = re.compile(r"[a-z0-9][a-z0-9_.:%/-]*")
_CJK_RUN = re.compile(r"[\u4e00-\u9fff]+")
_WHITESPACE = re.compile(r"\s+")
_NON_WORD_FINGERPRINT = re.compile(r"[^a-z0-9\u4e00-\u9fff]+")


class BidFieldAwareLexicalError(ValueError):
    code = "BID_FIELD_AWARE_LEXICAL_INVALID"


@dataclass(frozen=True)
class LexicalAtomSource:
    evidence_id: str
    text: str
    block_type: str
    section_path: tuple[str, ...] = ()


@dataclass(frozen=True)
class LexicalChildSource:
    child_id: str
    child_key: str
    entry_hash: str
    child_text: str
    section_path: tuple[str, ...]
    atoms: tuple[LexicalAtomSource, ...]


@dataclass(frozen=True)
class FieldAwareLexicalProjection:
    child_id: str
    child_key: str
    source_entry_hash: str
    channels: tuple[tuple[str, str], ...]
    token_counts: tuple[tuple[str, tuple[tuple[str, int], ...]], ...]
    channel_lengths: tuple[tuple[str, int], ...]
    projection_hash: str

    def channel_map(self) -> dict[str, str]:
        return dict(self.channels)

    def token_map(self) -> dict[str, dict[str, int]]:
        return {
            channel: dict(values) for channel, values in self.token_counts
        }

    def length_map(self) -> dict[str, int]:
        return dict(self.channel_lengths)

    def stable_payload(self) -> dict[str, object]:
        return {
            "child_key": self.child_key,
            "source_entry_hash": self.source_entry_hash,
            "channels": {key: value for key, value in self.channels},
            "projection_hash": self.projection_hash,
        }


@dataclass(frozen=True)
class FieldAwareLexicalCorpus:
    profile_version: str
    projections: tuple[FieldAwareLexicalProjection, ...]
    document_frequencies: tuple[tuple[str, int], ...]
    average_channel_lengths: tuple[tuple[str, float], ...]
    boilerplate_terms: tuple[str, ...]
    corpus_hash: str

    def projection_map(self) -> dict[str, FieldAwareLexicalProjection]:
        return {row.child_id: row for row in self.projections}


@dataclass(frozen=True)
class FieldAwareLexicalRank:
    child_id: str
    score: float
    matched_channels: tuple[str, ...]


_CORPUS_CACHE: OrderedDict[str, FieldAwareLexicalCorpus] = OrderedDict()
_CORPUS_CACHE_LOCK = threading.RLock()


def _canonical_hash(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def normalize_lexical_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or ""))
    return _WHITESPACE.sub(" ", normalized).strip()


def lexical_tokens(value: str) -> tuple[str, ...]:
    normalized = normalize_lexical_text(value).lower()
    ascii_terms = _ASCII_TERM.findall(normalized)
    chinese_terms: list[str] = []
    for run in _CJK_RUN.findall(normalized):
        if len(run) <= 2:
            chinese_terms.append(run)
            continue
        chinese_terms.extend(run[index : index + 2] for index in range(len(run) - 1))
        chinese_terms.extend(run[index : index + 3] for index in range(len(run) - 2))
    return tuple(ascii_terms + chinese_terms)


def _dedupe_text(values: Iterable[str]) -> str:
    rows: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = normalize_lexical_text(value)
        fingerprint = _NON_WORD_FINGERPRINT.sub("", normalized.lower())
        if not normalized or not fingerprint or fingerprint in seen:
            continue
        seen.add(fingerprint)
        rows.append(normalized)
    return "\n".join(rows)


def _looks_like_table_key(key: str, value: str) -> bool:
    raw_key = normalize_lexical_text(key)
    normalized_key = raw_key.strip("：: ")
    normalized_value = normalize_lexical_text(value)
    if not normalized_key or not normalized_value or len(normalized_key) > 40:
        return False
    key_word_chars = _NON_WORD_FINGERPRINT.sub("", normalized_key.lower())
    if not key_word_chars:
        return False
    key_digit_ratio = sum(character.isdigit() for character in normalized_key) / max(
        len(normalized_key), 1
    )
    if key_digit_ratio > 0.5:
        return False
    return len(normalized_key) <= len(normalized_value) or raw_key.endswith(
        ("：", ":")
    )


def _table_key_values(text: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    cells = tuple(
        value
        for value in (
            normalize_lexical_text(part) for part in _TABLE_CELL_SPLIT.split(text)
        )
        if value
    )
    if len(cells) < 2:
        return (), ()
    pairs = tuple(zip(cells[0::2], cells[1::2])) if len(cells) % 2 == 0 else ()
    if pairs and all(_looks_like_table_key(key, value) for key, value in pairs):
        return tuple(key for key, _value in pairs), tuple(value for _key, value in pairs)
    if len(cells) == 2 and _looks_like_table_key(cells[0], cells[1]):
        return (cells[0],), (cells[1],)
    return (), ()


def _projection(source: LexicalChildSource) -> FieldAwareLexicalProjection:
    if (
        not source.child_id
        or not source.child_key
        or not source.entry_hash
        or not source.atoms
    ):
        raise BidFieldAwareLexicalError("BID_LEXICAL_SOURCE_INVALID")
    heading_values: list[str] = list(source.section_path)
    table_keys: list[str] = []
    table_values: list[str] = []
    table_rows: list[str] = []
    body_values: list[str] = []
    for atom in source.atoms:
        text = normalize_lexical_text(atom.text)
        if not text:
            continue
        block_type = str(atom.block_type or "").strip().lower()
        if block_type == "heading":
            heading_values.append(text)
        elif block_type in {"table", "table_row"}:
            table_rows.append(text)
            keys, values = _table_key_values(text)
            table_keys.extend(keys)
            table_values.extend(values)
        else:
            body_values.append(text)
    channels = {
        "section_heading": _dedupe_text(heading_values),
        "table_key": _dedupe_text(table_keys),
        "table_value": _dedupe_text(table_values),
        "table_row": _dedupe_text(table_rows),
        "body": _dedupe_text(body_values),
    }
    if not any(channels.values()):
        channels["body"] = normalize_lexical_text(source.child_text)
    frozen_channels = tuple((name, channels[name]) for name in CHANNEL_ORDER)
    token_counts: list[tuple[str, tuple[tuple[str, int], ...]]] = []
    channel_lengths: list[tuple[str, int]] = []
    for channel, text in frozen_channels:
        tokens = lexical_tokens(text)
        token_counts.append((channel, tuple(sorted(Counter(tokens).items()))))
        channel_lengths.append((channel, len(tokens)))
    stable = {
        "contract_version": LEXICAL_SEARCH_CONTRACT_VERSION,
        "profile_version": FIELD_AWARE_LEXICAL_PROFILE_VERSION,
        "child_key": source.child_key,
        "source_entry_hash": source.entry_hash,
        "channels": {key: value for key, value in frozen_channels},
    }
    return FieldAwareLexicalProjection(
        child_id=source.child_id,
        child_key=source.child_key,
        source_entry_hash=source.entry_hash,
        channels=frozen_channels,
        token_counts=tuple(token_counts),
        channel_lengths=tuple(channel_lengths),
        projection_hash=_canonical_hash(stable),
    )


def build_field_aware_lexical_corpus(
    sources: Sequence[LexicalChildSource],
) -> FieldAwareLexicalCorpus:
    projections = tuple(
        _projection(source)
        for source in sorted(sources, key=lambda row: (row.child_key, row.child_id))
    )
    if (
        not projections
        or len({row.child_id for row in projections}) != len(projections)
        or len({row.child_key for row in projections}) != len(projections)
    ):
        raise BidFieldAwareLexicalError("BID_LEXICAL_CORPUS_INVALID")
    document_frequencies: Counter[str] = Counter()
    channel_totals: Counter[str] = Counter()
    for projection in projections:
        token_map = projection.token_map()
        document_terms: set[str] = set()
        for channel in CHANNEL_ORDER:
            terms = token_map[channel]
            document_terms.update(terms)
            channel_totals[channel] += sum(terms.values())
        document_frequencies.update(document_terms)
    document_count = len(projections)
    boilerplate_terms = tuple(
        sorted(
            term
            for term, frequency in document_frequencies.items()
            if document_count >= 4
            and frequency / document_count >= BOILERPLATE_DF_RATIO
        )
    )
    average_lengths = tuple(
        (
            channel,
            round(channel_totals[channel] / max(document_count, 1), 8),
        )
        for channel in CHANNEL_ORDER
    )
    stable = {
        "contract_version": LEXICAL_SEARCH_CONTRACT_VERSION,
        "profile_version": FIELD_AWARE_LEXICAL_PROFILE_VERSION,
        "projections": [row.stable_payload() for row in projections],
        "document_frequencies": dict(sorted(document_frequencies.items())),
        "average_channel_lengths": dict(average_lengths),
        "boilerplate_terms": list(boilerplate_terms),
    }
    return FieldAwareLexicalCorpus(
        profile_version=FIELD_AWARE_LEXICAL_PROFILE_VERSION,
        projections=projections,
        document_frequencies=tuple(sorted(document_frequencies.items())),
        average_channel_lengths=average_lengths,
        boilerplate_terms=boilerplate_terms,
        corpus_hash=_canonical_hash(stable),
    )


def _channel_weights(
    *,
    field_codes: Sequence[str],
    answer_shapes: Sequence[str],
) -> dict[str, float]:
    weights = dict(BASE_CHANNEL_WEIGHTS)
    shapes = {str(value) for value in answer_shapes}
    if field_codes:
        weights["table_key"] *= 1.15
        weights["section_heading"] *= 1.08
    if shapes & VALUE_ANSWER_SHAPES:
        weights["table_value"] *= 1.25
        weights["table_row"] *= 1.08
    if shapes & TEXT_ANSWER_SHAPES:
        weights["body"] *= 1.15
        weights["section_heading"] *= 1.10
    return weights


def rank_field_aware_bm25f(
    query: str,
    corpus: FieldAwareLexicalCorpus,
    *,
    field_codes: Sequence[str] = (),
    answer_shapes: Sequence[str] = (),
) -> tuple[FieldAwareLexicalRank, ...]:
    query_terms = tuple(dict.fromkeys(lexical_tokens(query)))
    if not query_terms:
        return ()
    document_count = len(corpus.projections)
    frequencies = dict(corpus.document_frequencies)
    average_lengths = dict(corpus.average_channel_lengths)
    weights = _channel_weights(field_codes=field_codes, answer_shapes=answer_shapes)
    normalized_query = _NON_WORD_FINGERPRINT.sub(
        "", normalize_lexical_text(query).lower()
    )
    ranks: list[FieldAwareLexicalRank] = []
    for projection in corpus.projections:
        token_map = projection.token_map()
        length_map = projection.length_map()
        channel_text = projection.channel_map()
        channel_scores: Counter[str] = Counter()
        score = 0.0
        for term in query_terms:
            frequency = frequencies.get(term, 0)
            if frequency <= 0:
                continue
            ratio = frequency / max(document_count, 1)
            suppression = (
                0.20
                if ratio >= SEVERE_BOILERPLATE_DF_RATIO
                else 0.45
                if ratio >= BOILERPLATE_DF_RATIO
                else 1.0
            )
            inverse = math.log(
                1
                + (document_count - frequency + 0.5)
                / (frequency + 0.5)
            )
            weighted_tf = 0.0
            per_channel_tf: dict[str, float] = {}
            for channel in CHANNEL_ORDER:
                tf = token_map[channel].get(term, 0)
                if not tf:
                    continue
                length = length_map[channel]
                average = max(float(average_lengths[channel]), 1.0)
                normalized_tf = tf / (
                    1.0 - BM25_B + BM25_B * length / average
                )
                contribution = weights[channel] * normalized_tf
                per_channel_tf[channel] = contribution
                weighted_tf += contribution
            if weighted_tf <= 0:
                continue
            term_score = (
                inverse
                * suppression
                * ((BM25_K1 + 1.0) * weighted_tf)
                / (BM25_K1 + weighted_tf)
            )
            score += term_score
            for channel, contribution in per_channel_tf.items():
                channel_scores[channel] += term_score * contribution / weighted_tf
        if 2 <= len(normalized_query) <= 120:
            for channel in CHANNEL_ORDER:
                normalized_channel = _NON_WORD_FINGERPRINT.sub(
                    "", channel_text[channel].lower()
                )
                if normalized_query and normalized_query in normalized_channel:
                    bonus = min(1.0, 0.4 * weights[channel])
                    score += bonus
                    channel_scores[channel] += bonus
        if score > 0:
            ranks.append(
                FieldAwareLexicalRank(
                    child_id=projection.child_id,
                    score=round(score, 8),
                    matched_channels=tuple(
                        channel
                        for channel in CHANNEL_ORDER
                        if channel_scores[channel] > 0
                    ),
                )
            )
    stable_keys = {
        projection.child_id: projection.child_key
        for projection in corpus.projections
    }
    ranks.sort(
        key=lambda row: (
            -row.score,
            stable_keys[row.child_id],
            row.child_id,
        )
    )
    return tuple(ranks)


def get_cached_field_aware_corpus(
    cache_key: str,
    *,
    expected_entry_hashes: Mapping[str, str],
) -> FieldAwareLexicalCorpus | None:
    with _CORPUS_CACHE_LOCK:
        corpus = _CORPUS_CACHE.get(str(cache_key))
        if corpus is None:
            return None
        actual = {
            row.child_id: row.source_entry_hash for row in corpus.projections
        }
        if actual != dict(expected_entry_hashes):
            _CORPUS_CACHE.pop(str(cache_key), None)
            return None
        _CORPUS_CACHE.move_to_end(str(cache_key))
        return corpus


def cache_field_aware_corpus(
    cache_key: str,
    corpus: FieldAwareLexicalCorpus,
) -> None:
    with _CORPUS_CACHE_LOCK:
        _CORPUS_CACHE[str(cache_key)] = corpus
        _CORPUS_CACHE.move_to_end(str(cache_key))
        while len(_CORPUS_CACHE) > _CACHE_LIMIT:
            _CORPUS_CACHE.popitem(last=False)


def clear_field_aware_lexical_cache() -> None:
    with _CORPUS_CACHE_LOCK:
        _CORPUS_CACHE.clear()


__all__ = [
    "BASE_CHANNEL_WEIGHTS",
    "BidFieldAwareLexicalError",
    "CHANNEL_ORDER",
    "FIELD_AWARE_LEXICAL_PROFILE_VERSION",
    "FIELD_AWARE_CHILD_RRF_WEIGHT",
    "FIELD_AWARE_PARENT_WEIGHT",
    "FieldAwareLexicalCorpus",
    "FieldAwareLexicalProjection",
    "FieldAwareLexicalRank",
    "LEGACY_LEXICAL_SEARCH_PROFILE_VERSION",
    "LEGACY_CHILD_RRF_WEIGHT",
    "LEXICAL_SEARCH_CONTRACT_VERSION",
    "LexicalAtomSource",
    "LexicalChildSource",
    "MAX_CHILDREN_PER_PARENT",
    "ORIGINAL_QUERY_ANCHOR_WEIGHT",
    "STRUCTURED_FIELD_RRF_WEIGHT",
    "build_field_aware_lexical_corpus",
    "cache_field_aware_corpus",
    "clear_field_aware_lexical_cache",
    "get_cached_field_aware_corpus",
    "lexical_tokens",
    "normalize_lexical_text",
    "rank_field_aware_bm25f",
]
