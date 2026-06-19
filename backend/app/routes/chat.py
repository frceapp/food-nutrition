import logging
import re

from fastapi import APIRouter, Depends

from app.config import Settings, get_settings
from app.admin_auth import (
    create_admin_login_code,
    get_whitelist_entry,
)
from app.domain import FoodAnalysisLog
from app.repository import NutritionRepository, RepositoryError, get_repository
from app.openrouter_service import (
    OpenRouterError,
    revise_food_analysis,
)
from app.profile_service import (
    INTRO_MESSAGE,
    format_profile,
    get_or_create_profile,
    process_onboarding,
    profile_prompt,
    reset_profile,
)
from app.routes.daily_summary import _utc_day_bounds, build_daily_summary_message
from app.routes.evaluation import build_evaluation_message
from app.runtime_service import maintenance_active, new_usage_event
from app.schemas import ChatRequest, ChatResponse


logger = logging.getLogger(__name__)
router = APIRouter(tags=["chat"])
PHONE_PATTERN = re.compile(r"^\+?\d{8,20}$")
HELP_MESSAGE = """Panduan NUTRIA:

📷 Kirim foto makanan dari atas piring untuk analisis.
✍️ Untuk memperbaiki hasil terakhir, langsung tulis revisinya. Contoh:
*ada tempe 2 potong juga*, *ayamnya ada 2*, atau
*itu bukan ayam tapi daging sapi*.

Command:
* help / tolong — tampilkan panduan
* profil — lihat IMT dan target kalori
* ubah profil — isi ulang data profil
* cek hari ini — total gizi hari ini
* evaluasi harian — evaluasi terhadap target harian
* evaluasi mingguan — evaluasi 7 hari terakhir

Semua hasil merupakan estimasi dan bukan diagnosis medis."""

DAILY_COMMANDS = {
    "cek",
    "info",
    "cek hari ini",
    "info hari ini",
    "cek makanan",
    "info makanan",
    "cek makanan hari ini",
    "info makanan hari ini",
    "cek gizi hari ini",
    "total gizi hari ini",
}
DAILY_EVALUATION_COMMANDS = {
    "evaluasi harian",
    "evaluasi hari ini",
    "cek evaluasi harian",
}
WEEKLY_EVALUATION_COMMANDS = {
    "evaluasi mingguan",
    "evaluasi minggu ini",
    "cek evaluasi mingguan",
}


def _normalize(message: str) -> str:
    return re.sub(r"\s+", " ", message.lower().strip().rstrip("?!.,"))


def _looks_like_revision(message: str) -> bool:
    normalized = _normalize(message)
    return bool(
        normalized.startswith("revisi ")
        or re.search(
            r"\b(bukan|ternyata|seharusnya|harusnya|diganti|ganti|jumlahnya|porsinya|tambah|tambahkan|hapus|hilangkan|kurang)\b",
            normalized,
        )
        or re.search(r"\b(ada|cuma|hanya)\s+\d+(?:[.,]\d+)?\b", normalized)
        or re.search(r"\bada\b.+\bjuga\b", normalized)
        or re.search(r"\bbelum\s+(?:masuk|terdeteksi|tercatat)\b", normalized)
    )


def _latest_food_log(
    repository: NutritionRepository, user_id: str, settings: Settings
) -> FoodAnalysisLog | None:
    utc_start, utc_end = _utc_day_bounds(settings.app_timezone)
    logs = repository.get_logs(
        user_id,
        utc_start,
        utc_end,
        successful_only=True,
        ascending=False,
    )
    return logs[0] if logs else None


