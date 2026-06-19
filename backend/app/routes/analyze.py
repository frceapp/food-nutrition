import logging
import re
import tempfile
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Response, UploadFile, status

from app.config import Settings, get_settings
from app.admin_auth import get_whitelist_entry
from app.domain import FoodAnalysisLog, User
from app.repository import NutritionRepository, RepositoryError, get_repository
from app.openrouter_service import (
    OpenRouterAPIError,
    OpenRouterResponseError,
    analyze_food_image,
)
from app.image_optimizer import ImageOptimizationError, optimize_for_ai
from app.nutrition_formatter import format_nutrition_message
from app.profile_service import INTRO_MESSAGE, get_or_create_profile, profile_prompt
from app.schemas import AnalyzeFoodResponse, NutritionAnalysis


logger = logging.getLogger(__name__)
router = APIRouter(tags=["nutrition"])

ALLOWED_IMAGE_TYPES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/heic": ".heic",
    "image/heif": ".heif",
}
PHONE_PATTERN = re.compile(r"^\+?\d{8,20}$")

AI_ERROR_MESSAGE = (
    "Maaf Bunda, analisis makanan belum berhasil dilakukan. "
    "Coba kirim ulang beberapa saat lagi ya."
)
INVALID_JSON_MESSAGE = (
    "Maaf Bunda, hasil analisis belum bisa dibaca dengan baik. Coba kirim ulang fotonya ya."
)
OVERSIZE_MESSAGE = (
    "Maaf Bunda, ukuran gambar terlalu besar. Coba kirim foto dengan ukuran lebih kecil ya."
)
INVALID_IMAGE_MESSAGE = "File yang dikirim harus berupa gambar JPEG, PNG, WebP, HEIC, atau HEIF."
UNREADABLE_IMAGE_MESSAGE = "Maaf Bunda, file gambar tidak dapat dibaca. Coba kirim foto lain ya."


def _save_log(
    repository: NutritionRepository,
    user: User,
    *,
    analysis: NutritionAnalysis | None = None,
    raw_response: str | None = None,
    formatted_message: str | None = None,
    log_status: str,
    error_message: str | None = None,
) -> None:
    log = FoodAnalysisLog(
        user_id=user.id,
        image_path=None,
        detected_foods_json=(
            [item.model_dump(mode="json") for item in analysis.detected_foods]
            if analysis
            else None
        ),
        nutrition_json=(
            analysis.nutrition_estimate.model_dump(mode="json")
            if analysis and analysis.nutrition_estimate
            else None
        ),
        plate_analysis_json=(
            analysis.plate_analysis.model_dump(mode="json")
            if analysis and analysis.plate_analysis
            else None
        ),
        gemini_raw_response=raw_response,
        formatted_message=formatted_message,
        confidence=analysis.confidence if analysis else None,
        status=log_status,
        error_message=error_message,
    )
    repository.add_log(log)


def _safe_save_failure(
    repository: NutritionRepository, user: User, **kwargs
) -> None:
    try:
        _save_log(repository, user, **kwargs)
    except RepositoryError:
        logger.exception("Gagal menyimpan log analisis")


