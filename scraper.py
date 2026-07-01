import time
import json
import asyncio
import requests
import threading
from urllib.parse import quote
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from selenium import webdriver
from selenium.webdriver.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
import uvicorn

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET"], allow_headers=["*"])

TMDB_KEY  = "bd7f03b084c8b2ad9de7229f1dcf6d36"
TMDB_BASE = "https://api.themoviedb.org/3"
NAKA_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://nakastream.tv/",
    "Origin":  "https://nakastream.tv",
    "Accept":  "application/json",
}

# ══════════════════════════════════════════════════════════════════
#  TMDB
# ══════════════════════════════════════════════════════════════════

def get_tmdb_meta(tmdb_id: str, media_type: str) -> dict:
    try:
        kind = "movie" if media_type == "movie" else "tv"
        res  = requests.get(
            f"{TMDB_BASE}/{kind}/{tmdb_id}",
            params={"api_key": TMDB_KEY, "language": "en-US"},
            timeout=8,
        )
        if res.ok:
            data   = res.json()
            title  = data.get("title") or data.get("name") or ""
            poster = data.get("poster_path") or ""
            print(f"   📋 {title} | {poster}")
            return {"title": title, "poster": poster}
    except Exception as e:
        print(f"   ⚠️ TMDB: {e}")
    return {"title": "", "poster": ""}

# ══════════════════════════════════════════════════════════════════
#  NAKASTREAM — ID interne
# ══════════════════════════════════════════════════════════════════

def get_naka_id(title: str, media_type: str, tmdb_id: str) -> str:
    if not title:
        return tmdb_id
    try:
        res = requests.get(
            "https://nakastream.tv/api/v1/browse/search",
            params={"q": title},
            headers=NAKA_HEADERS,
            timeout=10,
        )
        if res.ok:
            data    = res.json()
            results = []
            if isinstance(data, list):
                results = data
            elif isinstance(data, dict):
                for key in ["results", "data", "items", "movies", "series", "contents"]:
                    if key in data and isinstance(data[key], list):
                        results = data[key]
                        break
                if not results:
                    for v in data.values():
                        if isinstance(v, list) and v:
                            results = v
                            break

            tv_al    = ["tv", "serie", "series", "show", "tvseries", "tvshow"]
            movie_al = ["movie", "film"]
            aliases  = tv_al if media_type == "tv" else movie_al

            for item in results:
                itype = str(item.get("type") or item.get("media_type") or "").lower()
                iid   = str(item.get("id")   or item.get("_id") or "")
                if any(a in itype for a in aliases) and iid:
                    print(f"   🎯 Naka ID : {iid}")
                    return iid
            for item in results:
                iid = str(item.get("id") or item.get("_id") or "")
                if iid:
                    return iid
    except Exception as e:
        print(f"   ❌ Naka search: {e}")
    return tmdb_id

# ══════════════════════════════════════════════════════════════════
#  SELENIUM — extraction M3U8
# ══════════════════════════════════════════════════════════════════

