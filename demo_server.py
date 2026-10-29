"""QAI Chat v20.0 -> OpenAI-compatible local gateway (demo).

    python demo_server.py [--port 8931]

Routes
    GET  /health
    GET  /v1/models
    POST /v1/chat/completions      stream=true | false
    GET  /                         demo.html

Design notes that the upstream forces on us (see ANALYSIS.md):
  * upstream accepts only {"messages":[...]} -> max_tokens/temperature/stop/logprobs
    are silently dropped; we report them as unsupported instead of pretending.
  * upstream is ALWAYS SSE -> non-streaming is an aggregation on our side.
  * upstream emits finishReason "length" when it hits its own hard cap (~3.5k CJK
    chars observed); we pass that through as OpenAI "length".
  * upstream has no usage block -> we emit an estimate flagged `estimated: true`.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import qai_upstream as up

MODEL_ID = "qai-v20"
MODEL_CREATED = 1750000000
MAX_BODY = 8 * 1024 * 1024
DEMO_API_KEY = os.environ.get("DEMO_API_KEY", "").strip()
HERE = os.path.dirname(os.path.abspath(__file__))
STARTED = time.time()


def json_bytes(obj) -> bytes:
    return json.dumps(obj, ensure_ascii=False).encode("utf-8")


def openai_chunk(cid: str, model: str, delta: dict, finish=None) -> bytes:
    payload = {
        "id": cid,
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }
    return b"data: " + json_bytes(payload) + b"\n\n"


def normalize_messages(raw) -> list[dict]:
    """OpenAI content may be a string or a list of {"type":"text","text":...} parts."""
    if not isinstance(raw, list):
        raise ValueError("messages must be an array")
    out: list[dict] = []
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("each message must be an object")
        content = item.get("content", "")
        if isinstance(content, list):
            content = "".join(
                part.get("text", "")
                for part in content
                if isinstance(part, dict) and part.get("type") in (None, "text")
            )
        out.append({"role": str(item.get("role") or "user"), "content": str(content)})
    if not out:
        raise ValueError("messages must not be empty")
    return out


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "qai-demo/1.0"

    # ---- plumbing -------------------------------------------------------
    def log_message(self, fmt, *args):  # quieter than default, keep one line
        sys.stderr.write("[%s] %s\n" % (time.strftime("%H:%M:%S"), fmt % args))

    def _send(self, code: int, body: bytes, ctype="application/json; charset=utf-8", extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _error(self, code: int, message: str, etype="invalid_request_error"):
        self._send(code, json_bytes({"error": {"message": message, "type": etype, "code": code}}))

    def _authorized(self) -> bool:
        if not DEMO_API_KEY:
            return True
        auth = self.headers.get("Authorization", "")
        return auth.removeprefix("Bearer ").strip() == DEMO_API_KEY

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        self.send_header("Content-Length", "0")
        self.end_headers()

    # ---- routes ---------------------------------------------------------
    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/health":
            return self._send(200, json_bytes({
                "status": "ok",
                "upstream": up.BASE_URL + up.CHAT_PATH,
                "model": MODEL_ID,
                "uptime_s": round(time.time() - STARTED, 1),
            }))
        if path == "/v1/models":
            return self._send(200, json_bytes({"object": "list", "data": [{
                "id": MODEL_ID, "object": "model", "created": MODEL_CREATED,
                "owned_by": "qai-v20-upstream",
            }]}))
        if path in ("/", "/index.html"):
            try:
                with open(os.path.join(HERE, "demo.html"), "rb") as fh:
                    return self._send(200, fh.read(), "text/html; charset=utf-8")
            except OSError:
                return self._error(500, "demo.html missing")
        return self._error(404, f"no route {path}")

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        if path != "/v1/chat/completions":
            return self._error(404, f"no route {path}")
        if not self._authorized():
            return self._error(401, "invalid api key", "authentication_error")
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0 or length > MAX_BODY:
                return self._error(400, "missing or oversized body")
            req = json.loads(self.rfile.read(length).decode("utf-8"))
            messages = normalize_messages(req.get("messages"))
        except (ValueError, json.JSONDecodeError) as exc:
            return self._error(400, str(exc))

        stream = bool(req.get("stream"))
        model = str(req.get("model") or MODEL_ID)
        cid = "chatcmpl-" + uuid.uuid4().hex[:24]
        prompt_text = "".join(m["content"] for m in messages)

        ignored = [k for k in ("max_tokens", "temperature", "top_p", "stop",
                               "presence_penalty", "frequency_penalty", "n", "logprobs")
                   if req.get(k) not in (None, [], {})]

        try:
            resp = self._open_upstream(messages)
        except Exception as exc:  # network-level failure before any byte of stream
            return self._error(502, f"upstream unreachable: {exc}", "upstream_error")

        if resp.status_code != 200:
            body = resp.text[:400]
            resp.close()
            code = 503 if resp.status_code in (502, 503, 504) else 502
            return self._error(code, f"upstream {resp.status_code}: {body}", "upstream_error")

        if not stream:
            return self._non_stream(resp, cid, model, prompt_text, ignored)
        return self._stream(resp, cid, model, prompt_text, ignored)

    def _open_upstream(self, messages, retries=1):
        """Upstream sheds load with 503 under fan-out; retry once before failing."""
        resp = up.open_stream(messages)
        for _ in range(retries):
            if resp.status_code not in (502, 503, 504):
                break
            resp.close()
            time.sleep(0.8)
            self.log_message("upstream %s, retrying", resp.status_code)
            resp = up.open_stream(messages)
        return resp

    # ---- writers --------------------------------------------------------
    def _non_stream(self, resp, cid, model, prompt_text, ignored):
        parts: list[str] = []
        finish = "stop"
        try:
            for delta, why in up.iter_deltas(resp):
                if delta:
                    parts.append(delta)
                if why:
                    finish = why
        finally:
            resp.close()
        text = "".join(parts)
        payload = {
            "id": cid, "object": "chat.completion", "created": int(time.time()),
            "model": model,
            "choices": [{"index": 0, "message": {"role": "assistant", "content": text},
                         "finish_reason": finish}],
            "usage": {
                "prompt_tokens": up.estimate_tokens(prompt_text),
                "completion_tokens": up.estimate_tokens(text),
                "total_tokens": up.estimate_tokens(prompt_text) + up.estimate_tokens(text),
                "estimated": True,
            },
        }
        if ignored:
            payload["x_qai_ignored_params"] = ignored
        self._send(200, json_bytes(payload))

    def _stream(self, resp, cid, model, prompt_text, ignored):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()

        def write(payload: bytes):
            self.wfile.write(b"%x\r\n" % len(payload) + payload + b"\r\n")
            self.wfile.flush()

        text_parts: list[str] = []
        finish = "stop"
        try:
            write(openai_chunk(cid, model, {"role": "assistant", "content": ""}))
            for delta, why in up.iter_deltas(resp):
                if delta:
                    text_parts.append(delta)
                    write(openai_chunk(cid, model, {"content": delta}))
                if why:
                    finish = why
                    break
            write(openai_chunk(cid, model, {}, finish))
            done = {"id": cid, "object": "chat.completion.chunk", "created": int(time.time()),
                    "model": model, "choices": [],
                    "usage": {
                        "prompt_tokens": up.estimate_tokens(prompt_text),
                        "completion_tokens": up.estimate_tokens("".join(text_parts)),
                        "total_tokens": up.estimate_tokens(prompt_text)
                        + up.estimate_tokens("".join(text_parts)),
                        "estimated": True,
                    }}
            if ignored:
                done["x_qai_ignored_params"] = ignored
            write(b"data: " + json_bytes(done) + b"\n\n")
            write(b"data: [DONE]\n\n")
            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            # client closed the tab mid-stream; upstream response still needs closing
            pass
        finally:
            resp.close()
            self.close_connection = True


def port_already_serving(host: str, port: int) -> bool:
    """Windows lets a second process bind a port Python already listens on
    (SO_REUSEADDR), so a failed bind is not a reliable "in use" signal --
    probe with connect_ex instead, otherwise you silently get two servers
    fighting over one port."""
    with socket.socket() as probe:
        probe.settimeout(0.4)
        return probe.connect_ex((host, port)) == 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8931)
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()
    if port_already_serving(args.host, args.port):
        raise SystemExit(
            f"{args.host}:{args.port} is already serving something. "
            "Pick another port with --port (see ANALYSIS.md 9.7)."
        )
    try:
        srv = ThreadingHTTPServer((args.host, args.port), Handler)
    except OSError as exc:
        raise SystemExit(
            f"cannot bind {args.host}:{args.port} ({exc}). "
            "Another local service may already hold this port -- pick a free one with --port."
        ) from exc
    srv.daemon_threads = True
    print(f"QAI v20 demo gateway -> http://{args.host}:{args.port}  (upstream {up.BASE_URL})")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")
    finally:
        srv.server_close()


if __name__ == "__main__":
    main()
