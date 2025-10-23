from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, Generic, Optional, Tuple, TypeVar

from .constants import POLLING_CONFIG, STATUS_CODE_MAP
from .errors import JimengPollingTimeout
from .logging import get_logger

T = TypeVar("T")


@dataclass
class PollingStatus:
    status: int
    fail_code: Optional[str]
    item_count: int
    finish_time: Optional[int] = None
    history_id: Optional[str] = None


@dataclass
class PollingResult:
    status: int
    fail_code: Optional[str]
    item_count: int
    elapsed_time: float
    poll_count: int
    exit_reason: str


class SmartPoller(Generic[T]):
    def __init__(
        self,
        *,
        max_poll_count: int | None = None,
        poll_interval: float | None = None,
        stable_rounds: int | None = None,
        timeout_seconds: float | None = None,
        expected_item_count: int = 4,
        item_type: str = "image",
    ) -> None:
        self.logger = get_logger()
        self.max_poll_count = max_poll_count or POLLING_CONFIG["MAX_POLL_COUNT"]
        self.poll_interval = poll_interval or POLLING_CONFIG["POLL_INTERVAL"]
        self.stable_rounds = stable_rounds or POLLING_CONFIG["STABLE_ROUNDS"]
        self.timeout_seconds = timeout_seconds or POLLING_CONFIG["TIMEOUT_SECONDS"]
        self.expected_item_count = expected_item_count
        self.item_type = item_type

    def _status_name(self, status: int) -> str:
        return STATUS_CODE_MAP.get(status, f"UNKNOWN({status})")

    def _next_interval(self, status: int) -> float:
        if status == 42:
            return self.poll_interval * 1.2
        if status == 45:
            return self.poll_interval * 1.5
        if status in (10, 50, 30):
            return 0.0
        return self.poll_interval

    def poll(
        self,
        poll_fn: Callable[[], Tuple[PollingStatus, T]],
        *,
        history_id: str | None = None,
    ) -> Tuple[PollingResult, T]:
        self.logger.info(
            "开始轮询: history_id=%s, 目标=%s, 最多轮询=%s",
            history_id or "N/A",
            self.expected_item_count,
            self.max_poll_count,
        )
        poll_count = 0
        start = time.time()
        last_item_count = 0
        stable_rounds = 0
        last_status = None
        data: Optional[T] = None

        while True:
            poll_count += 1
            status, data = poll_fn()
            last_status = status
            elapsed = time.time() - start
            if status.item_count == last_item_count:
                stable_rounds += 1
            else:
                stable_rounds = 0
                last_item_count = status.item_count

            self.logger.info(
                "轮询 %s/%s: status=%s(%s) items=%s elapsed=%.1fs",
                poll_count,
                self.max_poll_count,
                status.status,
                self._status_name(status.status),
                status.item_count,
                elapsed,
            )

            exit_reason = None
            if status.status in (10, 30, 50):
                exit_reason = "完成" if status.status != 30 else "失败"
            elif status.item_count >= self.expected_item_count:
                exit_reason = "已获得完整结果"
            elif stable_rounds >= self.stable_rounds and status.item_count > 0:
                exit_reason = "结果数量稳定"
            elif poll_count >= self.max_poll_count:
                exit_reason = "轮询次数超限"
            elif elapsed >= self.timeout_seconds and status.item_count > 0:
                exit_reason = "时间超限但有结果"

            if exit_reason:
                result = PollingResult(
                    status=status.status,
                    fail_code=status.fail_code,
                    item_count=status.item_count,
                    elapsed_time=elapsed,
                    poll_count=poll_count,
                    exit_reason=exit_reason,
                )
                return result, data

            if elapsed >= self.timeout_seconds:
                raise JimengPollingTimeout(
                    f"轮询超时 {elapsed:.0f}s status={status.status} items={status.item_count}"
                )

            interval = self._next_interval(status.status)
            if interval > 0:
                time.sleep(interval)

