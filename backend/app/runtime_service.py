from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.config import Settings
from app.domain import RuntimeSettings, UsageEvent, utc_now
from app.repository import NutritionRepository


def _minutes(value: str) -> int | None:
    try:
        hour_text, minute_text = value.split(":", 1)
        hour, minute = int(hour_text), int(minute_text)
    except (ValueError, AttributeError):
        return None
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        return None
    return hour * 60 + minute


def maintenance_active(
    runtime: RuntimeSettings, settings: Settings, now: datetime | None = None
) -> bool:
    if runtime.maintenance_manual:
        return True
    if not runtime.maintenance_schedule_enabled:
        return False
    try:
        zone = ZoneInfo(settings.app_timezone)
    except ZoneInfoNotFoundError:
        zone = timezone.utc
    local_now = (now or utc_now()).astimezone(zone)
    current = local_now.hour * 60 + local_now.minute
    start = _minutes(runtime.maintenance_start)
    end = _minutes(runtime.maintenance_end)
    if start is None or end is None or start == end:
        return False
    if start < end:
        return start <= current < end
    return current >= start or current < end


def image_limit_message(
    repository: NutritionRepository,
    runtime: RuntimeSettings,
    phone_number: str,
    now: datetime | None = None,
) -> str | None:
    current = now or utc_now()
    start = current - timedelta(minutes=runtime.image_window_minutes)
    image_events = [
        event
        for event in repository.get_usage_events(start, phone_number=phone_number)
        if event.request_type == "image"
        and event.image_count > 0
        and event.status not in {"maintenance", "rate_limited"}
    ]
    if image_events and runtime.image_cooldown_seconds > 0:
        elapsed = (current - image_events[0].created_at).total_seconds()
        if elapsed < runtime.image_cooldown_seconds:
            wait = max(1, round(runtime.image_cooldown_seconds - elapsed))
            return f"Tunggu {wait} detik sebelum mengirim foto berikutnya."
    if len(image_events) >= runtime.max_images_per_window:
        return (
            f"Batas {runtime.max_images_per_window} foto per "
            f"{runtime.image_window_minutes} menit telah tercapai. Coba lagi nanti."
        )
    return None


def new_usage_event(
    repository: NutritionRepository,
    phone_number: str,
    request_type: str,
    *,
    image_count: int = 0,
    status: str = "success",
) -> UsageEvent:
    return repository.add_usage_event(
        UsageEvent(
            user_id=phone_number,
            request_type=request_type,
            image_count=image_count,
            status=status,
        )
    )
