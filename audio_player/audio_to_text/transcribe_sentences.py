import sys, os, re, json, urllib.request, mlx_whisper
# LLM endpoint for splitting long lines (any OpenAI-compatible chat API). Configure via env; empty = skip.
LLM_BASE_URL=os.environ.get("LLM_BASE_URL","")
LLM_API_KEY=os.environ.get("LLM_API_KEY","")
LLM_MODEL=os.environ.get("LLM_MODEL","gpt-4o-mini")
mp3, out = sys.argv[1], sys.argv[2]
GAP=0.35; MAXW_LLM=12; MERGE_MAXW=2; MERGE_GAP=0.40
def fmt(t):
    t=max(0.0,t); h=int(t//3600); m=int(t%3600//60); s=t-int(t//60)*60; cc=round((s-int(s))*100)
    return f"[{h:02d}:{m:02d}:{int(s)%60:02d}"+(f".{cc:02d}]" if cc else "]")
def norm(s): return re.sub(r"[^a-z0-9']","",s.lower())
def wc(s): return len(re.findall(r"[A-Za-z0-9']+", s))
ABBR={"mr","mrs","ms","dr","st","jr","sr","prof","mt","vs","etc","lt","capt","col",
      "sgt","gen","rev","hon","ave","no","inc","ltd","co","mme","mlle","gov","sen",
      "pres","supt","det","sra","fr","rep","esq"}
def breaks_word(w):
    if re.search(r"[,!?;:]$", w): return True
    if w.endswith("."):
        if w.count(".")>1: return False
        core=re.sub(r"[^A-Za-z]","",w)
        if not core: return False
        if core.lower() in ABBR or len(core)==1: return False
        return True
    return False
def llm_split(text):
    if not LLM_BASE_URL:
        raise RuntimeError("LLM_BASE_URL not set; skip LLM split")
    prompt=("Split this transcribed speech into natural short sentences, ONE per line. "
            "Treat a comma as a sentence break too, so lines stay short. "
            "Keep ALL original words EXACTLY in the same order; do NOT add, remove, reorder, "
            "or change any word or spelling. You may only add a trailing comma/period. Output ONLY the lines.\n\n"+text)
    body=json.dumps({"model":LLM_MODEL,"messages":[{"role":"user","content":prompt}],
                     "temperature":0,"max_tokens":800}).encode()
    hdr={"Content-Type":"application/json"}
    if LLM_API_KEY: hdr["Authorization"]="Bearer "+LLM_API_KEY
    req=urllib.request.Request(LLM_BASE_URL,data=body,headers=hdr)
    return [l.strip() for l in json.load(urllib.request.urlopen(req,timeout=40))
            ["choices"][0]["message"]["content"].splitlines() if l.strip()]
def align(seg, sublines):                  # seg:[(start,end,word)] -> [(start,end,text)]
    res=[]; p=0; n=len(seg)
    for sl in sublines:
        toks=[norm(t) for t in sl.split() if norm(t)]
        if not toks: continue
        if p<n and norm(seg[p][2])!=toks[0]:
            for q in range(max(0,p-2),min(n,p+4)):
                if norm(seg[q][2])==toks[0]: p=q; break
        st=seg[min(p,n-1)][0]; p2=min(p+len(toks),n)
        en=seg[min(p2-1,n-1)][1]
        res.append((st,en,sl.strip())); p=p2
    return res

r=mlx_whisper.transcribe(mp3,path_or_hf_repo="mlx-community/whisper-large-v3-turbo",
                         language="en",task="transcribe",
                         condition_on_previous_text=False,word_timestamps=True)
W=[]
for s in r["segments"]:
    for w in s.get("words",[]):
        t=w.get("word","").strip()
        if t: W.append((w["start"],w["end"],t))
segs=[]; buf=[]
for i,(st,en,w) in enumerate(W):
    buf.append((st,en,w))
    gap=(W[i+1][0]-en) if i+1<len(W) else 9
    if breaks_word(w) or gap>GAP:
        segs.append(buf); buf=[]
if buf: segs.append(buf)
lines=[]; llm=0   # [start,end,text]
for seg in segs:
    if len(seg)>MAXW_LLM:
        try: lines+= [list(x) for x in align(seg, llm_split(" ".join(w for _,_,w in seg)))]; llm+=1
        except Exception: lines.append([seg[0][0],seg[-1][1]," ".join(w for _,_,w in seg)])
    else:
        lines.append([seg[0][0],seg[-1][1]," ".join(w for _,_,w in seg)])
# 自适应阈值: 句内正常词距(中位数)推算; 片段间隔~正常词距=连读->合并
import statistics
_g=[W[i+1][0]-W[i][1] for i in range(len(W)-1) if 0<=W[i+1][0]-W[i][1]<0.5]
MED=statistics.median(_g) if _g else 0.10
THR=min(0.35, max(0.18, MED*2.5))
def merge_gap(L):
    changed=True
    while changed:
        changed=False
        for i in range(len(L)):
            if wc(L[i][2])>MERGE_MAXW: continue
            gp=L[i][0]-L[i-1][1] if i>0 else 9
            gn=L[i+1][0]-L[i][1] if i+1<len(L) else 9
            pm=gp<=THR; nm=gn<=THR
            if not pm and not nm: continue
            if pm and nm:                       # 两侧都连读 -> 并入更短的邻句
                side="prev" if wc(L[i-1][2])<=wc(L[i+1][2]) else "next"
            else:
                side="prev" if pm else "next"
            if side=="prev" and i>0:
                L[i-1]=[L[i-1][0],L[i][1],L[i-1][2].rstrip()+" "+L[i][2].lstrip()]; del L[i]
            else:
                L[i+1]=[L[i][0],L[i+1][1],L[i][2].rstrip()+" "+L[i+1][2].lstrip()]; del L[i]
            changed=True; break
    return L
lines=merge_gap(lines)
print(f"  词距中位数={MED:.3f}s 合并阈值={THR:.3f}s")
fin=[(t,(x.strip()[:1].upper()+x.strip()[1:]) if x.strip() else x) for t,_,x in lines]
open(out,"w",encoding="utf-8").write("\n".join(f"{fmt(t)} {x}" for t,x in fin)+"\n")
print(f"{len(W)} words -> {len(segs)} 段, {llm} LLM, 合并后 {len(fin)} 行")
