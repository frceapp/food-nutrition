import re
from dataclasses import dataclass

from app.domain import User, UserProfile, utc_now
from app.repository import NutritionRepository


INTRO_MESSAGE = """Halo Bunda 👋
Saya NUTRIA, asisten untuk mencatat estimasi nutrisi dari foto makanan.

Cara menggunakan:
1. Lengkapi profil singkat.
2. Kirim foto makanan dari atas piring.
3. Ketik *dashboard* untuk melihat grafik, total gizi, dan evaluasi.

Semua hasil merupakan estimasi visual, bukan diagnosis atau hasil laboratorium.

Pertama, berapa berat badan Bunda saat ini dalam kg?
Contoh: *60*"""

ACTIVITY_LEVELS = {
    "1": ("rendah", 25.0, "jarang olahraga / lebih banyak duduk"),
    "rendah": ("rendah", 25.0, "jarang olahraga / lebih banyak duduk"),
    "2": ("ringan", 30.0, "aktivitas atau olahraga ringan 1–3 kali/minggu"),
    "ringan": ("ringan", 30.0, "aktivitas atau olahraga ringan 1–3 kali/minggu"),
    "3": ("sedang", 35.0, "olahraga sedang 3–5 kali/minggu"),
    "sedang": ("sedang", 35.0, "olahraga sedang 3–5 kali/minggu"),
    "4": ("tinggi", 40.0, "aktivitas berat 6–7 kali/minggu"),
    "tinggi": ("tinggi", 40.0, "aktivitas berat 6–7 kali/minggu"),
    "aktif": ("tinggi", 40.0, "aktivitas berat 6–7 kali/minggu"),
}

ACTIVITY_PROMPT = """Bagaimana pola aktivitas harian Bunda?
Balas angka 1–4:
1. Rendah — jarang olahraga / lebih banyak duduk
2. Ringan — olahraga ringan 1–3 kali/minggu
3. Sedang — olahraga sedang 3–5 kali/minggu
4. Tinggi — aktivitas berat 6–7 kali/minggu"""


@dataclass(slots=True)
class ProfileContext:
    user: User
    profile: UserProfile
    is_new_profile: bool


def get_or_create_profile(
    repository: NutritionRepository, phone_number: str, user_name: str | None
) -> ProfileContext:
    user = repository.get_user(phone_number)
    if user is None:
        user = User(phone_number=phone_number, name=user_name or None)
    elif user_name and not user.name:
        user.name = user_name
    repository.save_user(user)

    profile = repository.get_profile(phone_number)
    is_new = profile is None
    if profile is None:
        profile = UserProfile(user_id=phone_number, onboarding_step="weight")
        repository.save_profile(profile)
    return ProfileContext(user=user, profile=profile, is_new_profile=is_new)


def profile_prompt(profile: UserProfile) -> str:
    if profile.onboarding_step == "weight":
        return "Berapa berat badan Bunda dalam kg? Contoh: *60*"
    if profile.onboarding_step == "height":
        return "Berapa tinggi badan Bunda dalam cm? Contoh: *158*"
    if profile.onboarding_step == "activity":
        return ACTIVITY_PROMPT
    return "Profil sudah lengkap. Kirim foto makanan untuk mulai mencatat."


def _parse_number(message: str) -> float | None:
    match = re.search(r"\d+(?:[.,]\d+)?", message)
    return float(match.group(0).replace(",", ".")) if match else None


def _bmi_category(bmi: float) -> str:
    if bmi < 18.5:
        return "berat badan kurang"
    if bmi < 25:
        return "normal"
    if bmi < 30:
        return "berat badan berlebih"
    return "obesitas"


def process_onboarding(profile: UserProfile, message: str) -> str:
    if profile.onboarding_step == "weight":
        weight = _parse_number(message)
        if weight is None or not 25 <= weight <= 300:
            return "Berat badan belum valid. Masukkan angka 25–300 dalam kg. Contoh: *60*"
        profile.weight_kg = weight
        profile.onboarding_step = "height"
        return "Berat badan sudah dicatat ✅\n\nBerapa tinggi badan Bunda dalam cm? Contoh: *158*"

    if profile.onboarding_step == "height":
        height = _parse_number(message)
        if height is None or not 100 <= height <= 230:
            return "Tinggi badan belum valid. Masukkan angka 100–230 dalam cm. Contoh: *158*"
        profile.height_cm = height
        profile.onboarding_step = "activity"
        return f"Tinggi badan sudah dicatat ✅\n\n{ACTIVITY_PROMPT}"

    if profile.onboarding_step == "activity":
        normalized = message.lower().strip()
        activity = ACTIVITY_LEVELS.get(normalized)
        if activity is None:
            return f"Pilihan aktivitas belum dikenali.\n\n{ACTIVITY_PROMPT}"
        level, kcal_per_kg, _ = activity
        profile.activity_level = level
        height_m = (profile.height_cm or 0) / 100
        bmi = (profile.weight_kg or 0) / (height_m**2)
        calorie_target = (profile.weight_kg or 0) * kcal_per_kg
        profile.bmi = round(bmi, 1)
        profile.bmi_category = _bmi_category(bmi)
        profile.daily_calorie_target = round(calorie_target)
        profile.onboarding_step = "complete"
        profile.completed_at = utc_now()
        return (
            "Profil NUTRIA sudah lengkap ✅\n\n"
            f"* IMT: {profile.bmi:.1f} ({profile.bmi_category})\n"
            f"* Aktivitas: {profile.activity_level}\n"
            f"* Estimasi kebutuhan energi: ±{profile.daily_calorie_target:.0f} kkal/hari\n\n"
            "Kebutuhan energi ini merupakan estimasi awal berdasarkan berat badan dan aktivitas. "
            "Sekarang kirim foto makanan dari atas piring ya."
        )

    return "Profil sudah lengkap. Kirim foto makanan untuk mulai mencatat."


def format_profile(profile: UserProfile) -> str:
    if profile.onboarding_step != "complete":
        return profile_prompt(profile)
    return (
        "Profil NUTRIA:\n"
        f"* Berat badan: {profile.weight_kg:g} kg\n"
        f"* Tinggi badan: {profile.height_cm:g} cm\n"
        f"* Aktivitas: {profile.activity_level}\n"
        f"* IMT: {profile.bmi:.1f} ({profile.bmi_category})\n"
        f"* Target energi: ±{profile.daily_calorie_target:.0f} kkal/hari"
    )


def reset_profile(profile: UserProfile) -> str:
    profile.weight_kg = None
    profile.height_cm = None
    profile.activity_level = None
    profile.bmi = None
    profile.bmi_category = None
    profile.daily_calorie_target = None
    profile.completed_at = None
    profile.onboarding_step = "weight"
    return "Profil akan diperbarui. Berapa berat badan Bunda dalam kg? Contoh: *60*"
