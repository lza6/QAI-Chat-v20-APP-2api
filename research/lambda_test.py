from probe import call
import json
LAM="https://ljbgc4xyfeca3dm2q7td3bsrou0lrtqm.lambda-url.us-east-1.on.aws"
for url,model in [(f"{LAM}/", "t1"), (f"{LAM}/chatCompletion","t1"), ("https://aibr.elway-mobile.com/chatCompletion","t1")]:
    try:
        r,t0,_=call([{"role":"user","content":"reply exactly: PONG"}], model=model, url=url, timeout=45)
        print(f"--- {url} model={model} -> {r.status_code} {r.headers.get('content-type')}")
        n=0
        for line in r.iter_lines(decode_unicode=True):
            if line: print("   ", line[:160]); n+=1
            if n>6: break
    except Exception as e:
        print(f"--- {url} EXC {e}")