def _apply_meal_revision(log: FoodAnalysisLog, analysis, raw_response: str) -> str:
    log.detected_foods_json = [
        item.model_dump(mode="json") for item in analysis.detected_foods
    ]
    log.nutrition_json = analysis.nutrition_estimate.model_dump(mode="json")
    log.plate_analysis_json = analysis.plate_analysis.model_dump(mode="json")
    log.confidence = analysis.confidence
    existing_raw = log.gemini_raw_response or ""
    log.gemini_raw_response = (
        f"{existing_raw}\n\n[MEAL_REVISION]\n{raw_response}"
    ).strip()

    foods_text = "\n".join(
        f"{item.emoji} {item.name} ({item.estimated_portion})"
        for item in analysis.detected_foods
    )
    nutrition = analysis.nutrition_estimate
    message = (
        "Hasil makanan terakhir sudah direvisi ✅\n\n"
        f"{foods_text}\n\n"
        "Total estimasi setelah revisi:\n"
        f"* Energi: ±{nutrition.energy_kcal:g} kkal\n"
        f"* Karbohidrat: ±{nutrition.carbohydrate_g:g} gram\n"
        f"* Protein: ±{nutrition.protein_g:g} gram\n"
        f"* Lemak: ±{nutrition.fat_g:g} gram\n"
        f"* Serat: ±{nutrition.fiber_g:g} gram"
    )
    log.formatted_message = message
    return message


