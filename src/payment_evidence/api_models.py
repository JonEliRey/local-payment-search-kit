from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator
from pydantic_core import PydanticCustomError


BoundedText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)]
LongText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=512)]
TimestampText = Annotated[str, StringConstraints(pattern=r"^\d{14}$")]
DateWindowText = Annotated[str, StringConstraints(pattern=r"^(\d{14}|\d{4}-\d{2}-\d{2})$")]
LastFourText = Annotated[str, StringConstraints(pattern=r"^\d{4}$")]


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ApiResponseModel(BaseModel):
    model_config = ConfigDict(extra="ignore")


class HealthResponse(ApiResponseModel):
    status: Literal["ok"] = "ok"


class ApiErrorDetail(ApiResponseModel):
    field: BoundedText
    code: BoundedText


class ErrorResponse(ApiResponseModel):
    status: Literal["error"] = "error"
    error: BoundedText
    errors: list[ApiErrorDetail] = Field(default_factory=list)
    elapsed_ms: int | None = Field(default=None, ge=0)


class DeniedResponse(ApiResponseModel):
    status: Literal["denied"] = "denied"
    reason: BoundedText


class IdentityScope(ApiResponseModel):
    user_id: BoundedText
    role: BoundedText
    tenant_id: BoundedText | None = None
    iso_id: BoundedText | None = None


class MerchantScope(ApiResponseModel):
    alias: BoundedText
    display_name: BoundedText | None = None
    gateway: BoundedText | None = None


class WhoamiResponse(ApiResponseModel):
    status: Literal["ok"] = "ok"
    identity: IdentityScope
    authorized_merchants: list[MerchantScope] = Field(default_factory=list)


class CapabilityField(ApiResponseModel):
    name: BoundedText
    label: BoundedText
    required: bool = False
    input_type: BoundedText = "text"
    help_text: LongText | None = None
    pattern: BoundedText | None = None
    min_value: int | Decimal | None = None
    max_value: int | Decimal | None = None
    choices: list[BoundedText] = Field(default_factory=list)


class GatewayCapability(ApiResponseModel):
    gateway: BoundedText
    supported_operations: list[BoundedText]
    fields: list[CapabilityField] = Field(default_factory=list)
    strongest_identifiers: list[BoundedText] = Field(default_factory=list)
    date_window_required: bool = True
    redaction_modes: list[BoundedText] = Field(default_factory=lambda: ["summary"])
    artifact_outputs: list[BoundedText] = Field(default_factory=list)
    caveats: list[LongText] = Field(default_factory=list)


class CapabilitiesResponse(ApiResponseModel):
    status: Literal["ok"] = "ok"
    gateways: list[GatewayCapability] = Field(default_factory=list)


class SearchRequest(ApiModel):
    merchant: BoundedText | None = None
    merchant_id: BoundedText | None = None
    start_date: DateWindowText | None = None
    end_date: DateWindowText | None = None
    amount: Decimal | None = Field(default=None, ge=Decimal("0"), max_digits=12, decimal_places=2)
    order_id: BoundedText | None = None
    transaction_id: BoundedText | None = None
    last_four: LastFourText | None = None
    action_type: BoundedText | None = None
    condition: BoundedText | None = None
    transaction_type: BoundedText | None = None
    result_limit: int = Field(default=100, ge=1, le=500)
    max_pages: int = Field(default=5, ge=1, le=25)


class CandidateActionSummary(ApiResponseModel):
    action_type: BoundedText | None = None
    amount: BoundedText | None = None
    date: BoundedText | None = None
    success: BoundedText | bool | None = None


class SearchCandidate(ApiResponseModel):
    rank: int
    score: int
    transaction_id: BoundedText | None = None
    order_id: BoundedText | None = None
    amount: BoundedText | None = None
    date: BoundedText | None = None
    last_four: LastFourText | None = None
    condition: BoundedText | None = None
    transaction_type: BoundedText | None = None
    currency: BoundedText | None = None
    cc_type: BoundedText | None = None
    action_summaries: list[CandidateActionSummary] = Field(default_factory=list)
    explanations: list[LongText] = Field(default_factory=list)


class CandidateSummary(ApiResponseModel):
    candidate_count: int = 0
    top_score: int = 0
    ambiguous: bool = False


class SearchResponse(ApiResponseModel):
    status: Literal["ok"] = "ok"
    search_lookup: dict[BoundedText, BoundedText | int | bool | None] = Field(default_factory=dict)
    candidate_summary: CandidateSummary = Field(default_factory=CandidateSummary)
    candidates: list[SearchCandidate] = Field(default_factory=list)
    elapsed_ms: int | None = Field(default=None, ge=0)


class InvestigateRequest(SearchRequest):
    @model_validator(mode="after")
    def require_concrete_detail_clue(self) -> InvestigateRequest:
        if self.amount is None and not self.transaction_id and not self.order_id:
            raise PydanticCustomError("concrete_detail_clue_required", "concrete_detail_clue_required")
        return self


class ArtifactMetadata(ApiResponseModel):
    artifact_id: BoundedText
    label: BoundedText
    kind: BoundedText
    content_type: BoundedText | None = None
    merchant: BoundedText | None = None
    created_at: BoundedText | None = None
    expires_at: BoundedText | None = None


class ArtifactListResponse(ApiResponseModel):
    status: Literal["ok"] = "ok"
    artifacts: list[ArtifactMetadata] = Field(default_factory=list)


class InvestigateResponse(ApiResponseModel):
    status: Literal["ok", "completed", "ambiguous", "no_match", "error"] = "ok"
    case_id: BoundedText | None = None
    transaction_id: BoundedText | None = None
    order_id: BoundedText | None = None
    selected_transaction_id: BoundedText | None = None
    candidate_summary: CandidateSummary | None = None
    selected_candidate: SearchCandidate | None = None
    candidates: list[SearchCandidate] = Field(default_factory=list)
    artifacts: list[ArtifactMetadata] = Field(default_factory=list)
    elapsed_ms: int | None = Field(default=None, ge=0)
