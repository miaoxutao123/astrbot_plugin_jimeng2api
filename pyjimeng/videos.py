from __future__ import annotations

import json
import math
import random
import time
from typing import Dict, List, Optional, Tuple

from . import constants
from .core import get_credit, receive_credit, request
from .errors import JimengAPIError
from .logging import get_logger
from .poller import PollingStatus, SmartPoller
from .util import uuid_str

logger = get_logger()


def _map_model(name: str) -> str:
    mapping = constants.VIDEO_MODEL_MAP
    return mapping.get(name, mapping[constants.DEFAULT_VIDEO_MODEL])


def _aspect_ratio(width: int, height: int) -> str:
    gcd = math.gcd(width, height)
    return f"{width // gcd}:{height // gcd}"


def _build_text_to_video_payload(
    *,
    prompt: str,
    model: str,
    width: int,
    height: int,
    resolution: str,
) -> Dict[str, object]:
    component_id = uuid_str()
    now_ms = int(time.time() * 1000)
    metrics_extra = json.dumps(
        {
            "enterFrom": "click",
            "isDefaultSeed": 1,
            "promptSource": "custom",
            "isRegenerate": False,
            "originSubmitId": uuid_str(),
        }
    )

    text_to_video_params = {
        "type": "",
        "id": uuid_str(),
        "model_req_key": model,
        "priority": 0,
        "seed": random.randint(2_500_000_000, 2_600_000_000),
        "video_aspect_ratio": _aspect_ratio(width, height),
        "video_gen_inputs": [
            {
                "duration_ms": 5000,
                "first_frame_image": None,
                "end_frame_image": None,
                "fps": 24,
                "id": uuid_str(),
                "min_version": "3.0.5",
                "prompt": prompt,
                "resolution": resolution,
                "type": "",
                "video_mode": 2,
            }
        ],
    }

    draft_content = {
        "type": "draft",
        "id": uuid_str(),
        "min_version": "3.0.5",
        "is_from_tsn": True,
        "version": constants.DRAFT_VERSION,
        "main_component_id": component_id,
        "component_list": [
            {
                "type": "video_base_component",
                "id": component_id,
                "min_version": "1.0.0",
                "metadata": {
                    "type": "",
                    "id": uuid_str(),
                    "created_platform": 3,
                    "created_platform_version": "",
                    "created_time_in_ms": now_ms,
                    "created_did": "",
                },
                "generate_type": "gen_video",
                "aigc_mode": "workbench",
                "abilities": {
                    "type": "",
                    "id": uuid_str(),
                    "gen_video": {
                        "id": uuid_str(),
                        "type": "",
                        "text_to_video_params": text_to_video_params,
                        "video_task_extra": metrics_extra,
                    },
                },
            }
        ],
    }

    return {
        "extend": {
            "root_model": model,
            "m_video_commerce_info": {
                "benefit_type": "basic_video_operation_vgfm_v_three",
                "resource_id": "generate_video",
                "resource_id_type": "str",
                "resource_sub_type": "aigc",
            },
            "m_video_commerce_info_list": [
                {
                    "benefit_type": "basic_video_operation_vgfm_v_three",
                    "resource_id": "generate_video",
                    "resource_id_type": "str",
                    "resource_sub_type": "aigc",
                }
            ],
        },
        "submit_id": uuid_str(),
        "metrics_extra": metrics_extra,
        "draft_content": json.dumps(draft_content),
        "http_common_info": {
            "aid": int(constants.DEFAULT_ASSISTANT_ID_CN),
        },
    }


def _poll_video_status(history_id: str, refresh_token: str) -> Tuple[PollingStatus, Dict[str, object]]:
    payload = {"history_ids": [history_id]}
    history = request("POST", "/mweb/v1/get_history_by_ids", refresh_token, json=payload)

    info: Optional[Dict[str, object]] = None
    if isinstance(history, dict):
        if history_id in history:
            info = history[history_id]
        elif "history_list" in history and history["history_list"]:
            info = history["history_list"][0]
        elif "history_records" in history and history["history_records"]:
            info = history["history_records"][0]

    if not isinstance(info, dict):
        raise JimengAPIError("记录不存在")

    item_list = info.get("item_list") or []
    status = PollingStatus(
        status=info.get("status"),
        fail_code=info.get("fail_code"),
        item_count=len(item_list),
        history_id=history_id,
    )
    return status, info


def _extract_video_url(info: Dict[str, object]) -> Optional[str]:
    item_list = info.get("item_list") or []
    if not item_list:
        return None
    video_info = item_list[0].get("video") if isinstance(item_list[0], dict) else None
    if not isinstance(video_info, dict):
        return None

    candidates = [
        (video_info.get("transcoded_video") or {}).get("origin", {}).get("video_url")
        if isinstance(video_info.get("transcoded_video"), dict)
        else None,
        video_info.get("play_url"),
        video_info.get("download_url"),
        video_info.get("url"),
    ]
    for url in candidates:
        if isinstance(url, str) and url.startswith("http"):
            return url
    return None


def generate_video(
    _model: str,
    prompt: str,
    *,
    refresh_token: str,
    width: int = 1024,
    height: int = 1024,
    resolution: str = "720p",
) -> str:
    model = _map_model(_model)
    logger.info("使用视频模型: %s -> %s", _model, model)

    credits = get_credit(refresh_token)
    if credits["totalCredit"] <= 0:
        receive_credit(refresh_token)

    data = _build_text_to_video_payload(
        prompt=prompt,
        model=model,
        width=width,
        height=height,
        resolution=resolution,
    )

    params = {
        "web_version": "6.6.0",
        "da_version": constants.DRAFT_VERSION,
        "aigc_features": constants.AIGC_FEATURES,
    }

    response = request(
        "POST",
        "/mweb/v1/aigc_draft/generate",
        refresh_token,
        json=data,
        params=params,
    )

    aigc_data = response.get("aigc_data") if isinstance(response, dict) else None
    history_id = aigc_data.get("history_record_id") if isinstance(aigc_data, dict) else None
    if not history_id:
        raise JimengAPIError("记录ID不存在")

    poller = SmartPoller(
        expected_item_count=1,
        poll_interval=3.0,
        timeout_seconds=600.0,
        item_type="video",
    )

    result, info = poller.poll(lambda: _poll_video_status(history_id, refresh_token), history_id=history_id)

    url = _extract_video_url(info)
    if not url:
        raise JimengAPIError("未能获取视频URL")

    logger.info("视频生成完成 url=%s status=%s", url, result.status)
    return url
