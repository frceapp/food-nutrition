import re
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, Query, Response, status

from app.config import Settings, get_settings
from app.domain import FoodAnalysisLog, User
from app.repository import NutritionRepository, RepositoryError, get_repository
from app.schemas import DailySummaryResponse


router = APIRouter(tags=["nutrition"])
PHONE_PATTERN = re.compile(r"^\+?\d{8,20}$")


def _number(value: float) -> str:
    return str(int(value)) if value.is_integer() else f"{value:.1f}"


def _utc_day_bounds(timezone_name: str) -> tuple[datetime, datetime]:
    try:
        local_timezone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        local_timezone = timezone.utc

    local_now = datetime.now(local_timezone)
    local_start = datetime.combine(local_now.date(), time.min, tzinfo=local_timezone)
    local_end = datetime.combine(
        local_now.date() + timedelta(days=1), time.min, tzinfo=local_timezone
    )
    utc_start = local_start.astimezone(timezone.utc)
    utc_end = local_end.astimezone(timezone.utc)
    return utc_start, utc_end


def _format_summary(logs: list[FoodAnalysisLog]) -> str:
    if not logs:
        return (
            "Halo Bunda 👋\n"
            "Belum ada makanan yang tercatat hari ini.\n\n"
            "Kirim foto makanan, nanti NUTRIA bantu mencatat estimasi gizinya."
        )

    totals = {
        "energy_kcal": 0.0,
        "carbohydrate_g": 0.0,
        "protein_g": 0.0,
        "fat_g": 0.0,
        "fiber_g": 0.0,
    }
    food_lines: list[str] = []
    for log in logs:
        nutrition = log.nutrition_json or {}
        for key in totals:
            value = nutrition.get(key, 0)
            if isinstance(value, (int, float)):
                totals[key] += float(value)

        for food in log.detected_foods_json or []:
            if not isinstance(food, dict):
                continue
            name = food.get("name")
            if not name:
                continue
            emoji = food.get("emoji") or "🍽️"
            portion = food.get("estimated_portion")
            food_lines.append(
                f"{emoji} {name}{f' ({portion})' if portion else ''}"
            )

    displayed_foods = food_lines[:20]
    if len(food_lines) > 20:
        displayed_foods.append(f"• dan {len(food_lines) - 20} item lainnya")
    foods_text = "\n".join(displayed_foods) or "• Detail makanan tidak tersedia"

    return (
        "Halo Bunda 👋\n"
        "Ringkasan estimasi makanan hari ini:\n\n"
        f"Makanan tercatat ({len(logs)} kali analisis):\n"
        f"{foods_text}\n\n"
        "Total estimasi gizi:\n"
        f"* Energi: ±{_number(totals['energy_kcal'])} kkal\n"
        f"* Karbohidrat: ±{_number(totals['carbohydrate_g'])} gram\n"
        f"* Protein: ±{_number(totals['protein_g'])} gram\n"
        f"* Lemak: ±{_number(totals['fat_g'])} gram\n"
        f"* Serat: ±{_number(totals['fiber_g'])} gram"
    )


def build_daily_summary_message(
    repository: NutritionRepository, user: User, settings: Settings
) -> str:
    utc_start, utc_end = _utc_day_bounds(settings.app_timezone)
    logs = repository.get_logs(
        user.phone_number,
        utc_start,
        utc_end,
        successful_only=True,
        ascending=True,
    )
    return _format_summary(logs)


@router.get("/daily-summary", response_model=DailySummaryResponse)
def daily_summary(
    response: Response,
    phone_number: str = Query(min_length=8, max_length=32),
    repository: NutritionRepository = Depends(get_repository),
    settings: Settings = Depends(get_settings),
) -> DailySummaryResponse:
    phone_number = phone_number.strip()
    if not PHONE_PATTERN.fullmatch(phone_number):
        response.status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
        return DailySummaryResponse(
            success=False,
            formatted_message="Nomor WhatsApp tidak valid",
            error_message="Nomor WhatsApp tidak valid",
        )

    try:
        user = repository.get_user(phone_number)
        if user is None:
            return DailySummaryResponse(
                success=True,
                formatted_message=_format_summary([]),
            )

        return DailySummaryResponse(
            success=True,
            formatted_message=build_daily_summary_message(repository, user, settings),
        )
    except RepositoryError:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return DailySummaryResponse(
            success=False,
            formatted_message="Maaf Bunda, ringkasan hari ini belum dapat dibuka.",
            error_message="Penyimpanan tidak tersedia",
        )
