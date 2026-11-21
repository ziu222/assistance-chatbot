from datetime import timedelta

from app.config.settings import Settings
from app.modules.agent_runtime.infrastructure.run_requests import DebounceWindow


def debounce_window(settings: Settings) -> DebounceWindow:
    return DebounceWindow(
        window=timedelta(seconds=settings.agent_debounce_window_seconds),
        max_wait=timedelta(seconds=settings.agent_debounce_max_wait_seconds),
    )
