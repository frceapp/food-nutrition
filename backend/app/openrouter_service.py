import base64
import json
from dataclasses import dataclass, field

import httpx
from pydantic import ValidationError

from app.config import Settings
from app.schemas import MealRevisionAnalysis, NutritionAnalysis


SYSTEM_PROMPT = """Kamu adalah asisten analisis nutrisi makanan bernama NUTRIA.

Analisis foto makanan yang dikirim melalui WhatsApp. Berikan estimasi jenis makanan,
porsi, energi, karbohidrat, protein, lemak, serat, komposisi isi piring,
dan saran makanan berikutnya.

Aturan:
- Semua angka dan porsi adalah estimasi visual, bukan hasil laboratorium.
- Jangan memberi diagnosis atau saran medis.
- Gunakan nama makanan dan bahasa Indonesia yang mudah dipahami.
- Jika porsi tidak pasti, gunakan estimasi konservatif.
- Makanan campuran Indonesia tetap diestimasi dari komponen yang terlihat.
- Jika gambar buram, gelap, bukan makanan, atau makanan tidak jelas, set
  image_quality ke "bad", nutrition_estimate dan plate_analysis ke null.
- Jika ada kemungkinan salah deteksi, gunakan confidence "low" atau "medium".
- Persentase komposisi piring harus berada pada rentang 0 sampai 100.
- Balas hanya dengan JSON valid yang mengikuti schema, tanpa Markdown atau teks lain.
"""


class OpenRouterError(Exception):
    def __init__(self, message: str, raw_response: str | None = None):
        super().__init__(message)
        self.raw_response = raw_response


class OpenRouterAPIError(OpenRouterError):
    pass


class OpenRouterResponseError(OpenRouterError):
    pass


@dataclass(slots=True)
class AIUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float = 0.0


@dataclass(slots=True)
class OpenRouterResult:
    analysis: NutritionAnalysis
    raw_response: str
    usage: AIUsage = field(default_factory=AIUsage)


@dataclass(slots=True)
class MealRevisionResult:
    analysis: MealRevisionAnalysis
    raw_response: str
    usage: AIUsage = field(default_factory=AIUsage)


def _extract_text(payload: dict) -> str:
    try:
        content = payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise OpenRouterResponseError(
            "OpenRouter tidak mengembalikan jawaban",
            json.dumps(payload, ensure_ascii=False),
        ) from exc

    if isinstance(content, str):
        text = content.strip()
    elif isinstance(content, list):
        text = "".join(
            item.get("text", "")
            for item in content
            if isinstance(item, dict) and item.get("type") == "text"
        ).strip()
    else:
        text = ""

    if not text:
        raise OpenRouterResponseError(
            "OpenRouter mengembalikan jawaban kosong",
            json.dumps(payload, ensure_ascii=False),
        )
    return text


def _parse_analysis(raw_text: str) -> NutritionAnalysis:
    cleaned = raw_text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.removeprefix("```json").removeprefix("```")
        cleaned = cleaned.removesuffix("```").strip()
    try:
        analysis = NutritionAnalysis.model_validate(json.loads(cleaned))
    except (json.JSONDecodeError, ValidationError) as exc:
        raise OpenRouterResponseError("Response JSON OpenRouter tidak valid", raw_text) from exc

    if analysis.image_quality == "good" and (
        analysis.nutrition_estimate is None or analysis.plate_analysis is None
    ):
        raise OpenRouterResponseError("Data nutrisi OpenRouter tidak lengkap", raw_text)
    return analysis


def _clean_json_text(raw_text: str) -> str:
    cleaned = raw_text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.removeprefix("```json").removeprefix("```")
        cleaned = cleaned.removesuffix("```").strip()
    return cleaned


