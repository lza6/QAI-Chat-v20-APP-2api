from probe import call
import concurrent.futures as cf, json

CANDIDATES = ["default","gpt-4o","gpt-4o-mini","gpt-4.1","gpt-4.1-mini","gpt-4-turbo","gpt-3.5-turbo",
 "o1","o3-mini","o4-mini","claude-3-5-sonnet","claude-3-7-sonnet","claude-sonnet-4",
 "gemini-2.0-flash","gemini-2.5-pro","gemini-2.5-flash","deepseek-chat","deepseek-r1",
 "qwen-max","llama-3.1-70b","grok-3","mistral-large","gpt-5","gpt-5-mini"]

def probe(model):
    try:
        r, t0, body = call([{"role":"user","content":"reply with the exact word: PONG"}], model=model, timeout=45)
        if r.status_code != 200:
            return model, f"HTTP {r.status_code}", r.text[:160]
        out=[]
        for line in r.iter_lines(decode_unicode=True):
            if line and line.startswith("data:"):
                d=line[5:].strip()
                if d:
                    try: j=json.loads(d)
                    except: return model,"BADJSON",d[:120]
                    if j.get("content"): out.append(j["content"])
                    if j.get("finishReason"): break
        return model, "OK", "".join(out)[:120]
    except Exception as e:
        return model, "ERR", str(e)[:120]

with cf.ThreadPoolExecutor(max_workers=6) as ex:
    for m,s,txt in ex.map(probe, CANDIDATES):
        print(f"{m:24} {s:12} {txt!r}")
