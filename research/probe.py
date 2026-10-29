import base64, hashlib, json, time, sys
import requests
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import padding

PUB = "MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEA7TIHog89K/IEIO3LF2/B9A8Gi/DkXZG6Y+PoydoKD9JrxR9CHRP1Pmbsm09oY9ycxyw7Tfb4ljt9ySxOmzMALoN1mJx0ontUe9+BoAxddK2NQDhRz55B/HUUAGFJ9tGTC8dlkAgc0OnEBR9O60x5KDOltgNR/iriGdlxMoTGRnGS6LOXGiRQlAzyj1hmyTl9vOePkWTvj/GG3W1sWaw9LTvd83WZdxvgGtcjMe4ZqW86BGTmG32yqgg+uQCew88BaSRAmTxpH/YqKWsBD7mk5pS80+ss2mS6XeQRtqN82DActjrgOoOiaL9nZdh7DZJIY2Sqz8aHh9vkHeqwJi1iDwIDAQAB"
KEY = serialization.load_der_public_key(base64.b64decode(PUB))

def sign(body: str) -> str:
    md5 = hashlib.md5(body.encode()).digest()
    return base64.b64encode(KEY.encrypt(md5, padding.PKCS1v15())).decode()

def call(messages, model="default", url="https://aibr.elway-mobile.com/chatCompletion", timeout=60):
    body = json.dumps({"messages": messages}, ensure_ascii=False, separators=(",", ":"))
    h = {
        "aibsg": sign(body), "aibpf": "android", "aibmd": model,
        "appmv": "14", "appvr": "1440",
        "Content-Type": "text/event-stream", "Connection": "keep-alive",
        "User-Agent": "ktor-client",
    }
    t0 = time.time()
    r = requests.post(url, data=body.encode(), headers=h, timeout=timeout, stream=True)
    return r, t0, body

if __name__ == "__main__":
    r, t0, body = call([{"role":"user","content":"say hi in 5 words"}])
    print("status", r.status_code, "ct", r.headers.get("content-type"))
    full = ""
    for line in r.iter_lines(decode_unicode=True):
        if line is None: continue
        print("RAW:", line[:200])
        full += line + "\n"
    print("elapsed", round(time.time()-t0,2))
