#!/usr/bin/env python3.11
"""Screensaver web page: clock page + cached proxies for BBC/CNN news, weather, radar.

Caching model: stale-while-revalidate. Requests ALWAYS return the cached copy
immediately; if the cache is older than TTL, a background thread refreshes it.
Only the very first request after startup could block (mitigated by a startup
prefetch). No requests => no upstream traffic at all.
"""
import json
import re
import time
import threading
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from itertools import zip_longest
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

import os
PORT = int(os.environ.get("PORT", "8000"))
HOST = os.environ.get("HOST", "0.0.0.0")
TTL = 3600   # cache expire time: 1 hour (news / weather)
TTLS = {"news": 3600, "weather": 3600,
        "radar": 600}  # radar updates upstream every ~6 min; keep it fresh

FEEDS = [
    ("BBC", "https://feeds.bbci.co.uk/news/rss.xml"),           # top stories
    ("WORLD", "https://feeds.bbci.co.uk/news/world/rss.xml"),
    ("TECH", "https://feeds.bbci.co.uk/news/technology/rss.xml"),
    # CNN's RSS died in Apr 2023; NYT is paywalled — NPR is free to read
    ("NPR", "https://feeds.npr.org/1001/rss.xml"),
]
MAX_PER_FEED = 12
MAX_AGE_H = 24  # drop items older than this (also drops items with no pubDate)

# Weather location — read from config.py (gitignored); falls back to the example.
try:
    from config import CITY, LAT, LON
except ImportError:
    from config_example import CITY, LAT, LON

# NMC national radar mosaic (updated every ~6 min upstream)
RADAR_PAGE = "http://www.nmc.cn/publish/radar/chinaall.html"

# dismissed news items ("看过了"), persisted across restarts.
#   key -> {"ts": epoch, "title": …, "source": …, "date": …}
# 以前存的是 sorted(set), 只有 key —— 于是"刚才误删的那条"根本查不回来: 既不知道什么时候
# 删的, 也不知道删掉的是什么。带上 ts 和条目自身的字段, 才能做"最近删除 / 恢复"。
DISMISSED_FILE = Path(__file__).parent / "dismissed.json"
# 离开 feed 窗口后还保留多久, 好让"最近删除"在 feed 轮换后依然恢复得回来
TRASH_TTL = 24 * 3600


def _load_dismissed() -> dict:
    try:
        raw = json.loads(DISMISSED_FILE.read_text())
    except Exception:
        return {}
    if isinstance(raw, list):     # 旧格式: 只有 key 的数组, ts=0 表示"删除时间未知"
        return {k: {"ts": 0} for k in raw if isinstance(k, str)}
    if isinstance(raw, dict):
        return {k: (v if isinstance(v, dict) else {"ts": 0}) for k, v in raw.items()}
    return {}


_dismissed = _load_dismissed()


def _save_dismissed() -> None:
    """调用方必须已持有 _lock。"""
    DISMISSED_FILE.write_text(json.dumps(_dismissed, ensure_ascii=False, sort_keys=True))

_lock = threading.Lock()
_caches = {
    "news": {"ts": 0.0, "data": None, "refreshing": False},
    "weather": {"ts": 0.0, "data": None, "refreshing": False},
    "radar": {"ts": 0.0, "data": None, "refreshing": False},
}


def _urlopen(url, timeout=15, referer=None):
    headers = {"User-Agent": "Mozilla/5.0 (screensaver)"}
    if referer:
        headers["Referer"] = referer
    return urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=timeout)


# ---------- fetchers (raise on failure, return fresh data) ----------

