"""QAI Chat v20.0 (chat.gpt.open.ai) upstream protocol client.

Reverse-engineered protocol (verified live 2026-10-29):

    POST https://aibr.elway-mobile.com/chatCompletion
    Headers:
        aibsg: base64( RSA_PKCS1v15_sign_style( md5(raw_json_body_bytes) ) )
        aibmd: <model alias>        (server IGNORES it -- see ANALYSIS.md)
        Content-Type: text/event-stream
    Body (plaintext UTF-8 JSON):
        {"messages":[{"role":"user","content":"..."}]}
    Response:
        text/event-stream, one JSON object per "data:" line
        data:{"role":"assistant","content":"Hello"}     <- first chunk carries role
        data:{"content":" there"}
        data:{"finishReason":"stop"}                   <- terminal chunk, no content

Nothing else gates the call: no account, no device id, no quota header, no
Bearer token. The only barrier is the signature, and the RSA public key is
baked into the APK, so anyone holding the APK can mint it. `aibpf`, `appmv`,
`appvr` are decoration -- the server answers 200 with `aibsg` alone.

No dependencies beyond `requests` + `cryptography`.
"""

from __future__ import annotations

import base64
import hashlib
import json

import requests
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import padding

BASE_URL = "https://aibr.elway-mobile.com"
CHAT_PATH = "/chatCompletion"

# md.elway.evo.utils.Crypto.getPKey() -- X.509 SubjectPublicKeyInfo, 2048-bit RSA.
RSA_PUBLIC_KEY_B64 = (
    "MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEA7TIHog89K/IEIO3LF2/B9A8Gi/DkXZG6"
    "Y+PoydoKD9JrxR9CHRP1Pmbsm09oY9ycxyw7Tfb4ljt9ySxOmzMALoN1mJx0ontUe9+BoAxddK2NQ"
    "DhRz55B/HUUAGFJ9tGTC8dlkAgc0OnEBR9O60x5KDOltgNR/iriGdlxMoTGRnGS6LOXGiRQlAzyj1"
    "hmyTl9vOePkWTvj/GG3W1sWaw9LTvd83WZdxvgGtcjMe4ZqW86BGTmG32yqgg+uQCew88BaSRAmTx"
    "pH/YqKWsBD7mk5pS80+ss2mS6XeQRtqN82DActjrgOoOiaL9nZdh7DZJIY2Sqz8aHh9vkHeqwJi1i"
    "DwIDAQAB"
)

DEFAULT_MODEL_ALIAS = "t1"  # value the app's Firebase Remote Config ships today


class UpstreamError(Exception):
    """Upstream returned a non-200 response or an unusable stream."""

    def __init__(self, status: int, message: str, body: str = ""):
        super().__init__(message)
        self.status = status
        self.message = message
        self.body = body


_pubkey = serialization.load_der_public_key(base64.b64decode(RSA_PUBLIC_KEY_B64))


def sign_body(raw_body: bytes) -> str:
    """Reproduce Crypto.encrypt(): RSA/ECB/PKCS1Padding over MD5(input)."""
    digest = hashlib.md5(raw_body).digest()
    return base64.b64encode(_pubkey.encrypt(digest, padding.PKCS1v15())).decode()


def serialize_request(messages: list[dict]) -> bytes:
    """Upstream DTO only carries `messages`; every other OpenAI field is dropped."""
    payload = {
        "messages": [
            {"role": str(m.get("role", "user")), "content": str(m.get("content", ""))}
            for m in messages
        ]
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _headers(raw_body: bytes, model: str) -> dict:
    return {
        "aibsg": sign_body(raw_body),
        "aibmd": model or DEFAULT_MODEL_ALIAS,
        "aibpf": "android",
        "appmv": "14",
        "appvr": "1440",
        "Content-Type": "text/event-stream",
        "Connection": "keep-alive",
        "Accept": "text/event-stream",
    }


def open_stream(
    messages: list[dict],
    model: str = DEFAULT_MODEL_ALIAS,
    timeout: tuple[float, float] = (20.0, 180.0),
    base_url: str = BASE_URL,
) -> requests.Response:
    """Open the SSE response. Caller owns the response object."""
    raw = serialize_request(messages)
    resp = requests.post(
        base_url.rstrip("/") + CHAT_PATH,
        data=raw,
        headers=_headers(raw, model),
        stream=True,
        timeout=timeout,
    )
    # Upstream omits charset; requests would fall back to ISO-8859-1 and mangle
    # every CJK character. Force UTF-8 before anyone touches resp.text/lines.
    resp.encoding = "utf-8"
    return resp


def iter_deltas(resp: requests.Response, stop_on_finish: bool = True):
    """Yield (content_delta, finish_reason) tuples from an open SSE response."""
    for line in resp.iter_lines(decode_unicode=True):
        if not line:
            continue
        if line.startswith("data:"):
            line = line[5:]
        line = line.strip()
        if not line or line == "[DONE]":
            continue
        try:
            chunk = json.loads(line)
        except json.JSONDecodeError:
            continue
        content = chunk.get("content")
        finish = chunk.get("finishReason")
        if content:
            yield content, None
        if finish:
            if stop_on_finish:
                yield "", finish
                return
            yield "", finish


def chat_completion(
    messages: list[dict],
    model: str = DEFAULT_MODEL_ALIAS,
    timeout: tuple[float, float] = (20.0, 180.0),
    retries: int = 2,
    base_url: str = BASE_URL,
) -> tuple[str, str]:
    """Non-streaming convenience call -> (text, finish_reason)."""
    last: Exception | None = None
    for attempt in range(retries + 1):
        try:
            resp = open_stream(messages, model=model, timeout=timeout, base_url=base_url)
            if resp.status_code != 200:
                body = resp.text[:400]
                resp.close()
                # 503 = backend shed load; 500 = signature/body mismatch. Retry the
                # former, fail fast on the latter.
                if resp.status_code in (502, 503, 504) and attempt < retries:
                    last = UpstreamError(resp.status_code, "upstream overloaded", body)
                    continue
                raise UpstreamError(resp.status_code, "upstream returned non-200", body)
            parts: list[str] = []
            finish = "stop"
            for delta, why in iter_deltas(resp):
                if delta:
                    parts.append(delta)
                if why:
                    finish = why
            resp.close()
            return "".join(parts), finish
        except UpstreamError:
            raise
        except requests.RequestException as exc:
            last = exc
            if attempt >= retries:
                break
    raise UpstreamError(0, f"upstream unreachable: {last}")


def estimate_tokens(text: str) -> int:
    """Rough heuristic: CJK ~1 token/char, ASCII ~4 chars/token."""
    if not text:
        return 0
    cjk = sum(1 for ch in text if "一" <= ch <= "鿿")
    other = len(text) - cjk
    return cjk + max(1, other // 4) if other else cjk
