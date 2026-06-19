from datetime import datetime, time, timedelta, timezone
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, Query, Response, status

from app.config import Settings, get_settings
from app.domain import User, UserProfile
from app.repository import NutritionRepository, RepositoryError, get_repository
from app.schemas import DailySummaryResponse


router = APIRouter(tags=["nutrition"])


def _period_bounds(
    timezone_name: str, period: Literal["daily", "weekly"]
) -> tuple[datetime, datetime, int]:
    try:
        local_timezone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        local_timezone = timezone.utc
    local_today = datetime.now(local_timezone).date()
    days = 1 if period == "daily" else 7
    start_date = local_today if days == 1 else local_today - timedelta(days=6)
    local_start = datetime.combine(start_date, time.min, tzinfo=local_timezone)
    local_end = datetime.combine(
        local_today + timedelta(days=1), time.min, tzinfo=local_timezone
    )
    return (
        local_start.astimezone(timezone.utc),
        local_end.astimezone(timezone.utc),
        days,
    )


def _number(value: float) -> str:
    return str(int(round(value)))


def _progress_status(percent: float) -> str:
    if percent < 80:
        return "belum mencapai target"
    if percent <= 120:
        return "sesuai rentang target"
    return "di atas target"


def build_evaluation_message(
    repository: NutritionRepository,
    user: User,
    profile: UserProfile,
    settings: Settings,
    period: Literal["daily", "weekly"],
) -> str:
    utc_start, utc_end, days = _period_bounds(settings.app_timezone, period)
    logs = repository.get_logs(
        user.phone_number,
        utc_start,
        utc_end,
        successful_only=True,
    )
    label = "hari ini" if period == "daily" else "7 hari terakhir"
    if not logs:
        return (
            f"Belum ada data makanan untuk {label}.\n"
            "Kirim foto makanan agar NUTRIA dapat membuat evaluasi."
        )

    totals = {
        "energy_kcal": 0.0,
        "carbohydrate_g": 0.0,
        "protein_g": 0.0,
        "fat_g": 0.0,
        "fiber_g": 0.0,
    }
    for log in logs:
        nutrition = log.nutrition_json or {}
        for key in totals:
            value = nutrition.get(key, 0)
            if isinstance(value, (int, float)):
                totals[key] += float(value)

    daily_calories = float(profile.daily_calorie_target or 0)
    targets = {
        "energy_kcal": daily_calories * days,
        "carbohydrate_g": (daily_calories * 0.50 / 4) * days,
        "protein_g": (daily_calories * 0.20 / 4) * days,
        "fat_g": (daily_calories * 0.30 / 9) * days,
        "fiber_g": 25.0 * days,
    }

    def line(label_text: str, key: str, unit: str) -> str:
        target = targets[key]
        percent = totals[key] / target * 100 if target else 0
        return (
            f"* {label_text}: {_number(totals[key])}/{_number(target)} {unit} "
            f"({percent:.0f}%) — {_progress_status(percent)}"
        )

    title = "Evaluasi nutrisi hari ini" if days == 1 else "Evaluasi nutrisi 7 hari terakhir"
    message = (
        f"{title}:\n\n"
        f"{line('Energi', 'energy_kcal', 'kkal')}\n"
        f"{line('Karbohidrat', 'carbohydrate_g', 'gram')}\n"
        f"{line('Protein', 'protein_g', 'gram')}\n"
        f"{line('Lemak', 'fat_g', 'gram')}\n"
        f"{line('Serat', 'fiber_g', 'gram')}"
    )
    if days == 7:
        message += (
            "\n\nRata-rata energi per hari: "
            f"±{_number(totals['energy_kcal'] / 7)} kkal"
        )
    return message


@router.get("/nutrition-evaluation", response_model=DailySummaryResponse)
def nutrition_evaluation(
    response: Response,
    phone_number: str = Query(min_length=8, max_length=32),
    period: Literal["daily", "weekly"] = "daily",
    repository: NutritionRepository = Depends(get_repository),
    settings: Settings = Depends(get_settings),
) -> DailySummaryResponse:
    try:
        phone_number = phone_number.strip()
        user = repository.get_user(phone_number)
        if user is None:
            return DailySummaryResponse(
                success=True,
                formatted_message="Profil belum tersedia. Ketik *help* untuk memulai.",
            )
        profile = repository.get_profile(phone_number)
        if profile is None or profile.onboarding_step != "complete":
            return DailySummaryResponse(
                success=True,
                formatted_message="Lengkapi profil terlebih dahulu untuk menghitung target nutrisi.",
            )
        return DailySummaryResponse(
            success=True,
            formatted_message=build_evaluation_message(
                repository, user, profile, settings, period
            ),
        )
    except RepositoryError:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return DailySummaryResponse(
            success=False,
            formatted_message="Evaluasi nutrisi belum dapat dibuka.",
            error_message="Penyimpanan tidak tersedia",
        )
