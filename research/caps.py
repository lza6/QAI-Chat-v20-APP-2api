import json, requests, time
from probe import sign
URL="https://aibr.elway-mobile.com/chatCompletion"
def post(payload, model="t1", timeout=90, extra_headers=None):
    body=json.dumps(payload,ensure_ascii=False,separators=(",",":"))
    h={"aibsg":sign(body),"aibmd":model,"Content-Type":"text/event-stream"}
    if extra_headers: h.update(extra_headers)
    r=requests.post(URL,data=body.encode(),headers=h,timeout=timeout,stream=True)
    return r
def collect(r):
    txt=[];fin=None
    for line in r.iter_lines(decode_unicode=True):
        if not line or not line.startswith("data:"): continue
        d=line[5:].strip()
        if not d: continue
        j=json.loads(d)
        if j.get("content"): txt.append(j["content"])
        if j.get("finishReason"): fin=j["finishReason"]; break
    return "".join(txt), fin

def t(name,payload,**kw):
    t0=time.time()
    try:
        r=post(payload,**kw)
        if r.status_code!=200:
            print(f"{name:30} HTTP {r.status_code} {r.text[:120]}"); return
        txt,fin=collect(r)
        print(f"{name:30} OK fin={fin} {round(time.time()-t0,2)}s :: {txt[:150]!r}")
    except Exception as e:
        print(f"{name:30} EXC {str(e)[:120]}")

t("empty messages", {"messages":[]})
t("system only", {"messages":[{"role":"system","content":"You must answer in ALL CAPS only."}]})
t("system+user caps", {"messages":[{"role":"system","content":"You must answer in ALL CAPS only."},{"role":"user","content":"say hello"}]})
t("multi-turn memory", {"messages":[{"role":"user","content":"My name is Zeta-9. Remember it."},{"role":"assistant","content":"Understood."},{"role":"user","content":"What is my name?"}]})
t("bad role", {"messages":[{"role":"wizard","content":"hi"}]})
t("extra fields", {"messages":[{"role":"user","content":"count 1 to 3"}],"max_tokens":5,"temperature":0.1,"stream":False,"model":"gpt-4o"})
t("missing messages", {"foo":"bar"})
t("long 8k chars", {"messages":[{"role":"user","content":"Summarize in 5 words: "+("the quick brown fox jumps over the lazy dog. "*200)}]})
