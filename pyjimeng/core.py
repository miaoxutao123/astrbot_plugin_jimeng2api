from __future__ import annotations

import random
import time
from typing import Any, Dict, Iterable, Optional
from urllib.parse import urlparse

import requests

from . import constants
from .errors import JimengAPIError
from .logging import get_logger
from .util import md5, random_fingerprint, unix_timestamp, uuid_str

logger = get_logger()

SESSION = requests.Session()

MODEL_NAME = "jimeng"
DEVICE_ID = random_fingerprint()
WEB_ID = random_fingerprint()
USER_ID = uuid_str(False)

FAKE_HEADERS: Dict[str, str] = {
    "Accept": "application/json, text/plain, */*",
    # requests 默认支持 gzip/deflate，这里显式约束避免服务器返回 brotli/zstd
    "Accept-Encoding": "gzip, deflate",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Cache-Control": "no-cache",
    "Last-Event-ID": "undefined",
    "Appvr": constants.VERSION_CODE,
    "Pragma": "no-cache",
    "Priority": "u=1, i",
    "Pf": constants.PLATFORM_CODE,
    "Sec-Ch-Ua": '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
}


def is_us_token(token: str) -> bool:
    return token.lower().startswith("us-")


def normalize_token(token: str) -> str:
    return token[3:] if is_us_token(token) else token


def acquire_token(refresh_token: str) -> str:
    # Currently refresh token is equal to access token.
    return refresh_token


def generate_cookie(refresh_token: str) -> str:
    us = is_us_token(refresh_token)
    token = normalize_token(refresh_token)
    now = unix_timestamp()
    region = "us" if us else "cn-gd"
    return "; ".join(
        [
            f"_tea_web_id={WEB_ID}",
            "is_staff_user=false",
            f"store-region={region}",
            "store-region-src=uid",
            f"sid_guard={token}%7C{now}%7C5184000%7CMon%2C+03-Feb-2025+08%3A17%3A09+GMT",
            f"uid_tt={USER_ID}",
            f"uid_tt_ss={USER_ID}",
            f"sid_tt={token}",
            f"sessionid={token}",
            f"sessionid_ss={token}",
            f"sid_tt={token}",
        ]
    )


def sign_request(uri: str, device_time: int) -> str:
    suffix = uri[-7:] if len(uri) >= 7 else uri
    raw = f"9e2c|{suffix}|{constants.PLATFORM_CODE}|{constants.VERSION_CODE}|{device_time}||11ac"
    return md5(raw)


