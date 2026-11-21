"""Circuit breaker theo provider (ADR-0015): đóng → mở → nửa mở.

- 3 lỗi "provider có vấn đề" LIÊN TIẾP ⇒ mở 30 s; thành công xen giữa thì đếm lại từ đầu.
- Lỗi dai dẳng (key hỏng, hết quota, sai cấu hình) ⇒ mở ngay 10 phút: không tự lành sau vài giây.
- Hết hạn ⇒ nửa mở: cho ĐÚNG MỘT yêu cầu thật đi thử; thành công ⇒ đóng, lỗi ⇒ mở lại.
- Không ping chủ động (tốn tiền). Lỗi do yêu cầu/model trả rác không tính vào sức khỏe provider.

ponytail: trạng thái nằm trong từng process; chia sẻ qua Redis khi chạy nhiều worker và đo thấy
failover chậm nhận ra sự cố.
"""

import time
from collections.abc import Callable
from dataclasses import dataclass

from app.modules.agent_runtime.llm.port import PERSISTENT_FAILURES


@dataclass
class _State:
    failures: int = 0
    open_until: float | None = None
    trial_in_flight: bool = False


class CircuitBreaker:
    def __init__(
        self,
        *,
        threshold: int = 3,
        open_for: float = 30.0,
        persistent_open_for: float = 600.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._threshold = threshold
        self._open_for = open_for
        self._persistent_open_for = persistent_open_for
        self._clock = clock
        self._states: dict[str, _State] = {}

    def _state(self, provider: str) -> _State:
        return self._states.setdefault(provider, _State())

    def is_open(self, provider: str) -> bool:
        """Chưa đóng hẳn (đang mở hoặc đang chờ lượt thử)."""
        return self._state(provider).open_until is not None

    def allow(self, provider: str) -> bool:
        st = self._state(provider)
        if st.open_until is None:
            return True
        if self._clock() < st.open_until:
            return False
        if st.trial_in_flight:  # nửa mở: chỉ một lượt thử tại một thời điểm
            return False
        st.trial_in_flight = True
        return True

    def success(self, provider: str) -> None:
        self._states[provider] = _State()

    def failure(self, provider: str, reason: str) -> None:
        st = self._state(provider)
        was_trial = st.trial_in_flight
        st.trial_in_flight = False
        st.failures += 1
        if reason in PERSISTENT_FAILURES:
            st.open_until = self._clock() + self._persistent_open_for
        elif was_trial or st.failures >= self._threshold:
            st.open_until = self._clock() + self._open_for

    def release(self, provider: str) -> None:
        """Kết thúc bằng lỗi KHÔNG phải của provider: trả lại lượt thử, không đổi gì khác."""
        self._state(provider).trial_in_flight = False
