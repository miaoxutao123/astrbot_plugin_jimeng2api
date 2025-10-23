from __future__ import annotations

import base64
import hashlib
import random
import time
import uuid
from dataclasses import dataclass
from typing import Iterable, Optional

import requests


def uuid_str(with_hyphen: bool = True) -> str:
    value = str(uuid.uuid4())
    return value if with_hyphen else value.replace("-", "")


def unix_timestamp() -> int:
    return int(time.time())


def timestamp_ms() -> int:
    return int(time.time() * 1000)


def md5(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()


def random_fingerprint() -> int:
    return random.randint(7_000_000_000_000_000_000, 9_000_000_000_000_000_000)


def fetch_file_base64(url: str) -> str:
    response = requests.get(url, timeout=30)
    response.raise_for_status()
    return base64.b64encode(response.content).decode("ascii")


def is_base64_data(payload: str) -> bool:
    return payload.startswith("data:")


def normalize_base64(payload: str) -> str:
    if payload.startswith("data:"):
        return payload.split(",", 1)[1]
    return payload


def chunk_list(items: Iterable, size: int):
    chunk = []
    for item in items:
        chunk.append(item)
        if len(chunk) >= size:
            yield chunk
            chunk = []
    if chunk:
        yield chunk


@dataclass
class PollingOutcome:
    status: int
    fail_code: Optional[str]
    item_count: int
    elapsed: float
    reason: str