def build_default_params(
    *,
    refresh_token: str,
    base_uri: str,
    extra_params: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    params = dict(extra_params or {})
    if is_us_token(refresh_token):
        params.setdefault("aid", constants.DEFAULT_ASSISTANT_ID_US)
        params.setdefault("device_platform", "web")
        params.setdefault("region", constants.REGION_US)
        params.setdefault("da_version", constants.DA_VERSION)
        params.setdefault("web_version", constants.WEB_VERSION)
        params.setdefault("web_component_open_flag", 1)
        params.setdefault("aigc_features", constants.AIGC_FEATURES)
    else:
        params.setdefault("aid", constants.DEFAULT_ASSISTANT_ID_CN)
        params.setdefault("device_platform", "web")
        params.setdefault("region", constants.REGION_CN)
        params.setdefault("webId", WEB_ID)
        params.setdefault("da_version", constants.DA_VERSION)
        params.setdefault("web_component_open_flag", 1)
        params.setdefault("web_version", constants.WEB_VERSION)
        params.setdefault("aigc_features", constants.AIGC_FEATURES)
    return params


def choose_base_url(refresh_token: str, uri: str) -> str:
    if is_us_token(refresh_token):
        if uri.startswith("/commerce/"):
            return constants.BASE_URL_US_COMMERCE
        return constants.BASE_URL_DREAMINA_US
    return constants.BASE_URL_CN


def check_result(response: requests.Response) -> Any:
    try:
        payload = response.json()
    except ValueError as exc:  # pragma: no cover - server always returns json
        raise JimengAPIError(f"非JSON响应: {response.text[:200]}") from exc

    ret = payload.get("ret")
    if ret is None:
        return payload

    if str(ret) == "0":
        return payload.get("data")

    errmsg = payload.get("errmsg") or payload.get("data", {}).get("msg") or "请求失败"
    raise JimengAPIError(errmsg, status_code=response.status_code)


def request(
    method: str,
    uri: str,
    refresh_token: str,
    *,
    params: Optional[Dict[str, Any]] = None,
    data: Optional[Any] = None,
    headers: Optional[Dict[str, str]] = None,
    json: Optional[Any] = None,
    files: Optional[Any] = None,
    no_default_params: bool = False,
    timeout: float = 45.0,
    stream: bool = False,
) -> Any:
    base_url = choose_base_url(refresh_token, uri)
    parsed = urlparse(base_url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    token = normalize_token(refresh_token)
    device_time = unix_timestamp()
    signature = sign_request(uri, device_time)

    request_params = params or {}
    if not no_default_params:
        request_params = build_default_params(
            refresh_token=refresh_token, base_uri=uri, extra_params=request_params
        )

    req_headers = {
        **FAKE_HEADERS,
        "Origin": origin,
        "Referer": origin,
        "Appid": str(constants.DEFAULT_ASSISTANT_ID_US if is_us_token(refresh_token) else constants.DEFAULT_ASSISTANT_ID_CN),
        "Cookie": generate_cookie(refresh_token),
        "Device-Time": str(device_time),
        "Sign": signature,
        "Sign-Ver": "1",
    }
    if headers:
        req_headers.update(headers)

    url = base_url + uri
    retries = 0
    max_retries = constants.RETRY_CONFIG["MAX_RETRY_COUNT"]
    delay = constants.RETRY_CONFIG["RETRY_DELAY"]

    while True:
        try:
            logger.info("请求 %s %s params=%s", method.upper(), url, request_params)
            resp = SESSION.request(
                method=method.upper(),
                url=url,
                params=request_params,
                data=data,
                json=json,
                headers=req_headers,
                files=files,
                timeout=timeout,
                stream=stream,
            )
        except requests.RequestException as exc:
            if retries < max_retries:
                retries += 1
                logger.warning("请求异常 %s，%ss后重试 (%s/%s)", exc, delay, retries, max_retries)
                time.sleep(delay)
                continue
            raise JimengAPIError(str(exc)) from exc

        if stream:
            resp.raise_for_status()
            return resp

        if resp.status_code >= 400 and retries < max_retries:
            retries += 1
            logger.warning(
                "请求失败 status=%s %s，%ss后重试 (%s/%s)",
                resp.status_code,
                resp.text[:200],
                delay,
                retries,
                max_retries,
            )
            time.sleep(delay)
            continue

        resp.raise_for_status()
        return check_result(resp)


def token_split(authorization: str) -> Iterable[str]:
    return authorization.replace("Bearer ", "").split(",")


def get_credit(refresh_token: str) -> Dict[str, Any]:
    result = request(
        "POST",
        "/commerce/v1/benefits/user_credit",
        refresh_token,
        json={},
        headers={"Referer": "https://jimeng.jianying.com/ai-tool/image/generate"},
        no_default_params=True,
    )
    credit = result["credit"]
    return {
        "giftCredit": credit["gift_credit"],
        "purchaseCredit": credit["purchase_credit"],
        "vipCredit": credit["vip_credit"],
        "totalCredit": credit["gift_credit"] + credit["purchase_credit"] + credit["vip_credit"],
    }


def receive_credit(refresh_token: str) -> Any:
    return request(
        "POST",
        "/commerce/v1/benefits/credit_receive",
        refresh_token,
        json={"time_zone": "Asia/Shanghai"},
        headers={"Referer": "https://jimeng.jianying.com/ai-tool/image/generate"},
    )


def get_token_live_status(refresh_token: str) -> bool:
    try:
        result = request(
            "POST",
            "/passport/account/info/v2",
            refresh_token,
            params={"account_sdk_source": "web"},
        )
        return bool(result.get("user_id"))
    except JimengAPIError:
        return False
