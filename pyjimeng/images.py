from __future__ import annotations

import base64
import datetime as dt
import json
import hashlib
import random
import string
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple

import requests

from . import constants
from .aws_signature import create_signature
from .core import get_credit, receive_credit, request
from .errors import JimengAPIError
from .logging import get_logger
from .poller import PollingStatus, SmartPoller
from .util import normalize_base64, uuid_str

logger = get_logger()

FAKE_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36"
)

HISTORY_IMAGE_INFO = {
    "width": 2048,
    "height": 2048,
    "format": "webp",
    "image_scene_list": [
        {"scene": "smart_crop", "width": 360, "height": 360, "uniq_key": "smart_crop-w:360-h:360", "format": "webp"},
        {"scene": "smart_crop", "width": 480, "height": 480, "uniq_key": "smart_crop-w:480-h:480", "format": "webp"},
        {"scene": "smart_crop", "width": 720, "height": 720, "uniq_key": "smart_crop-w:720-h:720", "format": "webp"},
        {"scene": "smart_crop", "width": 720, "height": 480, "uniq_key": "smart_crop-w:720-h:480", "format": "webp"},
        {"scene": "smart_crop", "width": 360, "height": 240, "uniq_key": "smart_crop-w:360-h:240", "format": "webp"},
        {"scene": "smart_crop", "width": 240, "height": 320, "uniq_key": "smart_crop-w:240-h:320", "format": "webp"},
        {"scene": "smart_crop", "width": 480, "height": 640, "uniq_key": "smart_crop-w:480-h:640", "format": "webp"},
        {"scene": "normal", "width": 2400, "height": 2400, "uniq_key": "2400", "format": "webp"},
        {"scene": "normal", "width": 1080, "height": 1080, "uniq_key": "1080", "format": "webp"},
        {"scene": "normal", "width": 720, "height": 720, "uniq_key": "720", "format": "webp"},
        {"scene": "normal", "width": 480, "height": 480, "uniq_key": "480", "format": "webp"},
        {"scene": "normal", "width": 360, "height": 360, "uniq_key": "360", "format": "webp"},
    ],
}


def _is_us(refresh_token: str) -> bool:
    return refresh_token.lower().startswith("us-")


def _random_string(length: int = 10) -> str:
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=length))


def _aws_timestamp() -> str:
    return dt.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")


def _crc32(data: bytes) -> str:
    import binascii

    return f"{binascii.crc32(data) & 0xFFFFFFFF:08x}"


def _get_resolution(resolution: str, ratio: str) -> Dict[str, Any]:
    group = constants.RESOLUTION_OPTIONS.get(resolution)
    if not group:
        raise JimengAPIError(f"不支持的分辨率 {resolution}")
    config = group.get(ratio)
    if not config:
        raise JimengAPIError(f"在分辨率 {resolution} 下不支持比例 {ratio}")
    return {**config, "resolution_type": resolution}


def _map_model(model: str, refresh_token: str) -> str:
    mapping = constants.IMAGE_MODEL_MAP_US if _is_us(refresh_token) else constants.IMAGE_MODEL_MAP
    if _is_us(refresh_token) and model not in mapping:
        supported = ", ".join(mapping)
        raise JimengAPIError(f"国际站不支持模型 {model}，可选: {supported}")
    return mapping.get(model, mapping[constants.DEFAULT_IMAGE_MODEL])


