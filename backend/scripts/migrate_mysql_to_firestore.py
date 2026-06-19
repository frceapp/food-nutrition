"""Migrasi satu kali dari database MySQL lama ke Cloud Firestore.

Jalankan dari folder backend setelah variabel MYSQL_* dan FIREBASE_* diisi:
    python scripts/migrate_mysql_to_firestore.py

Dokumen log memakai ID ``mysql-<id>`` sehingga skrip aman dijalankan ulang.
"""

from __future__ import annotations

import sys
from pathlib import Path

from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.domain import (  # noqa: E402
    FoodAnalysisLog,
    User,
    UserProfile,
    WhitelistEntry,
    ensure_utc,
    utc_now,
)
from app.models import (  # noqa: E402
    FoodAnalysisLog as SqlFoodAnalysisLog,
    User as SqlUser,
    UserProfile as SqlUserProfile,
    WhitelistEntry as SqlWhitelistEntry,
)
from app.repository import FirestoreRepository  # noqa: E402


def timestamp(value):
    return ensure_utc(value) or utc_now()


def main() -> None:
    repository = FirestoreRepository(get_settings())
    counts = {"users": 0, "profiles": 0, "logs": 0, "whitelist": 0}

    with SessionLocal() as session:
        sql_users = list(session.scalars(select(SqlUser)))
        phone_by_id = {user.id: user.phone_number for user in sql_users}

        for row in sql_users:
            repository.save_user(User(
                phone_number=row.phone_number,
                name=row.name,
                created_at=timestamp(row.created_at),
                updated_at=timestamp(row.updated_at),
            ))
            counts["users"] += 1

        for row in session.scalars(select(SqlUserProfile)):
            phone = phone_by_id.get(row.user_id)
            if not phone:
                print(f"Lewati profile {row.id}: user {row.user_id} tidak ditemukan")
                continue
            repository.save_profile(UserProfile(
                user_id=phone,
                weight_kg=row.weight_kg,
                height_cm=row.height_cm,
                activity_level=row.activity_level,
                bmi=row.bmi,
                bmi_category=row.bmi_category,
                daily_calorie_target=row.daily_calorie_target,
                onboarding_step=row.onboarding_step,
                completed_at=ensure_utc(row.completed_at),
                created_at=timestamp(row.created_at),
                updated_at=timestamp(row.updated_at),
            ))
            counts["profiles"] += 1

        for row in session.scalars(select(SqlFoodAnalysisLog)):
            phone = phone_by_id.get(row.user_id)
            if not phone:
                print(f"Lewati log {row.id}: user {row.user_id} tidak ditemukan")
                continue
            repository.add_log(FoodAnalysisLog(
                id=f"mysql-{row.id}",
                user_id=phone,
                image_path=row.image_path,
                detected_foods_json=row.detected_foods_json,
                nutrition_json=row.nutrition_json,
                plate_analysis_json=row.plate_analysis_json,
                gemini_raw_response=row.gemini_raw_response,
                formatted_message=row.formatted_message,
                confidence=row.confidence,
                status=row.status,
                error_message=row.error_message,
                created_at=timestamp(row.created_at),
                updated_at=timestamp(row.updated_at),
            ))
            counts["logs"] += 1

        for row in session.scalars(select(SqlWhitelistEntry)):
            repository.save_whitelist(WhitelistEntry(
                phone_number=row.phone_number,
                label=row.label,
                is_admin=row.is_admin,
                is_enabled=row.is_enabled,
                created_by_phone=row.created_by_phone,
                created_at=timestamp(row.created_at),
                updated_at=timestamp(row.updated_at),
            ))
            counts["whitelist"] += 1

    print("Migrasi selesai: " + ", ".join(f"{key}={value}" for key, value in counts.items()))


if __name__ == "__main__":
    main()
