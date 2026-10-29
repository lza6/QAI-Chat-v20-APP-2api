import json, requests, time, sys
from probe import sign
URL="https://aibr.elway-mobile.com/chatCompletion"
def run(len_chars, label):
    content = "A"*len_chars + " Now reply with exactly: DONE"
    body=json.dumps({"messages":[{"role":"user","content":content}]},separators=(",",":"))
    t0=time.time()
    try:
        r=requests.post(URL,data=body.encode(),headers={"aibsg":sign(body),"Content-Type":"text/event-stream"},timeout=180,stream=True)
        if r.status_code!=200:
            print(f"{label:12} {len_chars:7} chars -> HTTP {r.status_code} {r.text[:100]}"); return
        out=[]
        for line in r.iter_lines(decode_unicode=True):
            if line and line.startswith("data:"):
                d=line[5:].strip()
                if not d: continue
                try: j=json.loads(d)
                except: continue
                if j.get("content"): out.append(j["content"])
                if j.get("finishReason"): break
        print(f"{label:12} {len_chars:7} chars -> OK {round(time.time()-t0,1)}s {''.join(out)[:80]!r}")
    except Exception as e:
        print(f"{label:12} {len_chars:7} chars -> EXC {type(e).__name__} {str(e)[:60]}")
for n in (4000, 16000, 40000, 100000, 200000, 400000):
    run(n, "ctx")