@router.post("/analyze-food", response_model=AnalyzeFoodResponse)
async def analyze_food(
    response: Response,
    phone_number: Annotated[str, Form(min_length=8, max_length=32)],
    image: Annotated[UploadFile, File()],
    user_name: Annotated[str | None, Form(max_length=120)] = None,
    repository: NutritionRepository = Depends(get_repository),
    settings: Settings = Depends(get_settings),
) -> AnalyzeFoodResponse:
    phone_number = phone_number.strip()
    if not PHONE_PATTERN.fullmatch(phone_number):
        response.status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
        return AnalyzeFoodResponse(success=False, error_message="Nomor WhatsApp tidak valid")

    if get_whitelist_entry(repository, phone_number) is None:
        response.status_code = status.HTTP_403_FORBIDDEN
        return AnalyzeFoodResponse(
            success=False,
            error_message=(
                "Maaf, nomor ini belum terdaftar untuk menggunakan NUTRIA. "
                "Hubungi admin untuk meminta akses."
            ),
        )

    mime_type = (image.content_type or "").lower()
    if mime_type not in ALLOWED_IMAGE_TYPES:
        response.status_code = status.HTTP_415_UNSUPPORTED_MEDIA_TYPE
        return AnalyzeFoodResponse(success=False, error_message=INVALID_IMAGE_MESSAGE)

    image_bytes = await image.read(settings.max_image_size_bytes + 1)
    await image.close()
    if len(image_bytes) > settings.max_image_size_bytes:
        response.status_code = status.HTTP_413_REQUEST_ENTITY_TOO_LARGE
        return AnalyzeFoodResponse(success=False, error_message=OVERSIZE_MESSAGE)
    if not image_bytes:
        response.status_code = status.HTTP_400_BAD_REQUEST
        return AnalyzeFoodResponse(success=False, error_message="File gambar kosong")

    try:
        context = get_or_create_profile(
            repository, phone_number, user_name.strip() if user_name else None
        )
    except RepositoryError:
        logger.exception("Penyimpanan tidak dapat menyimpan data user")
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return AnalyzeFoodResponse(
            success=False, error_message="Layanan penyimpanan sedang tidak tersedia"
        )

    if context.is_new_profile:
        return AnalyzeFoodResponse(success=True, formatted_message=INTRO_MESSAGE)
    if context.profile.onboarding_step != "complete":
        return AnalyzeFoodResponse(
            success=True,
            formatted_message=(
                "Profil perlu dilengkapi sebelum foto dianalisis.\n\n"
                f"{profile_prompt(context.profile)}"
            ),
        )
    user = context.user

    temp_path: Path | None = None
    try:
        optimized_image = optimize_for_ai(image_bytes, mime_type, settings)
        logger.info(
            "Optimasi gambar: %s -> %s byte, dimensi=%sx%s, aktif=%s",
            len(image_bytes),
            len(optimized_image.data),
            optimized_image.width or "asli",
            optimized_image.height or "asli",
            optimized_image.was_optimized,
        )

        settings.upload_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            dir=settings.upload_dir,
            suffix=ALLOWED_IMAGE_TYPES[optimized_image.mime_type],
            delete=False,
        ) as temp_file:
            temp_file.write(optimized_image.data)
            temp_path = Path(temp_file.name)

        result = await analyze_food_image(
            optimized_image.data, optimized_image.mime_type, settings
        )
        formatted_message = format_nutrition_message(result.analysis)
        _save_log(
            repository,
            user,
            analysis=result.analysis,
            raw_response=result.raw_response,
            formatted_message=formatted_message,
            log_status="success",
        )
        return AnalyzeFoodResponse(
            success=True,
            formatted_message=formatted_message,
            gemini_json=result.analysis,
        )
    except ImageOptimizationError as exc:
        logger.warning("Gambar tidak dapat dioptimasi: %s", exc)
        _safe_save_failure(
            repository,
            user,
            log_status="invalid_image",
            error_message=str(exc),
        )
        response.status_code = status.HTTP_400_BAD_REQUEST
        return AnalyzeFoodResponse(success=False, error_message=UNREADABLE_IMAGE_MESSAGE)
    except OpenRouterResponseError as exc:
        logger.warning("Response OpenRouter tidak valid: %s", exc)
        _safe_save_failure(
            repository,
            user,
            raw_response=exc.raw_response,
            log_status="invalid_response",
            error_message=str(exc),
        )
        response.status_code = status.HTTP_502_BAD_GATEWAY
        return AnalyzeFoodResponse(success=False, error_message=INVALID_JSON_MESSAGE)
    except OpenRouterAPIError as exc:
        logger.error("OpenRouter API gagal: %s", exc)
        _safe_save_failure(
            repository,
            user,
            raw_response=exc.raw_response,
            log_status="openrouter_error",
            error_message=str(exc),
        )
        response.status_code = status.HTTP_502_BAD_GATEWAY
        return AnalyzeFoodResponse(success=False, error_message=AI_ERROR_MESSAGE)
    except (RepositoryError, ValueError):
        logger.exception("Analisis gagal diproses")
        response.status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
        return AnalyzeFoodResponse(success=False, error_message=AI_ERROR_MESSAGE)
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
