from probe import call
import concurrent.futures as cf, json
Q = [{"role":"system","content":"You are a helpful assistant."},
     {"role":"user","content":"Which AI model and version are you exactly? Who trained you? Answer in 2 sentences, be specific."}]
def probe(model):
    try:
        r,_,_ = call(Q, model=model, timeout=60)
        if r.status_code!=200: return model, f"HTTP {r.status_code}", r.text[:100]
        out=[]
        for line in r.iter_lines(decode_unicode=True):
            if line and line.startswith("data:"):
                d=line[5:].strip()
                if not d: continue
                j=json.loads(d)
                if j.get("content"): out.append(j["content"])
                if j.get("finishReason"): break
        return model,"OK","".join(out)
    except Exception as e: return model,"ERR",str(e)[:100]
for m in ["default","gpt-4o","gpt-4.1-mini","claude-sonnet-4","gemini-2.5-flash","deepseek-chat","gpt-5"]:
    mm,s,t = probe(m)
    print(f"--- {mm} [{s}]\n{t}\n")
