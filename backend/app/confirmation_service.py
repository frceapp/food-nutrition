import asyncio
import logging
from datetime import datetime

from app.domain import FoodAnalysisLog
from app.repository import NutritionRepository, RepositoryError


logger = logging.getLogger(__name__)
_background_tasks: set[asyncio.Task[None]] = set()


async def auto_close_confirmation(
    repository: NutritionRepository,
    user_id: str,
    log_id: str,
    expected_updated_at: datetime,
    delay_seconds: float,
) -> None:
    try:
        await asyncio.sleep(max(0, delay_seconds))
        logs = repository.get_logs(user_id, ascending=False)
        target = next((log for log in logs if log.id == log_id), None)
        if (
            target is None
            or target.status != "success"
            or not target.awaiting_confirmation
            or target.updated_at > expected_updated_at
        ):
            return
        target.awaiting_confirmation = False
        repository.save_log(target)
        logger.info("Konfirmasi analisis %s ditutup otomatis", log_id)
    except asyncio.CancelledError:
        return
    except RepositoryError:
        logger.exception("Gagal menutup konfirmasi analisis %s", log_id)


def schedule_auto_close(
    repository: NutritionRepository,
    log: FoodAnalysisLog,
    timeout_minutes: int,
) -> None:
    if not log.id:
        return
    task = asyncio.create_task(
        auto_close_confirmation(
            repository,
            log.user_id,
            log.id,
            log.updated_at,
            timeout_minutes * 60,
        )
    )
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
