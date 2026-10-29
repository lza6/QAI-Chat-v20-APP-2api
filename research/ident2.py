import json, requests, time
from probe import sign
URL="https://aibr.elway-mobile.com/chatCompletion"
def ask(q):
    body=json.dumps({"messages":[{"role":"user","content":q}]},ensure_ascii=False,separators=(",",":"))
    r=requests.post(URL,data=body.encode(),headers={"aibsg":sign(body),"aibmd":"t1","Content-Type":"text/event-stream"},timeout=60,stream=True)
    out=[]
    for line in r.iter_lines(decode_unicode=True):
        if not line or not line.startswith("data:"): continue
        d=line[5:].strip()
        if not d: continue
        j=json.loads(d)
        if j.get("content"): out.append(j["content"])
        if j.get("finishReason"): break
    return "".join(out)
QS=[
 "What is your training knowledge cutoff date? Answer with just the date.",
 "Name the exact model architecture and parameter count you run on.",
 "Which is bigger: 9.11 or 9.9? Answer in one line then explain briefly.",
 "Write a 3-line Python function that reverses a linked list. Only code.",
 "你是哪个公司开发的模型？请用中文回答，只回答公司名和模型名。",
 "Output exactly this JSON and nothing else: {\"ok\":true,\"n\":3}",
]
for q in QS:
    t0=time.time()
    print(f"Q: {q}\nA: {ask(q)}\n[{round(time.time()-t0,2)}s]\n")
