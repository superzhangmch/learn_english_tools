import sys, re, statistics
from funasr import AutoModel
APPLY="--apply" in sys.argv
files=[a for a in sys.argv[1:] if a.endswith(".mp3")]
vad=AutoModel(model="fsmn-vad",disable_update=True,log_level="ERROR")
def parse(fp):
    o=[]
    for ln in open(fp,encoding="utf-8"):
        m=re.match(r'(\[(\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,2}))?\])\s*(.*)',ln.rstrip("\n"))
        if m: o.append((int(m[2])*3600+int(m[3])*60+int(m[4])+(int(m[5])/100 if m[5] else 0), ln.rstrip("\n"), m[6]))
    return o
tot=drop=flag=0
for mp3 in files:
    txt=mp3[:-4]+".txt"
    segs=[(s/1000.0,e/1000.0) for s,e in vad.generate(input=mp3)[0]["value"]]
    if not segs: continue
    sp0,sp1=segs[0][0],segs[-1][1]            # 首句起 / 末句止
    lines=parse(txt); tot+=len(lines)
    def inside(t): return any(s<=t<=e for s,e in segs)
    def near(t): return min(((s-t) for s,e in segs),key=abs)
    keep=[]; dropped=[]; flags=[]
    for t,raw,body in lines:
        if t < sp0-0.5 or t > sp1+0.5:        # 片头/片尾非语音区 -> 丢
            dropped.append((t,body)); continue
        keep.append(raw)
        if not inside(t) and abs(near(t))>0.4:
            flags.append((t,body,near(t)))
    name=mp3.split("/")[-1]
    med=statistics.median([near(t) for t,_,_ in lines]) if lines else 0
    print(f"\n{name}: {len(lines)}行 中位偏移{med:+.2f}s | 丢弃{len(dropped)} 标记{len(flags)}")
    for t,b in dropped: print(f"   ✂️丢 [{int(t//60):02d}:{t%60:05.2f}] {b[:40]}")
    for t,b,d in flags: print(f"   ⚠️ [{int(t//60):02d}:{t%60:05.2f}] onset{d:+.2f}s {b[:38]}")
    drop+=len(dropped); flag+=len(flags)
    if APPLY and dropped:
        open(txt,"w",encoding="utf-8").write("\n".join(keep)+"\n")
print(f"\n=== {tot}行: 丢弃{drop}, 标记{flag} {'(已写回)' if APPLY else '(仅报告)'} ===")