async def _request_openrouter(
    messages: list[dict], schema_name: str, schema: dict, settings: Settings
) -> tuple[str, AIUsage]:
    if not settings.openrouter_api_key:
        raise OpenRouterAPIError("OPENROUTER_API_KEY belum dikonfigurasi")

    payload = {
        "model": settings.openrouter_model,
        "messages": messages,
        "temperature": 0.2,
        "max_tokens": settings.openrouter_max_output_tokens,
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": schema_name,
                "strict": True,
                "schema": schema,
            },
        },
        "provider": {"require_parameters": True},
    }
    headers = {
        "Authorization": f"Bearer {settings.openrouter_api_key}",
        "Content-Type": "application/json",
        "X-Title": settings.openrouter_app_name,
    }
    if settings.openrouter_site_url:
        headers["HTTP-Referer"] = settings.openrouter_site_url

    url = f"{settings.openrouter_base_url.rstrip('/')}/chat/completions"
    try:
        async with httpx.AsyncClient(
            timeout=settings.openrouter_timeout_seconds
        ) as client:
            response = await client.post(url, headers=headers, json=payload)
    except httpx.HTTPError as exc:
        raise OpenRouterAPIError("OpenRouter API tidak dapat dihubungi") from exc

    if response.is_error:
        raise OpenRouterAPIError(
            f"OpenRouter API mengembalikan status {response.status_code}",
            response.text[:10000],
        )
    try:
        response_payload = response.json()
    except ValueError as exc:
        raise OpenRouterResponseError(
            "Response OpenRouter bukan JSON", response.text[:10000]
        ) from exc
    usage_data = response_payload.get("usage") or {}
    usage = AIUsage(
        prompt_tokens=int(usage_data.get("prompt_tokens") or 0),
        completion_tokens=int(usage_data.get("completion_tokens") or 0),
        total_tokens=int(usage_data.get("total_tokens") or 0),
        cost_usd=float(usage_data.get("cost") or 0.0),
    )
    return _extract_text(response_payload), usage


async def analyze_food_image(
    image_bytes: bytes, mime_type: str, settings: Settings
) -> OpenRouterResult:
    image_data_url = (
        f"data:{mime_type};base64,{base64.b64encode(image_bytes).decode('ascii')}"
    )
    messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Analisis foto makanan ini."},
                    {"type": "image_url", "image_url": {"url": image_data_url}},
                ],
            },
        ]
    raw_text, usage = await _request_openrouter(
        messages,
        "nutria_food_analysis",
        NutritionAnalysis.model_json_schema(),
        settings,
    )
    return OpenRouterResult(
        analysis=_parse_analysis(raw_text), raw_response=raw_text, usage=usage
    )


async def revise_food_analysis(
    current_foods: list,
    current_nutrition: dict,
    current_plate_analysis: dict | None,
    correction: str,
    settings: Settings,
) -> MealRevisionResult:
    prompt = """Kamu memperbaiki hasil analisis makanan berdasarkan koreksi user.
Kembalikan daftar makanan LENGKAP setelah koreksi dan hitung ulang TOTAL nutrisi seluruh
makanan, bukan hanya selisihnya. Pertahankan item yang tidak dikoreksi. User dapat
menambah makanan yang belum terdeteksi, mengubah jumlah/porsi, mengganti jenis makanan,
atau menghapus item. Terapkan instruksi user lalu buat ulang komposisi isi piring.
Semua nilai tetap estimasi. Jangan memberi diagnosis. Balas hanya JSON sesuai schema."""
    context = {
        "current_detected_foods": current_foods,
        "current_nutrition": current_nutrition,
        "current_plate_analysis": current_plate_analysis,
        "user_correction": correction,
    }
    raw_text, usage = await _request_openrouter(
        [
            {"role": "system", "content": prompt},
            {"role": "user", "content": json.dumps(context, ensure_ascii=False)},
        ],
        "nutria_meal_revision",
        MealRevisionAnalysis.model_json_schema(),
        settings,
    )
    try:
        analysis = MealRevisionAnalysis.model_validate(
            json.loads(_clean_json_text(raw_text))
        )
    except (json.JSONDecodeError, ValidationError) as exc:
        raise OpenRouterResponseError("Response revisi makanan tidak valid", raw_text) from exc
    return MealRevisionResult(analysis=analysis, raw_response=raw_text, usage=usage)