def get_m3u8_url(tmdb_id: str, media_type: str = "movie",
                 season: int = None, episode: int = None) -> str | None:

    opts = Options()
    opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--disable-software-rasterizer")
    opts.add_argument("--window-size=1920,1080")
    opts.add_argument("--autoplay-policy=no-user-gesture-required")
    opts.add_argument("--disable-extensions")
    opts.add_argument("--blink-settings=imagesEnabled=false")
    opts.add_argument("--no-first-run")
    opts.add_argument("--no-default-browser-check")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_argument("--disable-setuid-sandbox")
    opts.add_argument("--ignore-certificate-errors")
    opts.add_argument("--disable-background-networking")
    opts.add_argument("--disable-default-apps")
    opts.add_argument("--disable-sync")
    opts.add_argument("--metrics-recording-only")
    opts.add_argument("--mute-audio")
    opts.add_argument("--no-zygote")
    opts.add_argument("--disable-renderer-backgrounding")
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    opts.add_experimental_option("useAutomationExtension", False)
    opts.add_experimental_option("prefs", {
        "profile.managed_default_content_settings.images": 2,
        "profile.managed_default_content_settings.fonts":  2,
    })
    opts.set_capability("goog:loggingPrefs", {"performance": "ALL"})

    driver = webdriver.Chrome(
        service=Service(ChromeDriverManager().install()),
        options=opts,
    )
    driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument",
        {"source": "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})"})
    driver.execute_cdp_cmd("Network.enable", {})
    driver.execute_cdp_cmd("Network.setBlockedURLs", {"urls": [
        "*doubleclick*", "*googlesyndication*", "*adservice*",
        "*googletagmanager*", "*google-analytics*", "*facebook*",
        "*hotjar*", "*clarity*",
        "*.gif", "*.png", "*.jpg", "*.jpeg", "*.webp",
        "*.woff", "*.woff2", "*.ttf",
    ]})

    main_handle = driver.current_window_handle

    # ── Helpers ───────────────────────────────────────────────────

    def scan_m3u8() -> str | None:
        for entry in driver.get_log("performance"):
            try:
                log = json.loads(entry["message"])["message"]
                if log.get("method") != "Network.requestWillBeSent":
                    continue
                url = log.get("params", {}).get("request", {}).get("url", "")
                if ".m3u8" not in url or ".ts" in url:
                    continue
                if any(q in url for q in ["720p", "1080p", "480p", "360p", "playlist"]):
                    return url
                if "master" not in url and "manifest" not in url:
                    return url
            except Exception:
                pass
        return None

    def kill_tabs() -> None:
        for h in list(driver.window_handles):
            if h != main_handle:
                try:
                    driver.switch_to.window(h)
                    driver.close()
                    print("   🗑️  Tab pub fermé")
                except Exception:
                    pass
        driver.switch_to.window(main_handle)

    def click_text(texts: list) -> bool:
        for text in texts:
            try:
                for el in driver.find_elements(By.XPATH, f"//*[contains(text(),'{text}')]"):
                    if el.is_displayed():
                        driver.execute_script("arguments[0].click();", el)
                        print(f"   ✅ Clic '{text}'")
                        return True
            except Exception:
                pass
        return False

    def run(timeout: float = 60) -> str | None:
        deadline        = time.time() + timeout
        clicked_lecture = False
        clicked_pub1    = False
        clicked_pub2    = False
        clicked_lancer  = False

        while time.time() < deadline:
            m3u8 = scan_m3u8()
            if m3u8:
                return m3u8

            kill_tabs()

            if not clicked_lecture:
                if click_text(["Lecture"]):
                    clicked_lecture = True

            if not clicked_pub1:
                if click_text(["Regarder la pub"]):
                    clicked_pub1 = True
            elif clicked_pub1 and not clicked_pub2:
                if click_text(["Regarder la pub"]):
                    clicked_pub2 = True

            if not clicked_lancer:
                if click_text(["Lancer la lecture", "Lancer"]):
                    clicked_lancer = True

            click_text(["Plus tard", "Fermer", "fermer", "plus tard"])

            time.sleep(0.05)

        return None

    # ── Pipeline ──────────────────────────────────────────────────
    try:
        meta    = get_tmdb_meta(tmdb_id, media_type)
        title   = meta["title"]
        poster  = meta["poster"]
        naka_id = get_naka_id(title, media_type, tmdb_id)

        base = "https://nakastream.tv/player"
        if media_type == "tv":
            naka_url = (
                f"{base}?title={quote(title)}&id={naka_id}"
                f"&poster={quote(poster)}&type=tv"
                f"&season={season}&episode={episode}"
            )
            print(f"📂 Série — {title} | {naka_id} | S{season}E{episode}")
        else:
            naka_url = (
                f"{base}?title={quote(title)}&id={naka_id}"
                f"&poster={quote(poster)}&type=movie"
            )
            print(f"📂 Film — {title} | {naka_id}")

        print(f"   🔗 {naka_url}")
        driver.get(naka_url)

        print("🚀 Boucle principale...")
        m3u8 = run(timeout=60)

        if m3u8:
            print(f"✅ M3U8 : {m3u8}")
            threading.Thread(target=driver.quit, daemon=True).start()
            return m3u8

        print("❌ M3U8 introuvable")
        return None

    except Exception:
        driver.quit()
        raise

# ══════════════════════════════════════════════════════════════════
#  ENDPOINTS
# ══════════════════════════════════════════════════════════════════

async def _run(tmdb_id: str, media_type: str,
               season: int = None, episode: int = None) -> str | None:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None,
        lambda: get_m3u8_url(tmdb_id, media_type, season, episode)
    )

@app.get("/m3u8/movie/{tmdb_id}")
async def get_movie_stream(tmdb_id: str):
    print(f"\n{'='*55}\n🎬 Film — {tmdb_id}\n{'='*55}")
    url = await _run(tmdb_id, "movie")
    if not url:
        raise HTTPException(status_code=404, detail="M3U8 introuvable")
    return JSONResponse({"url": url}, headers={"Cache-Control": "no-store"})

@app.get("/m3u8/tv/{tmdb_id}/{season}/{episode}")
async def get_tv_stream(tmdb_id: str, season: int, episode: int):
    print(f"\n{'='*55}\n📺 Série — {tmdb_id} S{season}E{episode}\n{'='*55}")
    url = await _run(tmdb_id, "tv", season, episode)
    if not url:
        raise HTTPException(status_code=404, detail="M3U8 introuvable")
    return JSONResponse({"url": url}, headers={"Cache-Control": "no-store"})

@app.get("/m3u8/{tmdb_id}")
async def get_stream_legacy(tmdb_id: str):
    return await get_movie_stream(tmdb_id)

# ══════════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("=" * 55)
    print("   🎬  Nexora FR — Streaming API  ⚡")
    print("=" * 55)
    print("   GET /m3u8/movie/{tmdb_id}")
    print("   GET /m3u8/tv/{tmdb_id}/{season}/{episode}")
    print("=" * 55)
    uvicorn.run(app, host="0.0.0.0", port=8000)
