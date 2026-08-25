from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class SourceAvailability(StrEnum):
    AVAILABLE = "available"
    PATH_EXPIRED = "path_expired"
    PERMISSION_DENIED = "permission_denied"
    DELETED = "deleted"


class CandidateSourceStatus(StrEnum):
    CAPTURED = "captured"
    OPENED = "opened"
    PROMOTED = "promoted"
    DISCARDED = "discarded"


class ProvenanceScope(StrEnum):
    """一条原话是谁给的。

    客户提供、经理反馈、公开材料是三件事：客户口径要带归属且保留口径缺口；
    经理反馈是内部指示，可以带归属写进稿，但既不是客户口径也不是外部证据；
    其余（公开网页、本机文件）不得写成任何人的口头。
    """

    CLIENT_PROVIDED = "client_provided"
    MANAGER_FEEDBACK = "manager_feedback"


class EpistemicType(StrEnum):
    FACTUAL_CLAIM = "factual_claim"
    INFERENCE = "inference"
    ASSUMPTION = "assumption"
    JUDGMENT = "judgment"


class VerificationStatus(StrEnum):
    CAPTURED = "captured"
    SOURCE_CHECKED = "source_checked"
    CORROBORATED = "corroborated"
    CONFLICTED = "conflicted"
    STALE = "stale"
    UNVERIFIABLE = "unverifiable"
    EXCLUDED = "excluded"


class ModelSuggestionKind(StrEnum):
    FINDING = "finding"
    OPTION = "option"


class ModelSuggestionStatus(StrEnum):
    PENDING = "pending"
    ADOPTED = "adopted"
    DISMISSED = "dismissed"


class OptionStatus(StrEnum):
    CANDIDATE = "candidate"
    NEEDS_EVIDENCE = "needs_evidence"
    RETAINED = "retained"
    DEFERRED = "deferred"
    EXCLUDED = "excluded"


class OverrideHandling(StrEnum):
    ASSUMPTION = "assumption"
    EXCLUDE = "exclude"
    SCENARIO = "scenario"


class ReviewAction(StrEnum):
    APPROVE = "approve"
    MODIFY = "modify"
    EXCLUDE = "exclude"


@dataclass(frozen=True, slots=True)
class Project:
    id: str
    name: str
    template_key: str
    execution_strategy_key: str
    schema_version: str


@dataclass(frozen=True, slots=True)
class CandidateSource:
    id: str
    project_id: str
    url: str
    title: str
    status: CandidateSourceStatus
    promoted_source_id: str | None = None


@dataclass(frozen=True, slots=True)
class Source:
    id: str
    project_id: str
    title: str
    availability: SourceAvailability
    content_hash: str | None = None
    supersedes_source_id: str | None = None


@dataclass(frozen=True, slots=True)
class EvidenceExcerpt:
    id: str
    source_id: str
    excerpt: str
    locator_json: str


@dataclass(frozen=True, slots=True)
class Claim:
    id: str
    project_id: str
    text: str
    epistemic_type: EpistemicType
    verification_status: VerificationStatus


@dataclass(frozen=True, slots=True)
class DeliverableBlock:
    id: str
    project_id: str
    title: str
    current_text: str
    restriction: str | None
