#!/usr/bin/env python3.11
# -*- coding: utf-8 -*-
"""
mp4 -> audio -> timestamped lyrics (ASR). Batch-ready.

  python3.11 mp4_to_lyrics.py <input.mp4 | dir-of-mp4s> [--out DIR] [--sensevoice]

For each video:
  <out>/<name>.mp3            128k audio (listenable + ASR source)
  <out>/<name>.txt            primary lyrics, [HH:MM:SS] text  (Whisper large-v3-turbo)
  with --sensevoice also:
  <out>/<name>.sensevoice.txt Alibaba SenseVoice transcription (2nd engine)
  <out>/<name>.crosscheck.txt side-by-side Whisper vs SenseVoice, ⚠️ marks disagreements

Whisper is primary (most accurate on English). SenseVoice is the optional cross-check.
Models load once and are reused across files, so this scales to hundreds of episodes.
"""
import argparse, os, re, subprocess, sys, time, glob

def sanitize(name):
    name = re.sub(r"\[[^\]]*\]", "", name)          # drop [www.xxx] tags
    name = re.sub(r"\s+", " ", name).strip()
    return re.sub(r"[^\w.\-]+", "_", name)          # filesystem/URL safe

def hms(sec):
    sec = max(0, int(sec)); return f"[{sec//3600:02d}:{sec%3600//60:02d}:{sec%60:02d}]"

def extract(video, mp3, wav):
    subprocess.run(["ffmpeg","-y","-v","error","-i",video,"-vn",
                    "-c:a","libmp3lame","-b:a","128k",mp3], check=True)
    subprocess.run(["ffmpeg","-y","-v","error","-i",mp3,"-ar","16000","-ac","1",wav], check=True)

def _clean(segs):
    """Drop Whisper hallucination loops: intra-line token loops + consecutive dup lines."""
    out = []
    for t, x in segs:
        ws = x.split()
        if ws and max((ws.count(w) for w in set(ws)), default=0) >= 6:
            continue
        if out and out[-1][1].lower() == x.lower():
            continue
        out.append((t, x))
    return out

def whisper_txt(mp3, out_txt):
    import mlx_whisper
    r = mlx_whisper.transcribe(mp3, path_or_hf_repo="mlx-community/whisper-large-v3-turbo",
                               language="en", task="transcribe",
                               condition_on_previous_text=False,      # anti-hallucination loop
                               compression_ratio_threshold=2.2,
                               no_speech_threshold=0.6)
    segs = _clean([(s["start"], s["text"].strip()) for s in r["segments"] if s["text"].strip()])
    with open(out_txt, "w", encoding="utf-8") as f:
        f.write("\n".join(f"{hms(t)} {txt}" for t, txt in segs) + "\n")
    return segs

def _toks(x): return set(re.findall(r"[a-z']+", x.lower()))
def _jac(a, b):
    A, B = _toks(a), _toks(b); return len(A & B) / len(A | B) if (A | B) else 1.0

_SV = {"vad": None, "sv": None}
EMOJI = re.compile(r'[\U0001F000-\U0001FAFF☀-➿️]')
def sensevoice_txt(mp3, out_txt):
    import librosa, soundfile as sf
    from funasr import AutoModel
    from funasr.utils.postprocess_utils import rich_transcription_postprocess
    if _SV["vad"] is None:
        _SV["vad"] = AutoModel(model="fsmn-vad", disable_update=True, log_level="ERROR")
        _SV["sv"]  = AutoModel(model="iic/SenseVoiceSmall", disable_update=True, log_level="ERROR")
    audio,_ = librosa.load(mp3, sr=16000, mono=True)
    segs_v = _SV["vad"].generate(input=mp3)[0]["value"]
    tmp = out_txt + ".seg.wav"; lines=[]
    for s,e in segs_v:
        clip = audio[int(s/1000*16000):int(e/1000*16000)]
        if len(clip) < 1600: continue
        sf.write(tmp, clip, 16000)
        rr = _SV["sv"].generate(input=tmp, language="en", use_itn=True)
        txt = EMOJI.sub("", rich_transcription_postprocess(rr[0]["text"])).strip()
        if txt: lines.append((s/1000.0, txt))
    if os.path.exists(tmp): os.remove(tmp)
    with open(out_txt, "w", encoding="utf-8") as f:
        f.write("\n".join(f"{hms(t)} {txt}" for t,txt in lines) + "\n")
    return lines

