import hashlib
import secrets
from datetime import timedelta

from app.config import Settings
from app.domain import WhitelistEntry, utc_now
from app.repository import NutritionRepository, RepositoryError


def normalize_phone(phone_number: str) -> str:
    return "".join(character for character in phone_number if character.isdigit())


def get_whitelist_entry(
    repository: NutritionRepository, phone_number: str
) -> WhitelistEntry | None:
    entry = repository.get_whitelist(normalize_phone(phone_number))
    return entry if entry and entry.is_enabled else None


def bootstrap_admins(repository: NutritionRepository, settings: Settings) -> None:
    for phone_number in settings.bootstrap_admin_phones:
        normalized = normalize_phone(phone_number)
        if not normalized:
            continue
        entry = repository.get_whitelist(normalized)
        if entry is None:
            entry = WhitelistEntry(
                phone_number=normalized,
                label="Bootstrap Admin",
                is_admin=True,
                is_enabled=True,
                created_by_phone="system",
            )
        else:
            entry.is_admin = True
            entry.is_enabled = True
        repository.save_whitelist(entry)


def create_firebase_login_token(entry: WhitelistEntry) -> str:
    if not entry.is_enabled:
        raise RepositoryError("Akses dashboard tidak tersedia")
    try:
        from firebase_admin import auth

        role = "admin" if entry.is_admin else "user"
        token = auth.create_custom_token(
            f"{role}:{entry.phone_number}",
            {
                "admin": entry.is_admin,
                "phone_number": entry.phone_number,
                "label": entry.label or entry.phone_number,
            },
        )
        return token.decode("utf-8") if isinstance(token, bytes) else str(token)
    except Exception as exc:
        raise RepositoryError(f"Token Firebase tidak dapat dibuat: {exc}") from exc


def _login_code_hash(code: str) -> str:
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


def create_dashboard_login_code(
    repository: NutritionRepository,
    entry: WhitelistEntry,
    settings: Settings,
) -> str:
    if not entry.is_enabled:
        raise RepositoryError("Akses dashboard tidak tersedia")
    code = secrets.token_urlsafe(24)
    custom_token = create_firebase_login_token(entry)
    repository.store_login_code(
        _login_code_hash(code),
        entry.phone_number,
        custom_token,
        utc_now() + timedelta(minutes=settings.admin_login_code_minutes),
    )
    return code


# Alias untuk kompatibilitas kode lama.
create_admin_login_code = create_dashboard_login_code