def fetch_news() -> dict:
    feeds = []
    for i, (source, url) in enumerate(FEEDS, 1):
        print(f"  news {i}/{len(FEEDS)}: {source} ...", flush=True)
        current = []
        try:
            root = None
            for attempt in range(3):  # flaky overseas HTTPS: retry a couple times
                try:
                    with _urlopen(url, timeout=10) as resp:
                        root = ET.fromstring(resp.read())
                    break
                except Exception as e:
                    if attempt == 2:
                        raise
                    print(f"    {source}: retry {attempt + 1} ({e})", flush=True)
                    time.sleep(1)
            cutoff = datetime.now(timezone.utc) - timedelta(hours=MAX_AGE_H)
            for item in root.iter("item"):
                title = (item.findtext("title") or "").strip()
                link = (item.findtext("link") or "").strip()
                try:  # pubDate is RFC822; drop undated/stale items (>24h)
                    pub = parsedate_to_datetime(item.findtext("pubDate"))
                except Exception:
                    continue
                if pub < cutoff:
                    continue
                date = pub.astimezone().strftime("%Y-%m-%d")
                if title:
                    current.append({"source": source, "title": title,
                                    "link": link, "date": date})
                if len(current) >= MAX_PER_FEED:
                    break
            print(f"    {source}: {len(current)} titles", flush=True)
        except Exception as e:
            print(f"    {source}: FAILED ({e})", flush=True)
        feeds.append(current)
    # interleave sources: BBC, NPR, BBC, NPR, ...
    items = [it for group in zip_longest(*feeds) for it in group if it]
    # de-duplicate: keep ONE copy per article (same link OR same title = duplicate).
    # The same story appears across BBC channels (top/world/tech) with an identical
    # link, and occasionally across sources with the same title. Prefer the BBC copy.
    _is_bbc = lambda it: "bbc." in (it["link"] or "")
    order = sorted(range(len(items)), key=lambda i: 0 if _is_bbc(items[i]) else 1)  # stable: BBC first
    seen_links, seen_titles, keep = set(), set(), set()
    for i in order:
        link, title = items[i]["link"], items[i]["title"]
        if (link and link in seen_links) or (title and title in seen_titles):
            continue
        if link:
            seen_links.add(link)
        if title:
            seen_titles.add(title)
        keep.add(i)
    items = [it for i, it in enumerate(items) if i in keep]  # keep interleaved display order
    if not items:
        raise RuntimeError("all feeds failed")
    with _lock:
        # 原来是「离开 feed 窗口就立刻忘掉」, 那样刚删的条目一轮换就再也恢复不了。
        # 现在多留 TRASH_TTL, 期间仍可从"最近删除"里捞回来; 之后照旧清掉, 文件不会无限长。
        live = {it["link"] or it["title"] for it in items}
        now = time.time()
        for k, v in list(_dismissed.items()):
            if k not in live and now - (v.get("ts") or 0) > TRASH_TTL:
                del _dismissed[k]
        _save_dismissed()
    return {"items": items}


def assess(code, feels, uv, rain, o3, pm25) -> dict:
    """Rate whether it's suitable to go outside: level 0 适合 / 1 注意 / 2 不宜."""
    bad, warn = [], []
    if code in {65, 66, 67, 75, 77, 82, 86, 95, 96, 99}:
        bad.append("severe weather")
    elif code in {51, 53, 55, 56, 57, 61, 63, 71, 73, 80, 81, 85}:
        warn.append("rain, take umbrella")
    if rain and not bad:
        warn.append("rain soon" if rain.get("in_min") else "raining")
    if feels >= 37:
        bad.append(f"extreme heat {feels}°")
    elif feels >= 33:
        warn.append("hot")
    if feels <= -15:
        bad.append(f"extreme cold {feels}°")
    elif feels <= -5:
        warn.append("cold")
    if uv >= 8:
        warn.append("very strong UV")
    elif uv >= 6:
        warn.append("strong UV")
    # CN standard breakpoints: PM2.5 µg/m³, O3 µg/m³ (1h)
    if pm25 and pm25 > 115:
        bad.append(f"PM2.5 {pm25} unhealthy")
    elif pm25 and pm25 > 75:
        warn.append("PM2.5 elevated")
    if o3 and o3 > 265:
        bad.append("heavy ozone")
    elif o3 and o3 > 200:
        warn.append("ozone elevated")
    reasons = ", ".join((bad + warn)[:2])
    if bad:
        return {"level": 2, "text": f"✗ Stay in · {reasons}"}
    if warn:
        return {"level": 1, "text": f"△ OK out · {reasons}"}
    return {"level": 0, "text": "✓ Good to go out"}