def fill_gaps(w, s, near=4.0, min_words=2):
    """Fill Whisper's empty regions with SenseVoice lines (no subtitle for Peppa).
    Only insert an SV line if no Whisper line is within `near` seconds AND the SV
    line has >= min_words words (skips 'Yeah'/'Oh' non-speech noise)."""
    ins = []
    for t, x in s:
        if len(re.findall(r"[A-Za-z']+", x)) < min_words:
            continue
        if any(abs(t - q) <= near for q, _ in w):
            continue                              # Whisper already has a line here
        if any(abs(t - q) <= 8.0 and _jac(x, wx) >= 0.5 for q, wx in w):
            continue                              # near-duplicate of a Whisper line -> skip
        ins.append((t, x + "  «sv»"))
    return sorted(w + ins, key=lambda p: p[0]), len(ins)

def crosscheck(w, s, out_txt):
    def toks(x): return set(re.findall(r"[a-z']+", x.lower()))
    def jac(a,b):
        A,B=toks(a),toks(b); return len(A&B)/len(A|B) if (A|B) else 1.0
    out=[]; agree=0
    for i,(t,txt) in enumerate(w):
        nt = w[i+1][0] if i+1<len(w) else t+999
        sv = " ".join(x for (st,x) in s if t-3 <= st < max(nt, t+1))
        sim = jac(txt, sv); ok = sim>=0.5; agree += ok
        out.append(f"{'  ' if ok else '⚠️'}{hms(t)} W: {txt}")
        out.append(f"      S: {sv}   (sim {sim:.2f})")
    open(out_txt,"w",encoding="utf-8").write("\n".join(out)+"\n")
    return agree, len(w)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("input", help="an .mp4 file or a directory containing .mp4 files")
    ap.add_argument("--out", default="lyrics_out", help="output directory")
    ap.add_argument("--sensevoice", action="store_true", help="also run Alibaba SenseVoice + crosscheck")
    a = ap.parse_args()

    vids = ([a.input] if os.path.isfile(a.input)
            else sorted(glob.glob(os.path.join(a.input, "*.mp4"))))
    if not vids:
        sys.exit(f"no mp4 found at {a.input}")
    os.makedirs(a.out, exist_ok=True)
    print(f"{len(vids)} video(s) -> {a.out}  (sensevoice={'on' if a.sensevoice else 'off'})")

    for i, v in enumerate(vids, 1):
        name = sanitize(os.path.splitext(os.path.basename(v))[0])
        mp3 = os.path.join(a.out, name+".mp3")
        wav = os.path.join(a.out, name+".16k.wav")
        txt = os.path.join(a.out, name+".txt")
        print(f"\n[{i}/{len(vids)}] {name}")
        t0=time.time()
        extract(v, mp3, wav)
        w = whisper_txt(mp3, txt)
        print(f"  whisper: {len(w)} lines, {time.time()-t0:.0f}s -> {os.path.basename(txt)}")
        if a.sensevoice:
            ts=time.time()
            s = sensevoice_txt(mp3, os.path.join(a.out, name+".sensevoice.txt"))
            ag, tot = crosscheck(w, s, os.path.join(a.out, name+".crosscheck.txt"))
            # fill Whisper's empty regions with SenseVoice, write final <name>.txt
            merged, nfill = fill_gaps(w, s)
            os.replace(txt, os.path.join(a.out, name+".whisperonly.txt"))
            with open(txt, "w", encoding="utf-8") as f:
                f.write("\n".join(f"{hms(t)} {x}" for t, x in merged) + "\n")
            print(f"  sensevoice: {len(s)} lines, {time.time()-ts:.0f}s  agree {ag}/{tot}  "
                  f"gap-filled +{nfill} lines -> {os.path.basename(txt)}")
        if os.path.exists(wav): os.remove(wav)
    print("\nDone.")

if __name__ == "__main__":
    main()
