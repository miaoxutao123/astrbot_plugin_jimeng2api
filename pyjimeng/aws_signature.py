from __future__ import annotations

import hashlib
import hmac
from urllib.parse import parse_qsl, urlsplit


def _sign(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


def create_signature(
    method: str,
    url: str,
    headers: dict[str, str],
    access_key_id: str,
    secret_access_key: str,
    session_token: str | None = None,
    payload: str = "",
) -> str:
    """
    Generate AWS v4 signature compatible with the Jimeng upload endpoints.
    """
    parsed = urlsplit(url)
    pathname = parsed.path or "/"
    query_pairs = sorted(parse_qsl(parsed.query, keep_blank_values=True))
    canonical_query = "&".join(f"{k}={v}" for k, v in query_pairs)

    timestamp = headers["x-amz-date"]
    date = timestamp[:8]
    region = "cn-north-1"
    service = "imagex"

    headers_to_sign: dict[str, str] = {"x-amz-date": timestamp}
    if session_token:
        headers_to_sign["x-amz-security-token"] = session_token

    payload_hash = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    if method.upper() != "POST" or not payload:
        payload_hash = hashlib.sha256(b"").hexdigest()
    else:
        headers_to_sign["x-amz-content-sha256"] = payload_hash

    signed_headers = ";".join(sorted(h.lower() for h in headers_to_sign))
    canonical_headers = "".join(
        f"{k.lower()}:{v.strip()}\n"
        for k, v in sorted(headers_to_sign.items(), key=lambda item: item[0].lower())
    )

    canonical_request = "\n".join(
        [
            method.upper(),
            pathname,
            canonical_query,
            canonical_headers,
            signed_headers,
            payload_hash,
        ]
    )

    scope = f"{date}/{region}/{service}/aws4_request"
    string_to_sign = "\n".join(
        [
            "AWS4-HMAC-SHA256",
            timestamp,
            scope,
            hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
        ]
    )

    k_date = _sign(("AWS4" + secret_access_key).encode("utf-8"), date)
    k_region = _sign(k_date, region)
    k_service = _sign(k_region, service)
    k_signing = _sign(k_service, "aws4_request")
    signature = hmac.new(k_signing, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()

    return f"AWS4-HMAC-SHA256 Credential={access_key_id}/{scope}, SignedHeaders={signed_headers}, Signature={signature}"