def _upload_buffer(buffer: bytes, refresh_token: str, *, is_us: bool) -> str:
    logger.info("上传图片，大小=%s字节 isUS=%s", len(buffer), is_us)
    token_info = request(
        "POST",
        "/mweb/v1/get_upload_token",
        refresh_token,
        json={"scene": 2},
    )

    access_key_id = token_info.get("access_key_id")
    secret_access_key = token_info.get("secret_access_key")
    session_token = token_info.get("session_token")
    service_id = token_info.get("service_id") or token_info.get("space_name")

    if not access_key_id or not secret_access_key or not session_token:
        raise JimengAPIError("获取上传令牌失败")

    apply_host = constants.BASE_URL_IMAGEX_US if is_us else "https://imagex.bytedanceapi.com"
    timestamp = _aws_timestamp()
    params = (
        f"?Action=ApplyImageUpload&Version=2018-08-01"
        f"&ServiceId={service_id}&FileSize={len(buffer)}&s={_random_string()}"
    )
    if is_us:
        params += "&device_platform=web"
    apply_url = f"{apply_host}/{params}"

    headers_for_sig = {
        "x-amz-date": timestamp,
        "x-amz-security-token": session_token,
    }
    authorization = create_signature(
        "GET", apply_url, headers_for_sig, access_key_id, secret_access_key, session_token
    )

    origin = constants.BASE_URL_DREAMINA_US if is_us else constants.BASE_URL_CN
    apply_resp = requests.get(
        apply_url,
        headers={
            "accept": "*/*",
            "authorization": authorization,
            "origin": origin,
            "referer": f"{origin}/ai-tool/generate",
            "user-agent": FAKE_UA,
            "x-amz-date": timestamp,
            "x-amz-security-token": session_token,
        },
        timeout=30,
    )
    apply_resp.raise_for_status()
    apply_data = apply_resp.json()

    upload_address = apply_data.get("Result", {}).get("UploadAddress")
    if not upload_address:
        raise JimengAPIError(f"申请上传失败: {apply_data}")

    store_info = upload_address["StoreInfos"][0]
    upload_host = upload_address["UploadHosts"][0]
    upload_url = f"https://{upload_host}/upload/v1/{store_info['StoreUri']}"

    crc = _crc32(buffer)

    upload_resp = requests.post(
        upload_url,
        headers={
            "Authorization": store_info["Auth"],
            "Content-CRC32": crc,
            "Content-Disposition": 'attachment; filename="upload.bin"',
            "Content-Type": "application/octet-stream",
            "User-Agent": FAKE_UA,
        },
        data=buffer,
        timeout=60,
    )
    upload_resp.raise_for_status()

    commit_url = f"{apply_host}/?Action=CommitImageUpload&Version=2018-08-01&ServiceId={service_id}"
    commit_payload = json.dumps({"SessionKey": upload_address["SessionKey"]})
    commit_timestamp = _aws_timestamp()
    payload_hash = hashlib.sha256(commit_payload.encode("utf-8")).hexdigest()
    commit_headers_for_sig = {
        "x-amz-date": commit_timestamp,
        "x-amz-security-token": session_token,
        "x-amz-content-sha256": payload_hash,
    }
    commit_authorization = create_signature(
        "POST",
        commit_url,
        commit_headers_for_sig,
        access_key_id,
        secret_access_key,
        session_token,
        commit_payload,
    )
    commit_headers = {
        "authorization": commit_authorization,
        "content-type": "application/json",
        "user-agent": FAKE_UA,
        "x-amz-date": commit_timestamp,
        "x-amz-security-token": session_token,
        "x-amz-content-sha256": payload_hash,
    }

    commit_resp = requests.post(
        commit_url,
        headers=commit_headers,
        data=commit_payload,
        timeout=30,
    )
    commit_resp.raise_for_status()
    commit_data = commit_resp.json()

    results = commit_data.get("Result", {}).get("Results", [])
    if not results:
        raise JimengAPIError(f"提交上传失败: {commit_data}")
    result = results[0]
    if result.get("UriStatus") != 2000:
        raise JimengAPIError(f"上传状态异常: {result}")

    return result["Uri"]


def _upload_image(source: str | bytes, refresh_token: str, *, is_us: bool) -> str:
    if isinstance(source, bytes):
        buffer = source
    elif isinstance(source, str):
        if source.startswith(("http://", "https://")):
            resp = requests.get(source, timeout=60)
            resp.raise_for_status()
            buffer = resp.content
        else:
            path = Path(source)
            if path.exists():
                buffer = path.read_bytes()
            else:
                payload = normalize_base64(source)
                buffer = base64.b64decode(payload)
    else:
        raise JimengAPIError("不支持的图片类型")

    return _upload_buffer(buffer, refresh_token, is_us=is_us)


def _extract_urls(items: List[Dict[str, Any]]) -> List[str]:
    urls: List[str] = []
    for item in items:
        url = (
            (item.get("image") or {})
            .get("large_images", [{}])[0]
            .get("image_url")
        )
        url = url or (item.get("common_attr") or {}).get("cover_url")
        url = url or item.get("image_url") or item.get("url")
        if url:
            urls.append(url)
    return urls


