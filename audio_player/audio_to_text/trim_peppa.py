import glob,re,os,sys,subprocess
SRC=sys.argv[1] if len(sys.argv)>1 else "peppa_lyrics"     # dir of <name>.mp3 + <name>.txt
OUT=sys.argv[2] if len(sys.argv)>2 else "peppa_trimmed"; os.makedirs(OUT,exist_ok=True)
INTRO={"im peppa pig","this is my little brother george","this is mummy pig","and this is daddy pig","peppa pig"}
JINGLE={"peppa pig","papa pig"}
PAD=0.15
def norm(t): return re.sub(r"[^a-z0-9 ]","",t.lower()).strip()
def parse(fp):
    o=[]
    for ln in open(fp,encoding="utf-8"):
        m=re.match(r'\[(\d\d):(\d\d):(\d\d)(?:\.(\d+))?\]\s*(.*)',ln.rstrip("\n"))
        if m and m[5].strip(): o.append((int(m[1])*3600+int(m[2])*60+int(m[3])+(int(m[4])/100 if m[4] else 0), m[5].strip()))
    return o
def fmt(t):
    t=max(0.0,t); h=int(t//3600); m=int(t%3600//60); s=t-int(t//60)*60; cc=round((s-int(s))*100)
    return f"[{h:02d}:{m:02d}:{int(s)%60:02d}"+(f".{cc:02d}]" if cc else "]")
mp3s=sorted(glob.glob(os.path.join(SRC,"*.mp3")))
n=len(mp3s); trimmed_h=trimmed_t=skip=0
print(f"处理 {n} 集 -> {OUT}")
for i,mp3 in enumerate(mp3s,1):
    base=os.path.basename(mp3)[:-4]; txt=os.path.join(SRC,base+".txt")
    if not os.path.exists(txt): continue
    L=parse(txt)
    if not L: continue
    a=0
    while a<len(L) and norm(L[a][1]) in INTRO: a+=1
    b=len(L)
    while b>0 and norm(L[b-1][1]) in JINGLE: b-=1
    if b<=a:
        skip+=1; print(f"[{i}/{n}] {base} SKIP(裁后空)",flush=True); continue
    head_cut = (L[a][0] if a>0 else 0.0)
    tail_cut = (L[b][0] if b<len(L) else None)
    astart=max(0.0, head_cut-PAD) if a>0 else 0.0
    kept=L[a:b]
    with open(os.path.join(OUT,base+".txt"),"w",encoding="utf-8") as f:
        f.write("\n".join(f"{fmt(t-astart)} {tx}" for t,tx in kept)+"\n")
    cmd=["ffmpeg","-y","-v","error","-ss",f"{astart:.3f}","-i",mp3]
    if tail_cut is not None: cmd+=["-t",f"{tail_cut-astart:.3f}"]
    cmd+=["-c:a","libmp3lame","-b:a","128k",os.path.join(OUT,base+".mp3")]
    subprocess.run(cmd,check=True)
    if a>0: trimmed_h+=1
    if tail_cut is not None: trimmed_t+=1
    print(f"[{i}/{n}] {base}  头-{a}行@{head_cut:.0f}s 尾-{len(L)-b}行  留{len(kept)}行",flush=True)
print(f"TRIM_DONE  裁头 {trimmed_h}, 裁尾 {trimmed_t}, 跳过 {skip}, 共 {n}")
