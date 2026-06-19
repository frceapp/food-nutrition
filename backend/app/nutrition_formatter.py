from app.schemas import NutritionAnalysis


BAD_IMAGE_MESSAGE = (
    "Maaf Bunda, makanan pada foto belum terlihat cukup jelas. "
    "Coba kirim foto dengan pencahayaan lebih terang dan posisi makanan terlihat dari atas ya."
)


def _number(value: float) -> str:
    return str(int(value)) if value.is_integer() else f"{value:g}"


def format_nutrition_message(analysis: NutritionAnalysis) -> str:
    if analysis.image_quality == "bad":
        return BAD_IMAGE_MESSAGE

    if analysis.nutrition_estimate is None or analysis.plate_analysis is None:
        raise ValueError("Analisis berkualitas baik harus memiliki data nutrisi dan isi piring")

    food_lines = "\n".join(
        f"{food.emoji} {food.name} ({food.estimated_portion})"
        for food in analysis.detected_foods
    )
    nutrition = analysis.nutrition_estimate
    plate = analysis.plate_analysis
    message = (
        "Halo Bunda 👋\n"
        "NUTRIA telah cek makanan yang dikirim ✨\n\n"
        "Makanan yang terdeteksi:\n"
        f"{food_lines}\n\n"
        "Perkiraan kandungan gizi:\n"
        f"* Energi: ±{_number(nutrition.energy_kcal)} kkal\n"
        f"* Karbohidrat: ±{_number(nutrition.carbohydrate_g)} gram\n"
        f"* Protein: ±{_number(nutrition.protein_g)} gram\n"
        f"* Lemak: ±{_number(nutrition.fat_g)} gram\n"
        f"* Serat: ±{_number(nutrition.fiber_g)} gram\n\n"
        "Analisis Isi Piringku:\n"
        f"🟢 Karbohidrat: {_number(plate.carbohydrate_percent)}% {plate.carbohydrate_status}\n"
        f"🟢 Protein: {_number(plate.protein_percent)}% {plate.protein_status}\n"
        f"🟢 Sayur: {_number(plate.vegetable_percent)}% {plate.vegetable_status}\n"
        f"🟢 Buah: {_number(plate.fruit_percent)}% {plate.fruit_status}\n\n"
        "Saran berikutnya:\n"
        f"{analysis.suggestion}\n\n"
        "Ada yang perlu diperbaiki? Langsung tulis revisinya, misalnya:\n"
        "*ada tempe 2 potong juga*, *ayamnya ada 2*, atau "
        "*itu bukan ayam tapi daging sapi*."
    )
    return message
