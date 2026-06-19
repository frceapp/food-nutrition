from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def ensure_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


@dataclass(slots=True)
class User:
    phone_number: str
    name: str | None = None
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)

    @property
    def id(self) -> str:
        return self.phone_number


@dataclass(slots=True)
class UserProfile:
    user_id: str
    weight_kg: float | None = None
    height_cm: float | None = None
    activity_level: str | None = None
    bmi: float | None = None
    bmi_category: str | None = None
    daily_calorie_target: float | None = None
    onboarding_step: str = "weight"
    completed_at: datetime | None = None
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)


@dataclass(slots=True)
class FoodAnalysisLog:
    user_id: str
    status: str
    id: str | None = None
    image_path: str | None = None
    detected_foods_json: list | None = None
    nutrition_json: dict | None = None
    plate_analysis_json: dict | None = None
    gemini_raw_response: str | None = None
    formatted_message: str | None = None
    confidence: str | None = None
    error_message: str | None = None
    revision_count: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float = 0.0
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)


@dataclass(slots=True)
class WhitelistEntry:
    phone_number: str
    label: str | None = None
    is_admin: bool = False
    is_enabled: bool = True
    created_by_phone: str | None = None
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)

    @property
    def id(self) -> str:
        return self.phone_number


@dataclass(slots=True)
class RuntimeSettings:
    maintenance_manual: bool = False
    maintenance_schedule_enabled: bool = False
    maintenance_start: str = "23:00"
    maintenance_end: str = "01:00"
    maintenance_message: str = (
        "NUTRIA sedang dalam pemeliharaan. Silakan coba kembali setelah maintenance selesai."
    )
    image_cooldown_seconds: int = 30
    image_window_minutes: int = 60
    max_images_per_window: int = 10
    max_revisions_per_analysis: int = 3
    updated_at: datetime = field(default_factory=utc_now)
    updated_by_phone: str | None = None


@dataclass(slots=True)
class UsageEvent:
    user_id: str
    request_type: str
    status: str = "success"
    id: str | None = None
    image_count: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float = 0.0
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)


def to_document(value: Any) -> dict:
    return asdict(value)


def user_from_document(document: dict) -> User:
    return User(
        phone_number=str(document["phone_number"]),
        name=document.get("name"),
        created_at=ensure_utc(document.get("created_at")) or utc_now(),
        updated_at=ensure_utc(document.get("updated_at")) or utc_now(),
    )


def profile_from_document(document: dict) -> UserProfile:
    return UserProfile(
        user_id=str(document["user_id"]),
        weight_kg=document.get("weight_kg"),
        height_cm=document.get("height_cm"),
        activity_level=document.get("activity_level"),
        bmi=document.get("bmi"),
        bmi_category=document.get("bmi_category"),
        daily_calorie_target=document.get("daily_calorie_target"),
        onboarding_step=document.get("onboarding_step", "weight"),
        completed_at=ensure_utc(document.get("completed_at")),
        created_at=ensure_utc(document.get("created_at")) or utc_now(),
        updated_at=ensure_utc(document.get("updated_at")) or utc_now(),
    )


def log_from_document(document: dict, document_id: str) -> FoodAnalysisLog:
    return FoodAnalysisLog(
        id=document_id,
        user_id=str(document["user_id"]),
        status=str(document["status"]),
        image_path=document.get("image_path"),
        detected_foods_json=document.get("detected_foods_json"),
        nutrition_json=document.get("nutrition_json"),
        plate_analysis_json=document.get("plate_analysis_json"),
        gemini_raw_response=document.get("gemini_raw_response"),
        formatted_message=document.get("formatted_message"),
        confidence=document.get("confidence"),
        error_message=document.get("error_message"),
        revision_count=int(document.get("revision_count", 0)),
        prompt_tokens=int(document.get("prompt_tokens", 0)),
        completion_tokens=int(document.get("completion_tokens", 0)),
        total_tokens=int(document.get("total_tokens", 0)),
        cost_usd=float(document.get("cost_usd", 0.0)),
        created_at=ensure_utc(document.get("created_at")) or utc_now(),
        updated_at=ensure_utc(document.get("updated_at")) or utc_now(),
    )


def whitelist_from_document(document: dict) -> WhitelistEntry:
    return WhitelistEntry(
        phone_number=str(document["phone_number"]),
        label=document.get("label"),
        is_admin=bool(document.get("is_admin", False)),
        is_enabled=bool(document.get("is_enabled", True)),
        created_by_phone=document.get("created_by_phone"),
        created_at=ensure_utc(document.get("created_at")) or utc_now(),
        updated_at=ensure_utc(document.get("updated_at")) or utc_now(),
    )


def runtime_settings_from_document(document: dict | None) -> RuntimeSettings:
    document = document or {}
    return RuntimeSettings(
        maintenance_manual=bool(document.get("maintenance_manual", False)),
        maintenance_schedule_enabled=bool(
            document.get("maintenance_schedule_enabled", False)
        ),
        maintenance_start=str(document.get("maintenance_start", "23:00")),
        maintenance_end=str(document.get("maintenance_end", "01:00")),
        maintenance_message=str(
            document.get("maintenance_message")
            or RuntimeSettings().maintenance_message
        ),
        image_cooldown_seconds=max(0, int(document.get("image_cooldown_seconds", 30))),
        image_window_minutes=max(1, int(document.get("image_window_minutes", 60))),
        max_images_per_window=max(1, int(document.get("max_images_per_window", 10))),
        max_revisions_per_analysis=max(
            0, int(document.get("max_revisions_per_analysis", 3))
        ),
        updated_at=ensure_utc(document.get("updated_at")) or utc_now(),
        updated_by_phone=document.get("updated_by_phone"),
    )


def usage_event_from_document(document: dict, document_id: str) -> UsageEvent:
    return UsageEvent(
        id=document_id,
        user_id=str(document["user_id"]),
        request_type=str(document.get("request_type", "chat")),
        status=str(document.get("status", "success")),
        image_count=int(document.get("image_count", 0)),
        prompt_tokens=int(document.get("prompt_tokens", 0)),
        completion_tokens=int(document.get("completion_tokens", 0)),
        total_tokens=int(document.get("total_tokens", 0)),
        cost_usd=float(document.get("cost_usd", 0.0)),
        created_at=ensure_utc(document.get("created_at")) or utc_now(),
        updated_at=ensure_utc(document.get("updated_at")) or utc_now(),
    )
