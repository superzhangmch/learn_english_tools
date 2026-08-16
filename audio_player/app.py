from flask import Flask, render_template, request, jsonify, send_file, abort, Response
import os, re, shutil, json, urllib.request, urllib.error
from datetime import datetime
from pathlib import Path
app=Flask(__name__)

# ---- LLM interpret (OpenAI-compatible streaming) ----
# Models come from config.py (gitignored; copy config_example.py). Env vars provide a
# single-model fallback for back-compat. Keys stay server-side; the UI only sees names.
LLM_BASE_URL=os.environ.get("LLM_BASE_URL","").rstrip("/")
LLM_API_KEY =os.environ.get("LLM_API_KEY","")
LLM_MODEL   =os.environ.get("LLM_MODEL","")
LLM_THINKING=os.environ.get("LLM_THINKING","")       # e.g. "disabled" for GLM-5.2 (skip slow chain-of-thought)
try:
    import config as _cfg
except Exception:
    _cfg=None
def _env_models():
    if LLM_BASE_URL and LLM_MODEL:
        m={"name":LLM_MODEL,"base_url":LLM_BASE_URL,"api_key":LLM_API_KEY,"model":LLM_MODEL}
        if LLM_THINKING: m["extra"]={"thinking":{"type":LLM_THINKING}}
        return [m]
    return []
MODELS=(getattr(_cfg,"MODELS",None) if _cfg else None) or _env_models()
MODELS_BY_NAME={m["name"]:m for m in MODELS}
SYS_PROMPT=("你是英语学习助手。下面的英文来自 ASR 自动转写，个别词可能有误。用户是中文母语学习者。"
  "针对【选中】内容用简体中文解释，务必简洁：\n"
  "- 简单常见的词/短语（如 excited、because 这类高中生就懂的）：只用一句话给出它在这句中的意思即可，"
  "不展开、不讲近义词/易混词区别、不给拓展。\n"
  "- 含有地道用法、俚语、习语、文化梗、专有名词或特殊言外之意时：简要解释意思和在此句中的含义。\n"
  "- 如果背后有值得一提的文化、历史、社会等背景知识，请务必介绍（这部分可以稍微多讲一点，但整体仍保持简洁）。\n"
  "深度随难度而定，但整体都要短。不要讲发音/连读；需要读音时只给 IPA 音标。"
  "若疑似 ASR 转写错误，简短指出更可能的原词。")
ROOT=os.path.expanduser(os.environ.get("LYRICS_ROOT","~/lyrics_data"))
LINE_RE=re.compile(r'^\[(\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,2}))?\]\s?(.*)$')
SKIP=re.compile(r'\.(v1|before|sensevoice|whisperonly|crosscheck)$')
def has_lyrics(d):
    try:
        for f in os.listdir(d):
            if f.endswith(".txt") and not SKIP.search(f[:-4]): return True
    except OSError: pass
    return False
def read_cfg(d):
    try:
        with open(os.path.join(d,"_config.json"),encoding="utf-8") as f: return json.load(f) or {}
    except Exception: return {}
def suites():
    out=[]
    if os.path.isdir(ROOT):
        for name in sorted(os.listdir(ROOT)):
            d=os.path.join(ROOT,name)
            if os.path.isdir(d) and has_lyrics(d):     # isdir 跟随软链
                c=read_cfg(d)
                out.append({"id":name,"label":c.get("label",name),"type":c.get("type","audio"),"_dir":os.path.realpath(d)})
    return out
def smap(): return {s["id"]:s for s in suites()}
def list_tracks(d):
    media=Path(d); out=[]
    if not media.is_dir(): return out
    brk_re=None
    cfgp=media/"_config.json"
    if cfgp.exists():
        try:
            pat=(json.load(open(cfgp,encoding="utf-8")) or {}).get("break_after")
            if pat: brk_re=re.compile(pat)
        except Exception: pass
    for txt in sorted(media.glob("*.txt")):
        if SKIP.search(txt.stem): continue
        name=txt.stem
        out.append({"name":name,"lyrics":txt.read_text(encoding="utf-8",errors="replace"),
                    "brk": bool(brk_re.search(name)) if brk_re else False})
    return out
@app.route('/')
def index():
    return render_template('index.html',suites=[{"id":s["id"],"label":s["label"],"type":s["type"]} for s in suites()])
@app.route('/api/suite/<path:sid>')
def suite(sid):
    s=smap().get(sid); return jsonify(list_tracks(s["_dir"]) if s else [])
def validate(t):
    prev=None
    for n,raw in enumerate(t.split('\n'),1):
        line=raw.rstrip()
        if not line.strip(): continue
        m=LINE_RE.match(line)
        if not m: return False,f'第{n}行格式错误（应为 [HH:MM:SS] 文本）：{line[:40]}'
        frac=float('0.'+m.group(4)) if m.group(4) else 0.0
        tt=int(m.group(1))*3600+int(m.group(2))*60+int(m.group(3))+frac
        if prev is not None and tt<prev-1e-9: return False,f'第{n}行时间逆序'
        prev=tt
    return True,None
