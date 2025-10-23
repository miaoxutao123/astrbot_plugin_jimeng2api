from __future__ import annotations

import base64
import random
from typing import Any, Dict, Iterable, List, Optional, Sequence, Union

import requests

from . import core
from .errors import JimengAPIError
from .images import generate_image_composition, generate_images
from .logging import get_logger
from .videos import generate_video as generate_video_api
from .util import unix_timestamp

logger = get_logger()


class JimengClient:
    """
    Pure Python client that talks to the Jimeng backend directly.
    """

    def __init__(
        self,
        session_ids: Optional[Union[str, Sequence[str]]] = None,
    ) -> None:
        if session_ids is None:
            self._session_ids: List[str] = []
        elif isinstance(session_ids, str):
            self._session_ids = [session_ids]
        else:
            self._session_ids = list(session_ids)

    # ------------------------------------------------------------------ #
    # Session helpers
    # ------------------------------------------------------------------ #
    @property
    def session_ids(self) -> List[str]:
        return list(self._session_ids)

    def set_session_ids(self, session_ids: Union[str, Sequence[str]]) -> None:
        if isinstance(session_ids, str):
            self._session_ids = [session_ids]
        else:
            self._session_ids = list(session_ids)

    def add_session_id(self, session_id: str) -> None:
        if session_id not in self._session_ids:
            self._session_ids.append(session_id)

    def remove_session_id(self, session_id: str) -> None:
        if session_id in self._session_ids:
            self._session_ids.remove(session_id)

    def clear_session_ids(self) -> None:
        self._session_ids.clear()

    def _choose_token(self, override: Optional[Union[str, Sequence[str]]] = None) -> str:
        if override:
            tokens = [override] if isinstance(override, str) else list(override)
        else:
            tokens = self._session_ids
        if not tokens:
            raise JimengAPIError("未提供 session_id")
        return random.choice(tokens)

    # ------------------------------------------------------------------ #
    # Core APIs
    # ------------------------------------------------------------------ #
    def check_session_status(self, session_id: Optional[str] = None) -> Dict[str, Any]:
        token = self._choose_token(session_id)
        live = core.get_token_live_status(token)
        return {"live": live}

    def get_points(self, session_ids: Optional[Union[str, Sequence[str]]] = None) -> List[Dict[str, Any]]:
        tokens = (
            [session_ids] if isinstance(session_ids, str)
            else list(session_ids) if session_ids else self._session_ids
        )
        if not tokens:
            raise JimengAPIError("未提供 session_id")
        results = []
        for token in tokens:
            points = core.get_credit(token)
            results.append({"token": token, "points": points})
        return results

    def generate_image(
        self,
        prompt: str,
        *,
        session_id: Optional[Union[str, Sequence[str]]] = None,
        model: str = "jimeng-4.0",
        ratio: str = "1:1",
        resolution: str = "2k",
        negative_prompt: Optional[str] = None,
        sample_strength: float = 0.5,
        response_format: str = "url",
    ) -> Dict[str, Any]:
        token = self._choose_token(session_id)
        urls = generate_images(
            model,
            prompt,
            refresh_token=token,
            ratio=ratio,
            resolution=resolution,
            sample_strength=sample_strength,
            negative_prompt=negative_prompt or "",
        )
        data = self._format_response(urls, response_format)
        return {"created": unix_timestamp(), "data": data}

    def image_composition(
        self,
        prompt: str,
        images: Sequence[Union[str, bytes]],
        *,
        session_id: Optional[Union[str, Sequence[str]]] = None,
        model: str = "jimeng-4.0",
        ratio: str = "1:1",
        resolution: str = "2k",
        negative_prompt: Optional[str] = None,
        sample_strength: float = 0.5,
        response_format: str = "url",
    ) -> Dict[str, Any]:
        token = self._choose_token(session_id)
        urls = generate_image_composition(
            model,
            prompt,
            images,
            refresh_token=token,
            ratio=ratio,
            resolution=resolution,
            sample_strength=sample_strength,
            negative_prompt=negative_prompt or "",
        )
        data = self._format_response(urls, response_format)
        return {
            "created": unix_timestamp(),
            "data": data,
            "input_images": len(images),
            "composition_type": "multi_image_synthesis",
        }

    def generate_video(
        self,
        prompt: str,
        *,
        session_id: Optional[Union[str, Sequence[str]]] = None,
        model: str = "jimeng-video-3.0",
        width: int = 1024,
        height: int = 1024,
        resolution: str = "720p",
        response_format: str = "url",
    ) -> Dict[str, Any]:
        token = self._choose_token(session_id)
        video_url = generate_video_api(
            model,
            prompt,
            refresh_token=token,
            width=width,
            height=height,
            resolution=resolution,
        )
        if response_format == "b64_json":
            response = requests.get(video_url, timeout=300)
            response.raise_for_status()
            data = [
                {
                    "b64_json": base64.b64encode(response.content).decode("ascii"),
                }
            ]
        else:
            data = [{"url": video_url}]
        return {"created": unix_timestamp(), "data": data}

    # ------------------------------------------------------------------ #
    # Helper
    # ------------------------------------------------------------------ #
    def _format_response(self, urls: Sequence[str], response_format: str) -> List[Dict[str, Any]]:
        if response_format == "b64_json":
            items = []
            for url in urls:
                content = requests.get(url, timeout=60)
                content.raise_for_status()
                b64 = base64.b64encode(content.content).decode("ascii")
                items.append({"b64_json": b64})
            return items
        return [{"url": url} for url in urls]
