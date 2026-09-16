import glob,re,os,sys,subprocess
SRC=sys.argv[1] if len(sys.argv)>1 else "peppa_lyrics"     # dir of <name>.mp3 + <name>.txt
OUT=sys.argv[2] if len(sys.argv)>2 else "peppa_trimmed"; os.makedirs(OUT,exist_ok=True)
INTRO={"im peppa pig","this is my little brother george","this is mummy pig","and this is daddy pig","peppa pig"}
# Whisper does not always render the ~22s intro as those exact separate lines: over the
# theme music it may merge them into one line, or hallucinate the whole chunk into a
# single unrelated phrase ("The End" is by far the most common). Exact set membership
# missed both, so `a` stayed 0, the head went untrimmed, and the junk line survived at
# the top of the transcript — and because Whisper decodes in 30s windows, the episode
# title and first narration line (spoken ~22-30s) were lost with it.
HALLUC={"the end","thank you","thanks for watching","subtitles by the amaraorg community"}
INTRO_MAX=25.0     # the intro never runs past this, so only look for junk before it
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
    def is_intro(t,txt):
        n=norm(txt)
        if not n: return True
        if n in INTRO: return True
        if t>=INTRO_MAX: return False          # past the intro nothing here is junk
        if n in HALLUC: return True
        r=n                                    # merged intro: peel off every known phrase
        for ph in sorted(INTRO|HALLUC,key=len,reverse=True): r=r.replace(ph,"")
        return not r.strip()
    a=0
    while a<len(L) and is_intro(*L[a]): a+=1
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
    warn=" WARN 裁后首行仍偏晚, 片头可能没识别干净" if kept[0][0]-astart>5 else ""
    print(f"[{i}/{n}] {base}  头-{a}行@{head_cut:.0f}s 尾-{len(L)-b}行  留{len(kept)}行{warn}",flush=True)
print(f"TRIM_DONE  裁头 {trimmed_h}, 裁尾 {trimmed_t}, 跳过 {skip}, 共 {n}")