@app.route('/save_lyrics',methods=['POST'])
def save_lyrics():
    d=request.get_json(force=True,silent=True) or {}
    s=smap().get(d.get('suite')); name=(d.get('name') or '').strip(); lyrics=d.get('lyrics')
    if not s: return jsonify(ok=False,error='未知合集'),400
    if not re.fullmatch(r'[A-Za-z0-9_\-]+',name): return jsonify(ok=False,error=f'非法曲目名：{name!r}'),400
    if not isinstance(lyrics,str): return jsonify(ok=False,error='缺少 lyrics'),400
    path=Path(s["_dir"])/f'{name}.txt'
    if not path.is_file(): return jsonify(ok=False,error=f'曲目不存在：{name}.txt'),404
    ok,err=validate(lyrics)
    if not ok: return jsonify(ok=False,error=err),400
    ts=datetime.now().strftime('%Y%m%d_%H%M%S')
    shutil.copy2(path,path.with_name(f'{name}.txt.{ts}.bak'))
    if not lyrics.endswith('\n'): lyrics+='\n'
    path.write_text(lyrics,encoding='utf-8')
    return jsonify(ok=True,backup=f'{name}.txt.{ts}.bak')

@app.route('/audio/<sid>/<base>')
def audio(sid, base):
    s=smap().get(sid)
    if not s or not re.fullmatch(r'[A-Za-z0-9_\-]+', base or ''): abort(404)
    p=Path(s["_dir"])/(base+".mp3")
    if not p.is_file(): abort(404)
    return send_file(str(p), mimetype='audio/mpeg', conditional=True)

@app.route('/media/<sid>/<base>')
def media(sid, base):
    s=smap().get(sid)
    if not s or not re.fullmatch(r'[A-Za-z0-9_\-]+', base or ''): abort(404)
    exts=('.mp4','.mp3') if s.get("type")=="video" else ('.mp3','.mp4')
    for ext in exts:
        p=Path(s["_dir"])/(base+ext)
        if p.is_file():
            return send_file(str(p), mimetype=('video/mp4' if ext=='.mp4' else 'audio/mpeg'), conditional=True)
    abort(404)

@app.route('/api/models')
def api_models():
    return jsonify(models=[m["name"] for m in MODELS], default=(MODELS[0]["name"] if MODELS else ""))

@app.route('/api/interpret', methods=['POST'])
def interpret():
    d=request.get_json(force=True,silent=True) or {}
    text=(d.get('text') or '').strip()
    context=(d.get('context') or '').strip()
    title=(d.get('title') or '').strip()
    history=d.get('history') or []
    if not text: return jsonify(error='no text'),400
    sel=MODELS_BY_NAME.get(d.get('model')) or (MODELS[0] if MODELS else None)   # name -> endpoint
    if not sel:
        return jsonify(error='LLM 未配置（缺少 config.py 或 LLM_* 环境变量）'),500
    base=sel["base_url"].rstrip("/")
    user=((f"【出处】{title}\n" if title else "")+
          (f"【上下文（同一段歌词）】\n{context}\n\n" if context else "")+
          f"【选中】{text}\n\n请讲解【选中】的部分。")
    messages=[{"role":"system","content":SYS_PROMPT}]
    for h in history:
        if isinstance(h,dict) and h.get('role') in ('user','assistant') and h.get('content'):
            messages.append({"role":h['role'],"content":str(h['content'])})
    messages.append({"role":"user","content":user})
    # merge per-model extras FIRST, then let the real fields win — so a stray
    # model/messages/stream in extra can't clobber them (extra = thinking, temperature, ...)
    payload={**(sel.get("extra") or {}), "model":sel["model"], "messages":messages, "stream":True}
    hdr={"Content-Type":"application/json"}
    if sel.get("api_key"): hdr["Authorization"]="Bearer "+sel["api_key"]
    req=urllib.request.Request(base+"/chat/completions",
                               data=json.dumps(payload).encode('utf-8'),headers=hdr,method="POST")
    def gen():
        try:
            with urllib.request.urlopen(req,timeout=120) as up:
                for raw in up:
                    line=raw.decode('utf-8','replace').strip()
                    if not line.startswith('data:'): continue
                    body=line[5:].strip()
                    if body=='[DONE]': break
                    try: o=json.loads(body)
                    except Exception: continue
                    try: delta=o['choices'][0]['delta'].get('content')
                    except Exception: delta=None
                    if delta: yield "data: "+json.dumps({"delta":delta})+"\n\n"
        except urllib.error.HTTPError as e:
            msg=e.read().decode('utf-8','replace')[:300]
            yield "data: "+json.dumps({"error":f"LLM {e.code}: {msg}"})+"\n\n"
        except Exception as e:
            yield "data: "+json.dumps({"error":str(e)})+"\n\n"
    return Response(gen(),mimetype='text/event-stream',
                    headers={"Cache-Control":"no-cache","X-Accel-Buffering":"no"})

if __name__=='__main__':
    app.run(host=os.environ.get('HOST','0.0.0.0'),port=int(os.environ.get('PORT','7080')),threaded=True)
