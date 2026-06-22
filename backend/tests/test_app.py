import asyncio
import hashlib
import os
import tempfile
import unittest
from datetime import timedelta
from io import BytesIO
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from unittest.mock import patch

os.environ["APP_ENV"] = "test"
os.environ["OPENROUTER_API_KEY"] = "test-key"

from fastapi import Response, UploadFile
from PIL import Image
from starlette.datastructures import Headers

from app.config import Settings, get_settings
from app.confirmation_service import auto_close_confirmation
from app.domain import FoodAnalysisLog, User, UserProfile, WhitelistEntry, utc_now
from app.openrouter_service import MealRevisionResult, OpenRouterResult
from app.repository import InMemoryRepository, get_repository
from app.routes.analyze import analyze_food
from app.routes.chat import _looks_like_revision, chat as chat_route
from app.routes.daily_summary import daily_summary
from app.routes.evaluation import nutrition_evaluation
from app.schemas import ChatRequest, MealRevisionAnalysis, NutritionAnalysis
from main import app, health


SAMPLE_ANALYSIS = NutritionAnalysis.model_validate({
    "image_quality": "good",
    "detected_foods": [{
        "name": "Nasi putih",
        "estimated_portion": "150 gram",
        "category": "karbohidrat",
        "emoji": "nasi",
    }],
    "nutrition_estimate": {
        "energy_kcal": 300,
        "carbohydrate_g": 50,
        "protein_g": 10,
        "fat_g": 5,
        "fiber_g": 3,
        "calcium_note": "Perlu ditingkatkan",
    },
    "plate_analysis": {
        "carbohydrate_percent": 50,
        "carbohydrate_status": "berlebih",
        "protein_percent": 20,
        "protein_status": "cukup",
        "vegetable_percent": 30,
        "vegetable_status": "cukup",
        "fruit_percent": 0,
        "fruit_status": "belum ada",
    },
    "suggestion": "Tambahkan satu porsi buah.",
    "confidence": "medium",
})


def image_upload(content: bytes, content_type: str) -> UploadFile:
    return UploadFile(
        file=BytesIO(content),
        filename="meal.jpg",
        headers=Headers({"content-type": content_type}),
    )


def jpeg_bytes(size: tuple[int, int] = (1600, 900)) -> bytes:
    output = BytesIO()
    Image.new("RGB", size, (210, 140, 80)).save(output, format="JPEG", quality=90)
    return output.getvalue()


