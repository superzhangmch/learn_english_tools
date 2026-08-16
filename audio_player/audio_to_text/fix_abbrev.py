import sys, re, glob
ABBR={"mr","mrs","ms","dr","st","jr","sr","prof","mt","vs","etc","lt","capt","col",
      "sgt","gen","rev","hon","ave","no","inc","ltd","co","mme","mlle","gov","sen",
      "pres","supt","det","sra","fr","rep","esq"}
def abbrev_end(text):
    text=text.rstrip()
    if not text.endswith("."): return False
    last=text.split()[-1] if text.split() else ""
    if last.count(".")>1: return True            # 缩略号 U.S. / a.m.
    core=re.sub(r"[^A-Za-z]","",last)
    if not core: return False
    return core.lower() in ABBR or len(core)==1   # 缩写 或 单字母首字母
def fix(fp):
    raw=[l.rstrip("\n") for l in open(fp,encoding="utf-8") if l.strip()]
    out=[]
    for ln in raw:
        m=re.match(r'(\[[\d:.]+\])\s*(.*)', ln)
        if m and out:
            pm=re.match(r'(\[[\d:.]+\])\s*(.*)', out[-1])
            if pm and abbrev_end(pm[2]):
                out[-1]=pm[1]+" "+pm[2].rstrip()+" "+m[2].lstrip(); continue
        out.append(ln)
    open(fp,"w",encoding="utf-8").write("\n".join(out)+"\n")
    return len(raw)-len(out)
files=sys.argv[1:]; tot=0
for fp in files:
    n=fix(fp); tot+=n
    if n: print(f"  {fp.split('/')[-1]}: 合并 {n} 处")
print(f"=== {len(files)} 文件, 共修复 {tot} 处缩写错切 ===")