def fetch_weather() -> dict:
    print("  weather: fetching ...", flush=True)
    url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={LAT}&longitude={LON}"
        "&current=temperature_2m,weather_code,relative_humidity_2m,"
        "apparent_temperature,precipitation"
        "&hourly=temperature_2m,weather_code,precipitation_probability,uv_index"
        "&minutely_15=precipitation"
        "&daily=temperature_2m_max,temperature_2m_min,uv_index_max"
        "&timezone=auto&forecast_days=2"
    )
    with _urlopen(url, timeout=10) as resp:
        raw = json.loads(resp.read())

    now = (datetime.now(timezone.utc)
           + timedelta(seconds=raw["utc_offset_seconds"])).replace(tzinfo=None)

    # next 8 hours starting from the current hour
    h = raw["hourly"]
    idx = next((i for i, t in enumerate(h["time"])
                if datetime.fromisoformat(t) > now - timedelta(hours=1)), 0)
    hours = [
        {
            "h": datetime.fromisoformat(h["time"][i]).hour,
            "t": round(h["temperature_2m"][i]),
            "code": h["weather_code"][i],
            "pp": h["precipitation_probability"][i],
        }
        for i in range(idx, min(idx + 8, len(h["time"])))
    ]

    # rain hint from 15-min precipitation series (next 2h)
    rain = None
    if raw["current"]["precipitation"] > 0.05:
        rain = {"now": True}
    else:
        m = raw.get("minutely_15", {})
        for t, precip in zip(m.get("time", []), m.get("precipitation", [])):
            dt = datetime.fromisoformat(t)
            if dt < now or dt > now + timedelta(hours=2):
                continue
            if precip > 0.05:
                rain = {"in_min": max(1, int((dt - now).total_seconds() // 60))}
                break

    uv = round(h["uv_index"][idx])
    uv_max = round(raw["daily"]["uv_index_max"][0])

    pm25 = o3 = None
    try:
        aq_url = (
            "https://air-quality-api.open-meteo.com/v1/air-quality"
            f"?latitude={LAT}&longitude={LON}&current=pm2_5,ozone&timezone=auto"
        )
        with _urlopen(aq_url, timeout=10) as resp:
            aq = json.loads(resp.read())["current"]
        pm25, o3 = round(aq["pm2_5"]), round(aq["ozone"])
    except Exception as e:
        print(f"    air quality: FAILED ({e})", flush=True)

    code = raw["current"]["weather_code"]
    feels = round(raw["current"]["apparent_temperature"])
    data = {
        "city": CITY,
        "temp": round(raw["current"]["temperature_2m"]),
        "code": code,
        "humidity": raw["current"]["relative_humidity_2m"],
        "feels": feels,
        "tmax": round(raw["daily"]["temperature_2m_max"][0]),
        "tmin": round(raw["daily"]["temperature_2m_min"][0]),
        "hours": hours,
        "rain": rain,
        "uv": uv,
        "uv_max": uv_max,
        "pm25": pm25,
        "o3": o3,
        "advice": assess(code, feels, uv, rain, o3, pm25),
    }
    print(f"    weather: {data['temp']}C code={code}", flush=True)
    return data


ANIM_TAKE = 20            # animation: sample the last 2h of frames...
ANIM_STRIDE = 2           # ...every 2nd frame -> 10 frames, 12 min apart
ANIM_TTL = 120            # url-list cache only (cheap page fetch), keep it fresh
_radar_frame_pool = {}    # url(no query) -> png bytes (latest static frame)
_radar_time = ""          # observation time (HH:MM Beijing) of the static frame
_anim = {"ts": 0.0, "urls": [], "times": []}
_anim_lock = threading.Lock()


def _radar_urls() -> list:
    with _urlopen(RADAR_PAGE) as resp:
        page = resp.read().decode("utf-8", "ignore")
    urls = sorted(set(re.findall(r'data-img="([^"]+)"', page)))
    if not urls:
        raise RuntimeError("no data-img found on NMC page")
    return urls


def _download_frame(url) -> bytes:
    key = url.split("?")[0]
    img = _radar_frame_pool.get(key)
    if img is None:
        with _urlopen(url, timeout=20, referer="http://www.nmc.cn/") as resp:
            img = resp.read()
        _radar_frame_pool[key] = img
    return img


def fetch_radar() -> bytes:
    """Latest frame only — for the always-on static view (prefetched hourly)."""
    global _radar_time
    print("  radar: fetching latest ...", flush=True)
    url = _radar_urls()[-1]
    img = _download_frame(url)
    key = url.split("?")[0]
    _radar_frame_pool.clear()
    _radar_frame_pool[key] = img  # keep only the latest frame in the pool
    _radar_time = _frame_time(url)
    print(f"    radar: {len(img)//1024}KB @ {_radar_time}", flush=True)
    return img


def _frame_time(url) -> str:
    """Frame HH:MM (Beijing) parsed from the filename's UTC timestamp."""
    m = re.search(r"_(\d{12})", url.split("/")[-1])
    if not m:
        return ""
    dt = datetime.strptime(m.group(1), "%Y%m%d%H%M") + timedelta(hours=8)
    return dt.strftime("%H:%M")


def get_radar_anim() -> dict:
    """Animation frame URLS + times, oldest first. The browser loads the images
    directly from image.nmc.cn (no hotlink protection, CORS wide open) —
    x260 only relays this tiny URL list."""
    with _anim_lock:
        if time.time() - _anim["ts"] < ANIM_TTL and _anim["urls"]:
            return _anim
        # newest first, take ANIM_TAKE, keep every ANIM_STRIDE-th, restore order
        urls = _radar_urls()[::-1][:ANIM_TAKE:ANIM_STRIDE][::-1]
        _anim["urls"] = urls
        _anim["times"] = [_frame_time(u) for u in urls]
        _anim["ts"] = time.time()
        print(f"  radar anim: {len(urls)} frame urls", flush=True)
        return _anim


FETCHERS = {"news": fetch_news, "weather": fetch_weather, "radar": fetch_radar}


# ---------- stale-while-revalidate cache ----------

def _refresh(name):
    try:
        fresh = FETCHERS[name]()
        with _lock:
            _caches[name]["data"] = fresh
            _caches[name]["ts"] = time.time()
    except Exception as e:
        print(f"  {name}: refresh FAILED ({e})", flush=True)
    finally:
        with _lock:
            _caches[name]["refreshing"] = False


def get(name):
    """Return cached data immediately; kick off a background refresh if expired."""
    with _lock:
        c = _caches[name]
        if c["data"] is not None:
            if time.time() - c["ts"] >= TTLS.get(name, TTL) and not c["refreshing"]:
                c["refreshing"] = True
                threading.Thread(target=_refresh, args=(name,), daemon=True).start()
            return c["data"]
        # cold start: nothing cached yet — one thread fetches, others wait briefly
        sync = not c["refreshing"]
        c["refreshing"] = sync
    if sync:
        _refresh(name)
    else:
        for _ in range(150):
            time.sleep(0.2)
            with _lock:
                if _caches[name]["data"] is not None:
                    break
    with _lock:
        return _caches[name]["data"]


def _updated(name) -> str:
    """HH:MM of the last successful upstream fetch, '' if never."""
    with _lock:
        ts = _caches[name]["ts"]
    return time.strftime("%H:%M", time.localtime(ts)) if ts else ""


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(Path(__file__).parent), **kwargs)

    def send_json(self, obj):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if self.path == "/dismiss":
            try:
                body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                key = json.loads(body)["key"]
                with _lock:
                    _dismissed.add(key)
                    DISMISSED_FILE.write_text(json.dumps(sorted(_dismissed)))
                self.send_json({"ok": True})
            except Exception as e:
                self.send_error(400, str(e))
        else:
            self.send_error(404)

    def do_GET(self):
        if self.path == "/news":
            data = get("news") or {"items": []}
            with _lock:
                items = [it for it in data["items"]
                         if (it["link"] or it["title"]) not in _dismissed]
            self.send_json({"items": items, "updated": _updated("news")})
        elif self.path == "/weather":
            data = get("weather") or {"error": "unavailable"}
            self.send_json({**data, "updated": _updated("weather")})
        elif self.path == "/status":
            self.send_json({**{name: _updated(name) for name in _caches},
                            "radar_time": _radar_time})
        elif self.path == "/radar-meta":  # frame url list; browser loads them directly
            try:
                anim = get_radar_anim()
                self.send_json({"count": len(anim["urls"]),
                                "urls": anim["urls"], "times": anim["times"]})
            except Exception as e:
                self.send_json({"count": 0, "error": str(e)})
        elif self.path.startswith("/radar"):
            img = get("radar")  # static latest frame from the prefetched cache
            if img:
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Content-Length", str(len(img)))
                self.end_headers()
                self.wfile.write(img)
            else:
                self.send_error(503, "radar unavailable")
        else:
            if self.path == "/":
                # Redirect by UA to a DISTINCT url so browser/proxy caches never
                # cross-serve (a cached "/" used to leak the mobile page onto desktop).
                ua = self.headers.get("User-Agent", "")
                target = "/mobile.html" if "Mobile" in ua else "/index.html"
                self.send_response(302)
                self.send_header("Location", target)
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                return
            elif self.path == "/m":  # mobile news page
                self.path = "/mobile.html"
            elif self.path == "/d":  # force desktop layout
                self.path = "/index.html"
            super().do_GET()

    def log_message(self, fmt, *args):
        pass  # keep stdout clean; fetch progress is printed by fetchers


if __name__ == "__main__":
    print(f"Screensaver at http://{HOST}:{PORT}  (Ctrl-C to stop, cache TTL {TTL}s)")
    for name in FETCHERS:  # startup prefetch so even the first request is instant
        threading.Thread(target=get, args=(name,), daemon=True).start()
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