@router.post("/chat", response_model=ChatResponse)
async def chat(
    request: ChatRequest,
    repository: NutritionRepository = Depends(get_repository),
    settings: Settings = Depends(get_settings),
) -> ChatResponse:
    phone_number = request.phone_number.strip()
    if not PHONE_PATTERN.fullmatch(phone_number):
        return ChatResponse(
            success=False,
            formatted_message="Nomor WhatsApp tidak valid",
            error_message="Nomor WhatsApp tidak valid",
        )

    try:
        whitelist_entry = get_whitelist_entry(repository, phone_number)
        if whitelist_entry is None:
            return ChatResponse(
                success=False,
                formatted_message=(
                    "Maaf, nomor ini belum terdaftar untuk menggunakan NUTRIA. "
                    "Hubungi admin untuk meminta akses."
                ),
                error_message="Nomor belum terdaftar",
            )

        runtime = repository.get_runtime_settings()
        usage_event = new_usage_event(repository, phone_number, "chat")

        command = _normalize(request.message)
        if command == "login":
            if not whitelist_entry.is_admin:
                return ChatResponse(
                    success=True,
                    formatted_message="Perintah tidak tersedia untuk nomor ini.",
                )
            login_code = create_admin_login_code(
                repository, whitelist_entry, settings
            )
            login_url = (
                f"{settings.frontend_url.rstrip('/')}/login?code={login_code}"
            )
            return ChatResponse(
                success=True,
                formatted_message=(
                    "Tautan login admin (berlaku singkat dan sekali pakai):\n"
                    f"{login_url}"
                ),
            )

        context = get_or_create_profile(
            repository,
            phone_number,
            request.user_name.strip() if request.user_name else None,
        )
        if context.is_new_profile:
            first_message = INTRO_MESSAGE
            if command in {"help", "tolong", "bantuan", "menu"}:
                first_message = f"{INTRO_MESSAGE}\n\n{HELP_MESSAGE}"
            return ChatResponse(success=True, formatted_message=first_message)

        if command in {"help", "tolong", "bantuan", "menu"}:
            suffix = (
                ""
                if context.profile.onboarding_step == "complete"
                else f"\n\nLanjutkan profil:\n{profile_prompt(context.profile)}"
            )
            return ChatResponse(
                success=True, formatted_message=f"{HELP_MESSAGE}{suffix}"
            )

        if context.profile.onboarding_step != "complete":
            message = process_onboarding(context.profile, request.message)
            repository.save_profile(context.profile)
            return ChatResponse(success=True, formatted_message=message)

        if command == "profil":
            return ChatResponse(
                success=True, formatted_message=format_profile(context.profile)
            )
        if command in {"ubah profil", "reset profil", "perbarui profil"}:
            message = reset_profile(context.profile)
            repository.save_profile(context.profile)
            return ChatResponse(success=True, formatted_message=message)
        if command in DAILY_COMMANDS:
            return ChatResponse(
                success=True,
                formatted_message=build_daily_summary_message(
                    repository, context.user, settings
                ),
            )
        if command in DAILY_EVALUATION_COMMANDS:
            return ChatResponse(
                success=True,
                formatted_message=build_evaluation_message(
                    repository, context.user, context.profile, settings, "daily"
                ),
            )
        if command in WEEKLY_EVALUATION_COMMANDS:
            return ChatResponse(
                success=True,
                formatted_message=build_evaluation_message(
                    repository, context.user, context.profile, settings, "weekly"
                ),
            )

        if _looks_like_revision(request.message):
            usage_event.request_type = "revision"
            if maintenance_active(runtime, settings):
                usage_event.status = "maintenance"
                repository.save_usage_event(usage_event)
                return ChatResponse(
                    success=False,
                    formatted_message=runtime.maintenance_message,
                    error_message="Maintenance aktif",
                )
            latest_log = _latest_food_log(repository, context.user.id, settings)
            if latest_log is None:
                usage_event.status = "no_analysis"
                repository.save_usage_event(usage_event)
                return ChatResponse(
                    success=True,
                    formatted_message=(
                        "Belum ada hasil makanan hari ini yang dapat direvisi. "
                        "Kirim foto makanannya terlebih dahulu."
                    ),
                )
            if latest_log.revision_count >= runtime.max_revisions_per_analysis:
                usage_event.status = "revision_limit"
                repository.save_usage_event(usage_event)
                return ChatResponse(
                    success=False,
                    formatted_message=(
                        "Batas revisi untuk hasil makanan ini sudah tercapai. "
                        "Kirim foto baru untuk membuat analisis berikutnya."
                    ),
                    error_message="Batas revisi tercapai",
                )
            try:
                result = await revise_food_analysis(
                    list(latest_log.detected_foods_json or []),
                    dict(latest_log.nutrition_json or {}),
                    dict(latest_log.plate_analysis_json or {}),
                    request.message,
                    settings,
                )
            except OpenRouterError as exc:
                usage_event.status = "ai_error"
                repository.save_usage_event(usage_event)
                logger.error("Revisi makanan melalui OpenRouter gagal: %s", exc)
                return ChatResponse(
                    success=False,
                    formatted_message=(
                        "Maaf Bunda, revisi makanan belum berhasil diproses. "
                        "Coba lagi beberapa saat ya."
                    ),
                    error_message="Revisi makanan gagal",
                )
            message = _apply_meal_revision(
                latest_log, result.analysis, result.raw_response
            )
            latest_log.revision_count += 1
            latest_log.prompt_tokens += result.usage.prompt_tokens
            latest_log.completion_tokens += result.usage.completion_tokens
            latest_log.total_tokens += result.usage.total_tokens
            latest_log.cost_usd += result.usage.cost_usd
            repository.save_log(latest_log)
            usage_event.status = "success"
            usage_event.prompt_tokens = result.usage.prompt_tokens
            usage_event.completion_tokens = result.usage.completion_tokens
            usage_event.total_tokens = result.usage.total_tokens
            usage_event.cost_usd = result.usage.cost_usd
            repository.save_usage_event(usage_event)
            return ChatResponse(success=True, formatted_message=message)

        return ChatResponse(
            success=True,
            formatted_message=(
                "Perintah belum dikenali. Ketik *help* atau *tolong* "
                "untuk melihat panduan NUTRIA."
            ),
        )
    except RepositoryError:
        logger.exception("Chat gagal mengakses penyimpanan")
        return ChatResponse(
            success=False,
            formatted_message="Maaf Bunda, sistem sedang sibuk. Coba lagi sebentar ya.",
            error_message="Penyimpanan tidak tersedia",
        )
