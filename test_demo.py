"""End-to-end checks against a running demo gateway.

    python demo_server.py --port 8931      # terminal 1
    python test_demo.py --port 8931        # terminal 2
"""

from __future__ import annotations

import argparse
import json
import sys
import time

import requests

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f"  -- {detail}" if detail and not cond else ""))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8931)
    ap.add_argument("--host", default="127.0.0.1")
    a = ap.parse_args()
    base = f"http://{a.host}:{a.port}"

    print("health / models")
    r = requests.get(base + "/health", timeout=10)
    check("health 200 ok", r.status_code == 200 and r.json().get("status") == "ok", r.text[:120])
    r = requests.get(base + "/v1/models", timeout=10)
    mids = [m["id"] for m in r.json().get("data", [])]
    check("models lists qai-v20", r.status_code == 200 and "qai-v20" in mids, str(mids))

    print("non-stream chat")
    t0 = time.time()
    r = requests.post(base + "/v1/chat/completions", json={
        "model": "qai-v20",
        "messages": [{"role": "user", "content": "Reply with exactly: PONG"}],
    }, timeout=120)
    j = r.json()
    text = j.get("choices", [{}])[0].get("message", {}).get("content", "")
    check("non-stream 200", r.status_code == 200, r.text[:200])
    check("non-stream has content", bool(text.strip()), repr(text[:80]))
    check("non-stream usage estimated", j.get("usage", {}).get("estimated") is True, str(j.get("usage")))
    check("non-stream object type", j.get("object") == "chat.completion", str(j.get("object")))
    print(f"       -> {text.strip()[:60]!r} in {time.time()-t0:.2f}s")

    print("non-stream 中文")
    r = requests.post(base + "/v1/chat/completions", json={
        "messages": [{"role": "user", "content": "用中文回答：中国的首都是哪里？只回答城市名。"}],
    }, timeout=120)
    text = r.json()["choices"][0]["message"]["content"]
    check("中文未乱码", "北京" in text and "�" not in text, repr(text[:80]))

    print("stream chat")
    chunks, done, finish, role_seen, content = 0, False, None, False, ""
    with requests.post(base + "/v1/chat/completions", json={
        "stream": True,
        "messages": [{"role": "user", "content": "Count 1 to 5, comma separated."}],
    }, timeout=120, stream=True) as r:
        check("stream 200", r.status_code == 200, str(r.status_code))
        check("stream content-type sse",
              "text/event-stream" in r.headers.get("content-type", ""), r.headers.get("content-type", ""))
        r.encoding = "utf-8"
        for line in r.iter_lines(decode_unicode=True):
            if not line or not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                done = True
                break
            obj = json.loads(data)
            check_obj = obj.get("object") == "chat.completion.chunk"
            if not check_obj:
                check("chunk object type", False, str(obj)[:120])
            ch = obj["choices"][0] if obj.get("choices") else None
            if ch:
                chunks += 1
                if ch["delta"].get("role"):
                    role_seen = True
                if ch["delta"].get("content"):
                    content += ch["delta"]["content"]
                if ch.get("finish_reason"):
                    finish = ch["finish_reason"]
    check("stream terminated with [DONE]", done)
    check("stream role chunk seen", role_seen)
    check("stream chunks > 3", chunks > 3, str(chunks))
    check("stream finish_reason set", finish in ("stop", "length"), str(finish))
    check("stream content sane", len(content.strip()) > 0, repr(content[:80]))
    print(f"       -> {content.strip()[:60]!r} ({chunks} chunks, finish={finish})")

    print("multi-turn memory")
    r = requests.post(base + "/v1/chat/completions", json={
        "messages": [
            {"role": "user", "content": "My codename is Delta-7. Remember it."},
            {"role": "assistant", "content": "Noted."},
            {"role": "user", "content": "What is my codename?"},
        ],
    }, timeout=120)
    text = r.json()["choices"][0]["message"]["content"]
    check("multi-turn recalls context", "Delta-7" in text, repr(text[:100]))

    print("ignored params surfaced")
    r = requests.post(base + "/v1/chat/completions", json={
        "messages": [{"role": "user", "content": "say hi"}],
        "max_tokens": 5, "temperature": 0.2,
    }, timeout=120)
    ignored = r.json().get("x_qai_ignored_params", [])
    check("ignored params reported", set(ignored) >= {"max_tokens", "temperature"}, str(ignored))

    print("error paths")
    r = requests.post(base + "/v1/chat/completions", json={"messages": []}, timeout=30)
    check("empty messages -> 400", r.status_code == 400, str(r.status_code))
    r = requests.post(base + "/v1/chat/completions", data=b"not json", timeout=30,
                      headers={"Content-Type": "application/json"})
    check("bad json -> 400", r.status_code == 400, str(r.status_code))
    r = requests.get(base + "/nope", timeout=10)
    check("unknown route -> 404", r.status_code == 404, str(r.status_code))
    r = requests.get(base + "/", timeout=10)
    check("demo.html served", r.status_code == 200 and "<html" in r.text.lower(), str(r.status_code))

    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        print("failed:", FAIL)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