class NutriaAppTests(unittest.TestCase):
    def setUp(self) -> None:
        repository = get_repository()
        assert isinstance(repository, InMemoryRepository)
        repository.clear()
        self.repository = repository
        for phone in ["628123456789", "628777777777", "628666666666", "628555555555"]:
            repository.save_whitelist(WhitelistEntry(phone_number=phone, label="Test User"))
        repository.save_whitelist(WhitelistEntry(
            phone_number="628000000000",
            label="Test Admin",
            is_admin=True,
        ))

    def seed_complete_profile(self, phone: str, name: str = "Dewi") -> None:
        self.repository.save_user(User(phone_number=phone, name=name))
        self.repository.save_profile(UserProfile(
            user_id=phone,
            weight_kg=60,
            height_cm=160,
            activity_level="ringan",
            bmi=23.4,
            bmi_category="normal",
            daily_calorie_target=1800,
            onboarding_step="complete",
        ))

    def test_health_and_no_admin_api(self) -> None:
        self.assertEqual(health()["status"], "ok")
        paths = {getattr(route, "path", None) for route in app.routes}
        self.assertIn("/health", paths)
        self.assertFalse(any(path and path.startswith("/admin/") for path in paths))

    def test_firestore_settings_and_cors(self) -> None:
        settings = Settings(
            _env_file=None,
            firebase_project_id="nutria-test",
            frontend_url="https://dashboard.example.com/",
            cors_origins="https://admin.example.com,http://localhost:5173/",
        )
        self.assertEqual(settings.firebase_project_id, "nutria-test")
        self.assertEqual(settings.allowed_cors_origins[-1], "https://dashboard.example.com")

    def test_daily_and_weekly_totals(self) -> None:
        phone = "628777777777"
        self.seed_complete_profile(phone)
        for energy, protein, food in [(300, 10, "Nasi"), (250, 20, "Ayam")]:
            self.repository.add_log(FoodAnalysisLog(
                user_id=phone,
                status="success",
                detected_foods_json=[{"name": food, "estimated_portion": "1 porsi", "emoji": "meal"}],
                nutrition_json={
                    "energy_kcal": energy,
                    "carbohydrate_g": 30,
                    "protein_g": protein,
                    "fat_g": 5,
                    "fiber_g": 3,
                },
            ))

        summary = daily_summary(Response(), phone, self.repository, get_settings())
        daily = nutrition_evaluation(Response(), phone, "daily", self.repository, get_settings())
        weekly = nutrition_evaluation(Response(), phone, "weekly", self.repository, get_settings())

        self.assertIn("550", summary.formatted_message)
        self.assertIn("30", summary.formatted_message)
        self.assertIn("550/1800", daily.formatted_message)
        self.assertIn("7 hari", weekly.formatted_message)

    def test_short_login_code_stores_temporary_token(self) -> None:
        with patch(
            "app.admin_auth.create_firebase_login_token",
            return_value="firebase-custom-token",
        ):
            result = asyncio.run(chat_route(
                ChatRequest(phone_number="628000000000", user_name="Admin", message="login"),
                self.repository,
                get_settings(),
            ))
        login_url = result.formatted_message.splitlines()[-1]
        code = parse_qs(urlparse(login_url).query)["code"][0]
        self.assertNotIn("eyJ", login_url)
        self.assertLess(len(login_url), 160)
        code_hash = hashlib.sha256(code.encode("utf-8")).hexdigest()
        stored = self.repository.login_codes[code_hash]
        self.assertEqual(stored[0], "628000000000")
        self.assertEqual(stored[1], "firebase-custom-token")
        self.assertGreater(stored[2] - utc_now(), timedelta(minutes=59))

    def test_regular_user_can_request_dashboard_login(self) -> None:
        with patch(
            "app.admin_auth.create_firebase_login_token",
            return_value="firebase-user-token",
        ):
            result = asyncio.run(chat_route(
                ChatRequest(
                    phone_number="628123456789",
                    user_name="Sari",
                    message="dashboard",
                ),
                self.repository,
                get_settings(),
            ))

        self.assertTrue(result.success)
        self.assertIn("/login?code=", result.formatted_message)
        code = parse_qs(urlparse(result.formatted_message.splitlines()[-1]).query)["code"][0]
        stored = self.repository.login_codes[
            hashlib.sha256(code.encode("utf-8")).hexdigest()
        ]
        self.assertEqual(stored[0], "628123456789")
        self.assertEqual(stored[1], "firebase-user-token")

    def test_unlisted_number_is_rejected(self) -> None:
        result = asyncio.run(chat_route(
            ChatRequest(phone_number="628999999999", user_name="Unknown", message="halo"),
            self.repository,
            get_settings(),
        ))
        self.assertFalse(result.success)
        self.assertIn("belum terdaftar", result.formatted_message)

    def test_onboarding_and_help(self) -> None:
        phone = "628666666666"

        async def send(message: str):
            return await chat_route(
                ChatRequest(phone_number=phone, user_name="Maya", message=message),
                self.repository,
                get_settings(),
            )

        intro = asyncio.run(send("halo"))
        weight = asyncio.run(send("60"))
        height = asyncio.run(send("160"))
        completed = asyncio.run(send("2"))
        help_result = asyncio.run(send("help"))
        removed_command = asyncio.run(send("cek hari ini"))
        self.assertIn("NUTRIA", intro.formatted_message)
        self.assertIn("tinggi badan", weight.formatted_message)
        self.assertIn("pola aktivitas", height.formatted_message)
        self.assertIn("23.4", completed.formatted_message)
        self.assertIn("dashboard", help_result.formatted_message)
        self.assertNotIn("evaluasi mingguan", help_result.formatted_message)
        self.assertIn("tersedia di dashboard", removed_command.formatted_message)

    def test_dashboard_only_command_does_not_trigger_pending_revision(self) -> None:
        phone = "628555555555"
        self.seed_complete_profile(phone)
        log = self.repository.add_log(FoodAnalysisLog(
            user_id=phone,
            status="success",
            detected_foods_json=[],
            nutrition_json={"energy_kcal": 300},
            awaiting_confirmation=True,
        ))

        with patch("app.routes.chat.revise_food_analysis") as revise:
            result = asyncio.run(chat_route(
                ChatRequest(
                    phone_number=phone,
                    user_name="Dewi",
                    message="evaluasi mingguan",
                ),
                self.repository,
                get_settings(),
            ))

        revise.assert_not_called()
        self.assertIn("tersedia di dashboard", result.formatted_message)
        self.assertEqual(self.repository.logs[log.id].revision_count, 0)

    def test_whatsapp_name_does_not_overwrite_saved_profile_name(self) -> None:
        phone = "628555555555"
        self.seed_complete_profile(phone, "Nama Dashboard")

        asyncio.run(chat_route(
            ChatRequest(
                phone_number=phone,
                user_name="Nama WhatsApp",
                message="profil",
            ),
            self.repository,
            get_settings(),
        ))

        self.assertEqual(self.repository.users[phone].name, "Nama Dashboard")

    def test_free_form_meal_revision(self) -> None:
        self.assertTrue(_looks_like_revision("itu bukan ayam tapi daging sapi"))
        phone = "628555555555"
        self.seed_complete_profile(phone, "Rina")
        log = self.repository.add_log(FoodAnalysisLog(
            user_id=phone,
            status="success",
            detected_foods_json=[],
            nutrition_json={"energy_kcal": 300, "carbohydrate_g": 40, "protein_g": 10, "fat_g": 8, "fiber_g": 2},
        ))
        revision = MealRevisionAnalysis.model_validate({
            "detected_foods": [{"name": "Daging sapi", "estimated_portion": "2 potong", "category": "protein", "emoji": "sapi"}],
            "nutrition_estimate": {"energy_kcal": 450, "carbohydrate_g": 40, "protein_g": 28, "fat_g": 18, "fiber_g": 2, "calcium_note": "Belum pasti"},
            "plate_analysis": {"carbohydrate_percent": 50, "carbohydrate_status": "cukup", "protein_percent": 35, "protein_status": "cukup", "vegetable_percent": 15, "vegetable_status": "kurang", "fruit_percent": 0, "fruit_status": "belum ada"},
            "confidence": "medium",
        })

        async def fake_revision(*_args, **_kwargs):
            return MealRevisionResult(revision, revision.model_dump_json())

        with patch("app.routes.chat.revise_food_analysis", new=fake_revision):
            result = asyncio.run(chat_route(
                ChatRequest(phone_number=phone, user_name="Rina", message="itu bukan ayam tapi daging sapi"),
                self.repository,
                get_settings(),
            ))
        self.assertIn("direvisi", result.formatted_message)
        self.assertEqual(self.repository.logs[log.id].nutrition_json["energy_kcal"], 450)

    def test_analyze_food_optimizes_image(self) -> None:
        phone = "628123456789"
        self.seed_complete_profile(phone, "Sari")
        optimized: dict = {}

        async def fake_analyze(image: bytes, mime_type: str, *_args):
            optimized["mime_type"] = mime_type
            with Image.open(BytesIO(image)) as sent_image:
                optimized["size"] = sent_image.size
            return OpenRouterResult(SAMPLE_ANALYSIS, SAMPLE_ANALYSIS.model_dump_json())

        settings = get_settings()
        settings.upload_dir = Path(tempfile.gettempdir())
        response = Response()
        with patch("app.routes.analyze.analyze_food_image", new=fake_analyze):
            result = asyncio.run(analyze_food(
                response,
                phone,
                image_upload(jpeg_bytes(), "image/jpeg"),
                "Sari",
                self.repository,
                settings,
            ))
        self.assertTrue(result.success)
        self.assertEqual(optimized["mime_type"], "image/jpeg")
        self.assertLessEqual(max(optimized["size"]), 1024)
        self.assertEqual(len(self.repository.logs), 1)
        saved_log = next(iter(self.repository.logs.values()))
        self.assertTrue(saved_log.awaiting_confirmation)
        self.assertIn("balas *YA*", result.formatted_message)

    def test_rejects_non_image(self) -> None:
        response = Response()
        result = asyncio.run(analyze_food(
            response,
            "628123456789",
            image_upload(b"not-image", "text/plain"),
            None,
            self.repository,
            get_settings(),
        ))
        self.assertFalse(result.success)
        self.assertEqual(response.status_code, 415)

    def test_manual_maintenance_blocks_image_ai_request(self) -> None:
        phone = "628123456789"
        self.seed_complete_profile(phone)
        self.repository.runtime_settings.maintenance_manual = True
        ai_called = False

        async def fake_analyze(*_args, **_kwargs):
            nonlocal ai_called
            ai_called = True
            return OpenRouterResult(SAMPLE_ANALYSIS, SAMPLE_ANALYSIS.model_dump_json())

        response = Response()
        with patch("app.routes.analyze.analyze_food_image", new=fake_analyze):
            result = asyncio.run(analyze_food(
                response,
                phone,
                image_upload(jpeg_bytes((100, 100)), "image/jpeg"),
                "Sari",
                self.repository,
                get_settings(),
            ))

        self.assertFalse(result.success)
        self.assertEqual(response.status_code, 503)
        self.assertFalse(ai_called)

    def test_revision_limit_blocks_ai_request(self) -> None:
        phone = "628555555555"
        self.seed_complete_profile(phone)
        self.repository.runtime_settings.max_revisions_per_analysis = 0
        self.repository.add_log(FoodAnalysisLog(
            user_id=phone,
            status="success",
            detected_foods_json=[{"name": "Ayam", "estimated_portion": "1 potong"}],
            nutrition_json={"energy_kcal": 200},
        ))
        ai_called = False

        async def fake_revision(*_args, **_kwargs):
            nonlocal ai_called
            ai_called = True

        with patch("app.routes.chat.revise_food_analysis", new=fake_revision):
            result = asyncio.run(chat_route(
                ChatRequest(
                    phone_number=phone,
                    user_name="Rina",
                    message="ayamnya ada 2",
                ),
                self.repository,
                get_settings(),
            ))

        self.assertFalse(result.success)
        self.assertIn("Batas revisi", result.formatted_message)
        self.assertFalse(ai_called)

    def test_pending_analysis_treats_free_text_as_revision_until_yes(self) -> None:
        phone = "628555555555"
        self.seed_complete_profile(phone)
        log = self.repository.add_log(FoodAnalysisLog(
            user_id=phone,
            status="success",
            awaiting_confirmation=True,
            detected_foods_json=[{"name": "Nasi", "estimated_portion": "1 centong"}],
            nutrition_json={"energy_kcal": 200},
        ))
        revision = MealRevisionAnalysis.model_validate({
            "detected_foods": [{"name": "Nasi", "estimated_portion": "2 centong", "category": "karbohidrat", "emoji": "nasi"}],
            "nutrition_estimate": {"energy_kcal": 400, "carbohydrate_g": 80, "protein_g": 8, "fat_g": 1, "fiber_g": 2, "calcium_note": "Belum pasti"},
            "plate_analysis": {"carbohydrate_percent": 100, "carbohydrate_status": "berlebih", "protein_percent": 0, "protein_status": "kurang", "vegetable_percent": 0, "vegetable_status": "kurang", "fruit_percent": 0, "fruit_status": "belum ada"},
            "confidence": "medium",
        })
        received_correction = ""

        async def fake_revision(*args, **_kwargs):
            nonlocal received_correction
            received_correction = args[3]
            return MealRevisionResult(revision, revision.model_dump_json())

        with patch("app.routes.chat.revise_food_analysis", new=fake_revision):
            revised = asyncio.run(chat_route(
                ChatRequest(
                    phone_number=phone,
                    user_name="Rina",
                    message="nasi putih 2 centong",
                ),
                self.repository,
                get_settings(),
            ))

        self.assertEqual(received_correction, "nasi putih 2 centong")
        self.assertIn("balas *YA*", revised.formatted_message)
        self.assertTrue(self.repository.logs[log.id].awaiting_confirmation)

        confirmed = asyncio.run(chat_route(
            ChatRequest(phone_number=phone, user_name="Rina", message="YA"),
            self.repository,
            get_settings(),
        ))
        self.assertTrue(confirmed.success)
        self.assertIn("dikonfirmasi", confirmed.formatted_message)
        self.assertFalse(self.repository.logs[log.id].awaiting_confirmation)

    def test_pending_analysis_auto_confirms_after_timeout(self) -> None:
        phone = "628555555555"
        self.seed_complete_profile(phone)
        log = self.repository.add_log(FoodAnalysisLog(
            user_id=phone,
            status="success",
            awaiting_confirmation=True,
            detected_foods_json=[{"name": "Nasi", "estimated_portion": "1 centong"}],
            nutrition_json={"energy_kcal": 200},
            updated_at=utc_now() - timedelta(minutes=2),
        ))
        ai_called = False

        async def fake_revision(*_args, **_kwargs):
            nonlocal ai_called
            ai_called = True

        with patch("app.routes.chat.revise_food_analysis", new=fake_revision):
            result = asyncio.run(chat_route(
                ChatRequest(phone_number=phone, user_name="Rina", message="profil"),
                self.repository,
                get_settings(),
            ))

        self.assertTrue(result.success)
        self.assertIn("IMT", result.formatted_message)
        self.assertFalse(ai_called)
        self.assertFalse(self.repository.logs[log.id].awaiting_confirmation)

    def test_no_rejects_analysis_without_deleting_admin_log(self) -> None:
        phone = "628555555555"
        self.seed_complete_profile(phone)
        log = self.repository.add_log(FoodAnalysisLog(
            user_id=phone,
            status="success",
            awaiting_confirmation=True,
            detected_foods_json=[{"name": "Nasi", "estimated_portion": "1 centong"}],
            nutrition_json={
                "energy_kcal": 200,
                "carbohydrate_g": 40,
                "protein_g": 4,
                "fat_g": 1,
                "fiber_g": 1,
            },
        ))

        result = asyncio.run(chat_route(
            ChatRequest(phone_number=phone, user_name="Rina", message="TIDAK"),
            self.repository,
            get_settings(),
        ))

        saved = self.repository.logs[log.id]
        self.assertTrue(result.success)
        self.assertIn("tidak dihitung", result.formatted_message)
        self.assertEqual(saved.status, "rejected")
        self.assertFalse(saved.awaiting_confirmation)
        self.assertEqual(
            self.repository.get_logs(phone, successful_only=True),
            [],
        )
        self.assertIn(log.id, self.repository.logs)

    def test_confirmation_is_closed_by_background_timer(self) -> None:
        log = self.repository.add_log(FoodAnalysisLog(
            user_id="628555555555",
            status="success",
            awaiting_confirmation=True,
            nutrition_json={"energy_kcal": 200},
        ))

        asyncio.run(auto_close_confirmation(
            self.repository,
            log.user_id,
            log.id,
            log.updated_at,
            0,
        ))

        self.assertFalse(self.repository.logs[log.id].awaiting_confirmation)


if __name__ == "__main__":
    unittest.main()
