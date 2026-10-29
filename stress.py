"""Load test for the QAI v20 upstream and the local gateway.

    python stress.py --target upstream --levels 1,5,10,16 --per-level 8
    python stress.py --target gateway  --levels 1,5,10 --per-level 8
    python stress.py --target upstream --duration 60 --concurrency 6   # soak

Reports success rate, TTFB, TTFT, total latency percentiles, output rate and the
distribution of upstream error codes.
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
from concurrent.futures import ThreadPoolExecutor

import requests

import qai_upstream as up

GATEWAY = "http://127.0.0.1:8931/v1/chat/completions"
PROMPT_SHORT = "Reply with exactly: PONG"
PROMPT_MED = "用大约150字介绍长江的地理与文化意义。"


def percentile(values: list[float], p: float):
    if not values:
        return None
    ordered = sorted(values)
    return round(ordered[min(len(ordered) - 1, int(len(ordered) * p))], 2)


def call_upstream(idx: int, prompt: str) -> dict:
    t0 = time.time()
    try:
        resp = up.open_stream([{"role": "user", "content": prompt}], timeout=(20, 180))
        ttfb = time.time() - t0
        if resp.status_code != 200:
            body = resp.text[:120]
            resp.close()
            return {"ok": False, "code": resp.status_code, "body": body, "dur": time.time() - t0}
        ttft = None
        chars = 0
        finish = None
        for delta, why in up.iter_deltas(resp):
            if delta:
                if ttft is None:
                    ttft = time.time() - t0
                chars += len(delta)
            if why:
                finish = why
                break
        dur = time.time() - t0
        resp.close()
        return {"ok": True, "code": 200, "ttfb": ttfb, "ttft": ttft, "dur": dur,
                "chars": chars, "finish": finish}
    except Exception as exc:
        return {"ok": False, "code": type(exc).__name__, "body": str(exc)[:120],
                "dur": time.time() - t0}


def call_gateway(idx: int, prompt: str) -> dict:
    t0 = time.time()
    try:
        with requests.post(GATEWAY, json={"model": "qai-v20", "stream": True,
                                          "messages": [{"role": "user", "content": prompt}]},
                           timeout=240, stream=True) as resp:
            ttfb = time.time() - t0
            if resp.status_code != 200:
                return {"ok": False, "code": resp.status_code, "body": resp.text[:120],
                        "dur": time.time() - t0}
            resp.encoding = "utf-8"
            ttft = None
            chars = 0
            finish = None
            for line in resp.iter_lines(decode_unicode=True):
                if not line or not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                obj = json.loads(data)
                choice = obj["choices"][0] if obj.get("choices") else None
                if choice and choice["delta"].get("content"):
                    if ttft is None:
                        ttft = time.time() - t0
                    chars += len(choice["delta"]["content"])
                if choice and choice.get("finish_reason"):
                    finish = choice["finish_reason"]
            return {"ok": True, "code": 200, "ttfb": ttfb, "ttft": ttft,
                    "dur": time.time() - t0, "chars": chars, "finish": finish}
    except Exception as exc:
        return {"ok": False, "code": type(exc).__name__, "body": str(exc)[:120],
                "dur": time.time() - t0}


def summarize(label: str, results: list[dict]) -> None:
    ok = [r for r in results if r["ok"]]
    durs = [r["dur"] for r in ok]
    ttfts = [r["ttft"] for r in ok if r.get("ttft")]
    rates = [r["chars"] / r["dur"] for r in ok if r.get("chars") and r["dur"] > 0]
    errors: dict[str, int] = {}
    for r in results:
        if not r["ok"]:
            errors[str(r["code"])] = errors.get(str(r["code"]), 0) + 1
    finishes: dict[str, int] = {}
    for r in ok:
        finishes[str(r.get("finish"))] = finishes.get(str(r.get("finish")), 0) + 1
    print(
        f"{label:18} ok={len(ok):3}/{len(results):3} "
        f"dur p50={percentile(durs,.5)} p90={percentile(durs,.9)} max={round(max(durs),2) if durs else '-'} "
        f"ttft p50={percentile(ttfts,.5)} "
        f"chars/req={round(statistics.mean(r.get('chars',0) for r in ok),0) if ok else 0} "
        f"rate={round(statistics.mean(rates),1) if rates else '-'}c/s "
        f"finish={finishes} errors={errors}"
    )


def gate_levels(fn, levels: list[int], per_level: int, prompt: str) -> None:
    for conc in levels:
        with ThreadPoolExecutor(max_workers=conc) as pool:
            results = list(pool.map(lambda i, f=fn, p=prompt: f(i, p), range(per_level)))
        summarize(f"conc={conc}", results)


def soak(fn, duration: int, concurrency: int, prompt: str) -> None:
    """Keep `concurrency` workers busy for `duration` seconds; report the tail."""
    deadline = time.time() + duration
    results: list[dict] = []
    lock = __import__("threading").Lock()

    def worker(ident: int):
        while time.time() < deadline:
            r = fn(ident, prompt)
            r["t"] = round(time.time() - (deadline - duration), 1)
            with lock:
                results.append(r)

    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        list(pool.map(worker, range(concurrency)))
    summarize(f"soak{concurrency}x{duration}s", results)
    fails = [r for r in results if not r["ok"]]
    if fails:
        print("  first failures:", [(f["t"], f["code"], f.get("body", "")[:60]) for f in fails[:5]])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", choices=("upstream", "gateway"), default="upstream")
    ap.add_argument("--levels", default="1,5,10,16")
    ap.add_argument("--per-level", type=int, default=8)
    ap.add_argument("--prompt", choices=("short", "med"), default="med")
    ap.add_argument("--duration", type=int, default=0, help="soak seconds (overrides --levels)")
    ap.add_argument("--concurrency", type=int, default=6)
    a = ap.parse_args()

    fn = call_upstream if a.target == "upstream" else call_gateway
    prompt = PROMPT_SHORT if a.prompt == "short" else PROMPT_MED
    print(f"target={a.target} prompt={a.prompt} ({len(prompt)} chars)")
    if a.duration:
        soak(fn, a.duration, a.concurrency, prompt)
    else:
        gate_levels(fn, [int(x) for x in a.levels.split(",") if x.strip()], a.per_level, prompt)


if __name__ == "__main__":
    main()
