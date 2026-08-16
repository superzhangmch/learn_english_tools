import sys, re
def collapse(text):
    t=text.split()
    out=[]                                  # 连续相同词折叠
    for w in t:
        if out and out[-1].lower()==w.lower(): continue
        out.append(w)
    t=out; res=[]; i=0                       # 连续相同二元组折叠
    while i<len(t):
        if i+3<len(t) and t[i].lower()==t[i+2].lower() and t[i+1].lower()==t[i+3].lower():
            res.extend([t[i],t[i+1]]); j=i+2
            while j+1<len(t) and t[j].lower()==t[i].lower() and t[j+1].lower()==t[i+1].lower(): j+=2
            i=j
        else: res.append(t[i]); i+=1
    return " ".join(res)
files=sys.argv[1:]; nfix=ndrop=0
for fp in files:
    out=[]
    for ln in open(fp,encoding="utf-8"):
        ln=ln.rstrip("\n")
        if not ln.strip(): continue
        m=re.match(r'(\[[\d:.]+\])\s*(.*)',ln)
        if not m: out.append(ln); continue
        ts,body=m[1],m[2]
        if not re.search(r"[A-Za-z0-9]",body): ndrop+=1; continue   # 纯符号丢
        nb=collapse(body)
        if nb!=body: nfix+=1
        out.append(f"{ts} {nb}")
    open(fp,"w",encoding="utf-8").write("\n".join(out)+"\n")
print(f"折叠复读 {nfix} 行, 丢纯符号 {ndrop} 行")