def _poll_history(history_id: str, refresh_token: str) -> Tuple[PollingStatus, Dict[str, Any]]:
    payload = {
        "history_ids": [history_id],
        "image_info": HISTORY_IMAGE_INFO,
    }
    history = request("POST", "/mweb/v1/get_history_by_ids", refresh_token, json=payload)
    if history_id not in history:
        raise JimengAPIError("记录不存在")
    info = history[history_id]
    status = PollingStatus(
        status=info.get("status"),
        fail_code=info.get("fail_code"),
        item_count=len(info.get("item_list") or []),
        finish_time=(info.get("task") or {}).get("finish_time"),
        history_id=history_id,
    )
    return status, info


def generate_images(
    model: str,
    prompt: str,
    *,
    refresh_token: str,
    ratio: str = "1:1",
    resolution: str = "2k",
    sample_strength: float = 0.5,
    negative_prompt: str = "",
) -> List[str]:
    is_us = _is_us(refresh_token)
    mapped_model = _map_model(model, refresh_token)

    if model == "nanobanana":
        width = height = 1024
        image_ratio = 1
        resolution_type = "2k"
    else:
        res = _get_resolution(resolution, ratio)
        width = res["width"]
        height = res["height"]
        image_ratio = res["ratio"]
        resolution_type = res["resolution_type"]

    credits = get_credit(refresh_token)
    if credits["totalCredit"] <= 0:
        receive_credit(refresh_token)

    component_id = uuid_str()
    submit_id = uuid_str()
    generate_id = uuid_str()
    now_ms = int(dt.datetime.utcnow().timestamp() * 1000)

    core_param = {
        "type": "",
        "id": uuid_str(),
        "model": mapped_model,
        "prompt": prompt,
        "negative_prompt": negative_prompt,
        "seed": random.randint(2_500_000_000, 2_600_000_000),
        "sample_strength": sample_strength,
        "image_ratio": image_ratio,
        "large_image_info": {
            "type": "",
            "id": uuid_str(),
            "height": height,
            "width": width,
            "resolution_type": resolution_type,
        },
        "intelligent_ratio": False,
    }

    payload = {
        "extend": {"root_model": mapped_model},
        "submit_id": submit_id,
        "metrics_extra": json.dumps(
            {
                "promptSource": "custom",
                "generateCount": 1,
                "enterFrom": "click",
                "generateId": generate_id,
                "isRegenerate": False,
            }
        ),
        "draft_content": json.dumps(
            {
                "type": "draft",
                "id": uuid_str(),
                "min_version": constants.DRAFT_MIN_VERSION,
                "min_features": [],
                "is_from_tsn": True,
                "version": constants.DRAFT_VERSION,
                "main_component_id": component_id,
                "component_list": [
                    {
                        "type": "image_base_component",
                        "id": component_id,
                        "min_version": constants.DRAFT_MIN_VERSION,
                        "aigc_mode": "workbench",
                        "metadata": {
                            "type": "",
                            "id": uuid_str(),
                            "created_platform": 3,
                            "created_platform_version": "",
                            "created_time_in_ms": str(now_ms),
                            "created_did": "",
                        },
                        "generate_type": "generate",
                        "abilities": {
                            "type": "",
                            "id": uuid_str(),
                            "generate": {
                                "type": "",
                                "id": uuid_str(),
                                "core_param": core_param,
                            },
                        },
                    }
                ],
            }
        ),
        "http_common_info": {
            "aid": constants.DEFAULT_ASSISTANT_ID_US if is_us else constants.DEFAULT_ASSISTANT_ID_CN
        },
    }

    response = request(
        "POST",
        "/mweb/v1/aigc_draft/generate",
        refresh_token,
        json=payload,
    )
    history_id = (response.get("aigc_data") or {}).get("history_record_id")
    if not history_id:
        raise JimengAPIError("记录ID不存在")

    poller = SmartPoller(expected_item_count=1, item_type="image")
    result, info = poller.poll(lambda: _poll_history(history_id, refresh_token), history_id=history_id)

    urls = _extract_urls(info.get("item_list") or [])
    if not urls:
        raise JimengAPIError("未能获取生成图片")
    return urls


