"""로그인 실패 횟수 제한.

PIN이 숫자 4자리(1만 가지)라 제한이 없으면 짧은 시간에 전부 시도할 수 있다.
프로세스 메모리 기반이라 워커/서버가 여러 개면 각자 따로 센다. 확장 시 DB나 Redis로 옮길 것.
"""
import time
from collections import deque
from collections.abc import Callable

_MAX_KEYS = 10_000  # 메모리 보호: 이 수를 넘으면 오래된 키를 정리


class FailureLimiter:
    def __init__(
        self,
        max_failures: int,
        window_seconds: int,
        lock_seconds: int,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.max_failures = max_failures
        self.window = window_seconds
        self.lock = lock_seconds
        self._clock = clock
        self._failures: dict[str, deque[float]] = {}
        self._locked_until: dict[str, float] = {}

    def retry_after(self, key: str) -> int:
        """잠겨 있으면 남은 초(올림), 아니면 0"""
        until = self._locked_until.get(key)
        if until is None:
            return 0
        now = self._clock()
        if until <= now:
            self.reset(key)
            return 0
        return int(until - now) + 1

    def record_failure(self, key: str) -> None:
        now = self._clock()
        q = self._failures.setdefault(key, deque())
        q.append(now)
        while q and q[0] <= now - self.window:
            q.popleft()
        if len(q) >= self.max_failures:
            self._locked_until[key] = now + self.lock
            q.clear()
        if len(self._failures) > _MAX_KEYS:
            self._prune(now)

    def reset(self, key: str) -> None:
        self._failures.pop(key, None)
        self._locked_until.pop(key, None)

    def _prune(self, now: float) -> None:
        for key in list(self._failures):
            q = self._failures[key]
            recent = q and q[-1] > now - self.window
            if not recent and key not in self._locked_until:
                del self._failures[key]
