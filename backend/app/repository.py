from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from functools import lru_cache
from uuid import uuid4

from app.config import Settings, get_settings
from app.domain import (
    FoodAnalysisLog,
    User,
    UserProfile,
    WhitelistEntry,
    log_from_document,
    profile_from_document,
    to_document,
    user_from_document,
    utc_now,
    whitelist_from_document,
)


class RepositoryError(RuntimeError):
    pass


class NutritionRepository(ABC):
    @abstractmethod
    def get_user(self, phone_number: str) -> User | None: ...

    @abstractmethod
    def save_user(self, user: User) -> None: ...

    @abstractmethod
    def get_profile(self, phone_number: str) -> UserProfile | None: ...

    @abstractmethod
    def save_profile(self, profile: UserProfile) -> None: ...

    @abstractmethod
    def add_log(self, log: FoodAnalysisLog) -> FoodAnalysisLog: ...

    @abstractmethod
    def save_log(self, log: FoodAnalysisLog) -> None: ...

    @abstractmethod
    def get_logs(
        self,
        phone_number: str,
        start: datetime | None = None,
        end: datetime | None = None,
        *,
        successful_only: bool = False,
        ascending: bool = True,
    ) -> list[FoodAnalysisLog]: ...

    @abstractmethod
    def get_whitelist(self, phone_number: str) -> WhitelistEntry | None: ...

    @abstractmethod
    def save_whitelist(self, entry: WhitelistEntry) -> None: ...

    @abstractmethod
    def store_login_code(
        self,
        code_hash: str,
        phone_number: str,
        custom_token: str,
        expires_at: datetime,
    ) -> None: ...


class InMemoryRepository(NutritionRepository):
    def __init__(self) -> None:
        self.users: dict[str, User] = {}
        self.profiles: dict[str, UserProfile] = {}
        self.logs: dict[str, FoodAnalysisLog] = {}
        self.whitelist: dict[str, WhitelistEntry] = {}
        self.login_codes: dict[str, tuple[str, str, datetime]] = {}

    def clear(self) -> None:
        self.users.clear()
        self.profiles.clear()
        self.logs.clear()
        self.whitelist.clear()
        self.login_codes.clear()

    def get_user(self, phone_number: str) -> User | None:
        return self.users.get(phone_number)

    def save_user(self, user: User) -> None:
        user.updated_at = utc_now()
        self.users[user.phone_number] = user

    def get_profile(self, phone_number: str) -> UserProfile | None:
        return self.profiles.get(phone_number)

    def save_profile(self, profile: UserProfile) -> None:
        profile.updated_at = utc_now()
        self.profiles[profile.user_id] = profile

    def add_log(self, log: FoodAnalysisLog) -> FoodAnalysisLog:
        log.id = log.id or uuid4().hex
        self.logs[log.id] = log
        return log

    def save_log(self, log: FoodAnalysisLog) -> None:
        if not log.id:
            self.add_log(log)
            return
        log.updated_at = utc_now()
        self.logs[log.id] = log

    def get_logs(
        self,
        phone_number: str,
        start: datetime | None = None,
        end: datetime | None = None,
        *,
        successful_only: bool = False,
        ascending: bool = True,
    ) -> list[FoodAnalysisLog]:
        logs = [log for log in self.logs.values() if log.user_id == phone_number]
        if start:
            logs = [log for log in logs if log.created_at >= start]
        if end:
            logs = [log for log in logs if log.created_at < end]
        if successful_only:
            logs = [
                log
                for log in logs
                if log.status == "success" and log.nutrition_json is not None
            ]
        return sorted(logs, key=lambda log: log.created_at, reverse=not ascending)

    def get_whitelist(self, phone_number: str) -> WhitelistEntry | None:
        return self.whitelist.get(phone_number)

    def save_whitelist(self, entry: WhitelistEntry) -> None:
        entry.updated_at = utc_now()
        self.whitelist[entry.phone_number] = entry

    def store_login_code(
        self,
        code_hash: str,
        phone_number: str,
        custom_token: str,
        expires_at: datetime,
    ) -> None:
        self.login_codes[code_hash] = (phone_number, custom_token, expires_at)