def generate_image_composition(
    model: str,
    prompt: str,
    images: Sequence[str | bytes],
    *,
    refresh_token: str,
    ratio: str = "1:1",
    resolution: str = "2k",
    sample_strength: float = 0.5,
    negative_prompt: str = "",
) -> List[str]:
    if not images:
        raise JimengAPIError("至少需要提供1张图片")
    if len(images) > 10:
        raise JimengAPIError("最多支持10张图片")

    is_us = _is_us(refresh_token)
    mapped_model = _map_model(model, refresh_token)
    res = _get_resolution(resolution, ratio)

    uploaded = [
        _upload_image(image, refresh_token, is_us=is_us)
        for image in images
    ]

    component_id = uuid_str()
    submit_id = uuid_str()
    now_ms = int(dt.datetime.utcnow().timestamp() * 1000)

    core_param = {
        "type": "",
        "id": uuid_str(),
        "model": mapped_model,
        "prompt": f"##{prompt}",
        "sample_strength": sample_strength,
        "image_ratio": res["ratio"],
        "large_image_info": {
            "type": "",
            "id": uuid_str(),
            "height": res["height"],
            "width": res["width"],
            "resolution_type": res["resolution_type"],
        },
        "intelligent_ratio": False,
    }

    ability_list = []
    for image_uri in uploaded:
        ability_list.append(
            {
                "type": "",
                "id": uuid_str(),
                "name": "byte_edit",
                "image_uri_list": [image_uri],
                "image_list": [
                    {
                        "type": "image",
                        "id": uuid_str(),
                        "source_from": "upload",
                        "platform_type": 1,
                        "name": "",
                        "image_uri": image_uri,
                        "width": 0,
                        "height": 0,
                        "format": "",
                        "uri": image_uri,
                    }
                ],
                "strength": 0.5,
            }
        )

    payload = {
        "extend": {"root_model": mapped_model},
        "submit_id": submit_id,
        "metrics_extra": json.dumps(
            {
                "promptSource": "custom",
                "generateCount": 1,
                "enterFrom": "click",
                "generateId": submit_id,
                "isRegenerate": False,
            }
        ),
        "draft_content": json.dumps(
            {
                "type": "draft",
                "id": uuid_str(),
                "min_version": constants.DRAFT_MIN_VERSION,
                "min_features": [],
                "is_from_tsn": True,
                "version": constants.DRAFT_VERSION,
                "main_component_id": component_id,
                "component_list": [
                    {
                        "type": "image_base_component",
                        "id": component_id,
                        "min_version": constants.DRAFT_MIN_VERSION,
                        "aigc_mode": "workbench",
                        "metadata": {
                            "type": "",
                            "id": uuid_str(),
                            "created_platform": 3,
                            "created_platform_version": "",
                            "created_time_in_ms": str(now_ms),
                            "created_did": "",
                        },
                        "generate_type": "blend",
                        "abilities": {
                            "type": "",
                            "id": uuid_str(),
                            "blend": {
                                "type": "",
                                "id": uuid_str(),
                                "min_features": [],
                                "core_param": core_param,
                                "ability_list": ability_list,
                                "prompt_placeholder_info_list": [
                                    {"type": "", "id": uuid_str(), "ability_index": idx}
                                    for idx in range(len(ability_list))
                                ],
                                "postedit_param": {"type": "", "id": uuid_str(), "generate_type": 0},
                            },
                        },
                    }
                ],
            }
        ),
        "http_common_info": {
            "aid": constants.DEFAULT_ASSISTANT_ID_US if is_us else constants.DEFAULT_ASSISTANT_ID_CN
        },
    }

    result = request(
        "POST",
        "/mweb/v1/aigc_draft/generate",
        refresh_token,
        json=payload,
    )
    history_id = (result.get("aigc_data") or {}).get("history_record_id")
    if not history_id:
        raise JimengAPIError("记录ID不存在")

    poller = SmartPoller(expected_item_count=1, item_type="image")
    _, info = poller.poll(lambda: _poll_history(history_id, refresh_token), history_id=history_id)

    urls = _extract_urls(info.get("item_list") or [])
    if not urls:
        raise JimengAPIError("图生图未生成图片")
    return urls
