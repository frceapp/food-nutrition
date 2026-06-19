from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class DetectedFood(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    estimated_portion: str = Field(min_length=1, max_length=80)
    category: str = Field(min_length=1, max_length=50)
    emoji: str = Field(default="🍽️", max_length=16)


class NutritionEstimate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    energy_kcal: float = Field(ge=0)
    carbohydrate_g: float = Field(ge=0)
    protein_g: float = Field(ge=0)
    fat_g: float = Field(ge=0)
    fiber_g: float = Field(ge=0)
    calcium_note: str = Field(min_length=1, max_length=300)


class PlateAnalysis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    carbohydrate_percent: float = Field(ge=0, le=100)
    carbohydrate_status: str = Field(min_length=1, max_length=50)
    protein_percent: float = Field(ge=0, le=100)
    protein_status: str = Field(min_length=1, max_length=50)
    vegetable_percent: float = Field(ge=0, le=100)
    vegetable_status: str = Field(min_length=1, max_length=50)
    fruit_percent: float = Field(ge=0, le=100)
    fruit_status: str = Field(min_length=1, max_length=50)


class NutritionAnalysis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    image_quality: Literal["good", "bad"]
    detected_foods: list[DetectedFood]
    nutrition_estimate: NutritionEstimate | None
    plate_analysis: PlateAnalysis | None
    suggestion: str = Field(min_length=1, max_length=1000)
    confidence: Literal["low", "medium", "high"]


class AnalyzeFoodResponse(BaseModel):
    success: bool
    formatted_message: str | None = None
    gemini_json: NutritionAnalysis | None = None
    error_message: str | None = None


class DailySummaryResponse(BaseModel):
    success: bool
    formatted_message: str
    error_message: str | None = None


class MealRevisionAnalysis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    detected_foods: list[DetectedFood]
    nutrition_estimate: NutritionEstimate
    plate_analysis: PlateAnalysis
    confidence: Literal["low", "medium", "high"]


class ChatRequest(BaseModel):
    phone_number: str = Field(min_length=8, max_length=32)
    user_name: str | None = Field(default=None, max_length=120)
    message: str = Field(min_length=1, max_length=500)


class ChatResponse(BaseModel):
    success: bool
    formatted_message: str
    error_message: str | None = None
