import requests, concurrent.futures as cf
BASE="https://aibr.elway-mobile.com"
PATHS=["chatCompletion","chatCompletionStream","completion","chat","v1/chat","v1/chat/completions","api/chat",
"image","images","imageGeneration","images/generations","art","evo/art","tts","transcribe","audio","models",
"user","auth","token","tokens","billing","subscribe","feedback","config","remoteConfig","health","status",
"error","actuator","actuator/health","actuator/env","swagger-ui.html","v3/api-docs","openapi.json","api-docs",
"v1/models","login","register","app/config","v1/config","version","ping","echo","test","admin"]
def probe(p):
    for m in ("GET","POST"):
        try:
            r=requests.request(m, f"{BASE}/{p}", timeout=12, allow_redirects=False,
                               json={} if m=="POST" else None)
            if r.status_code not in (404,):
                return f"{m:5} /{p:26} {r.status_code} {r.headers.get('content-type','')[:30]} {r.text[:90]!r}"
        except Exception as e:
            return f"{m:5} /{p:26} EXC {type(e).__name__}"
    return None
with cf.ThreadPoolExecutor(max_workers=12) as ex:
    for r in ex.map(probe, PATHS):
        if r: print(r)
print("scan done")