class FirestoreRepository(NutritionRepository):
    def __init__(self, settings: Settings) -> None:
        if not settings.firebase_project_id:
            raise RepositoryError("FIREBASE_PROJECT_ID wajib diisi")
        try:
            import firebase_admin
            from firebase_admin import credentials, firestore

            if not firebase_admin._apps:
                credential = (
                    credentials.Certificate(str(settings.firebase_credentials_path))
                    if settings.firebase_credentials_path
                    else None
                )
                firebase_admin.initialize_app(
                    credential,
                    {"projectId": settings.firebase_project_id},
                )
            self.client = firestore.client()
            self.firestore = firestore
        except Exception as exc:
            raise RepositoryError(f"Firebase tidak dapat diinisialisasi: {exc}") from exc

    def _get(self, collection: str, document_id: str) -> dict | None:
        try:
            snapshot = self.client.collection(collection).document(document_id).get()
            return snapshot.to_dict() if snapshot.exists else None
        except Exception as exc:
            raise RepositoryError(str(exc)) from exc

    def _set(self, collection: str, document_id: str, document: dict) -> None:
        try:
            self.client.collection(collection).document(document_id).set(document)
        except Exception as exc:
            raise RepositoryError(str(exc)) from exc

    def get_user(self, phone_number: str) -> User | None:
        document = self._get("users", phone_number)
        return user_from_document(document) if document else None

    def save_user(self, user: User) -> None:
        user.updated_at = utc_now()
        self._set("users", user.phone_number, to_document(user))

    def get_profile(self, phone_number: str) -> UserProfile | None:
        document = self._get("user_profiles", phone_number)
        return profile_from_document(document) if document else None

    def save_profile(self, profile: UserProfile) -> None:
        profile.updated_at = utc_now()
        self._set("user_profiles", profile.user_id, to_document(profile))

    def add_log(self, log: FoodAnalysisLog) -> FoodAnalysisLog:
        try:
            collection = self.client.collection("food_analysis_logs")
            reference = collection.document(log.id) if log.id else collection.document()
            log.id = reference.id
            reference.set(to_document(log))
            return log
        except Exception as exc:
            raise RepositoryError(str(exc)) from exc

    def save_log(self, log: FoodAnalysisLog) -> None:
        if not log.id:
            self.add_log(log)
            return
        log.updated_at = utc_now()
        self._set("food_analysis_logs", log.id, to_document(log))

    def get_logs(
        self,
        phone_number: str,
        start: datetime | None = None,
        end: datetime | None = None,
        *,
        successful_only: bool = False,
        ascending: bool = True,
    ) -> list[FoodAnalysisLog]:
        try:
            from google.cloud.firestore_v1.base_query import FieldFilter

            query = self.client.collection("food_analysis_logs").where(
                filter=FieldFilter("user_id", "==", phone_number)
            )
            if start:
                query = query.where(filter=FieldFilter("created_at", ">=", start))
            if end:
                query = query.where(filter=FieldFilter("created_at", "<", end))
            direction = (
                self.firestore.Query.ASCENDING
                if ascending
                else self.firestore.Query.DESCENDING
            )
            query = query.order_by("created_at", direction=direction)
            logs = [
                log_from_document(snapshot.to_dict(), snapshot.id)
                for snapshot in query.stream()
            ]
            return [
                log
                for log in logs
                if not successful_only
                or (log.status == "success" and log.nutrition_json is not None)
            ]
        except Exception as exc:
            raise RepositoryError(str(exc)) from exc

    def get_whitelist(self, phone_number: str) -> WhitelistEntry | None:
        document = self._get("whitelist_entries", phone_number)
        return whitelist_from_document(document) if document else None

    def save_whitelist(self, entry: WhitelistEntry) -> None:
        entry.updated_at = utc_now()
        self._set("whitelist_entries", entry.phone_number, to_document(entry))

    def store_login_code(
        self,
        code_hash: str,
        phone_number: str,
        custom_token: str,
        expires_at: datetime,
    ) -> None:
        self._set(
            "admin_login_codes",
            code_hash,
            {
                "phone_number": phone_number,
                "custom_token": custom_token,
                "created_at": utc_now(),
                "expires_at": expires_at,
            },
        )


@lru_cache
def get_repository() -> NutritionRepository:
    settings = get_settings()
    if settings.app_env.lower() == "test":
        return InMemoryRepository()
    return FirestoreRepository(settings)
