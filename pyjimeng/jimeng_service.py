from __future__ import annotations

from typing import Any, Dict, Optional, Sequence, Union

from .errors import JimengAPIError
from .service import JimengClient

SessionArg = Optional[Union[str, Sequence[str]]]


class JimengAPIService:
    """
    Backwards-compatible facade around JimengClient used by the legacy test script.
    """

    def __init__(self, session_id: SessionArg = None, *, auto_start: bool = True) -> None:
        self._client = JimengClient(session_ids=session_id)
        self._running = False
        if auto_start:
            self.start()

    # ------------------------------------------------------------------ #
    # Lifecycle helpers
    # ------------------------------------------------------------------ #
    def start(self) -> None:
        if not self._client.session_ids:
            raise JimengAPIError("未提供 session_id，无法启动服务")
        self._running = True

    def stop(self) -> None:
        self._running = False

    def is_running(self) -> bool:
        return self._running

    def set_session_ids(self, session_ids: SessionArg) -> None:
        self._client.set_session_ids(session_ids or [])

    # ------------------------------------------------------------------ #
    # Core API proxies
    # ------------------------------------------------------------------ #
    def check_session_status(self, session_id: SessionArg = None) -> Dict[str, Any]:
        self._ensure_running()
        return self._client.check_session_status(session_id=session_id)

    def get_points(self, session_ids: SessionArg = None) -> Any:
        self._ensure_running()
        return self._client.get_points(session_ids=session_ids)

    def generate_image(self, *args: Any, **kwargs: Any) -> Dict[str, Any]:
        self._ensure_running()
        return self._client.generate_image(*args, **kwargs)

    def image_composition(self, *args: Any, **kwargs: Any) -> Dict[str, Any]:
        self._ensure_running()
        return self._client.image_composition(*args, **kwargs)

    def generate_video(self, *args: Any, **kwargs: Any) -> Dict[str, Any]:
        self._ensure_running()
        return self._client.generate_video(*args, **kwargs)

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #
    def _ensure_running(self) -> None:
        if not self._running:
            raise JimengAPIError("服务尚未启动，请先调用 start()")

