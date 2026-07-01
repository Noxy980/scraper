import os
import time
import json
import asyncio
import requests
from urllib.parse import quote
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from playwright.async_api import async_playwright
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
                for key in ["results","data","items","movies","series","contents"]:
                    if key in data and isinstance(data[key], list):
                        results = data[key]
                        break
                if not results:
                    for v in data.values():
                        if isinstance(v, list) and v:
                            results = v
                            break

            tv_al    = ["tv","serie","series","show","tvseries","tvshow"]
            movie_al = ["movie","film"]
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
#  PLAYWRIGHT — extraction M3U8
# ══════════════════════════════════════════════════════════════════

async def get_m3u8_url(tmdb_id: str, media_type: str = "movie",
                       season: int = None, episode: int = None) -> str | None:

    meta    = get_tmdb_meta(tmdb_id, media_type)
    title   = meta["title"]
    poster  = meta["poster"]
    naka_id = get_naka_id(title, media_type, tmdb_id)

    base = "https://nakastream.tv/player"
    if media_type == "tv":
        naka_url = (f"{base}?title={quote(title)}&id={naka_id}"
                    f"&poster={quote(poster)}&type=tv"
                    f"&season={season}&episode={episode}")
        print(f"📂 Série — {title} | {naka_id} | S{season}E{episode}")
    else:
        naka_url = (f"{base}?title={quote(title)}&id={naka_id}"
                    f"&poster={quote(poster)}&type=movie")
        print(f"📂 Film — {title} | {naka_id}")

    print(f"   🔗 {naka_url}")

    m3u8_found = None

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--disable-software-rasterizer",
                "--autoplay-policy=no-user-gesture-required",
                "--mute-audio",
                "--no-zygote",
                "--disable-setuid-sandbox",
                "--disable-background-networking",
                "--disable-default-apps",
                "--disable-sync",
            ]
        )

        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
        )

        # Bloque les ressources inutiles
        await context.route("**/*.{png,jpg,jpeg,gif,webp,woff,woff2,ttf}", lambda r: r.abort())
        await context.route("**/*doubleclick*", lambda r: r.abort())
        await context.route("**/*googlesyndication*", lambda r: r.abort())
        await context.route("**/*google-analytics*", lambda r: r.abort())
        await context.route("**/*googletagmanager*", lambda r: r.abort())
        await context.route("**/*facebook*", lambda r: r.abort())

        page = await context.new_page()

        # Intercepte les requêtes réseau → cherche m3u8
        def on_request(request):
            nonlocal m3u8_found
            url = request.url
            if ".m3u8" in url and ".ts" not in url and m3u8_found is None:
                print(f"   🎯 M3U8 intercepté : {url}")
                m3u8_found = url

        page.on("request", on_request)

        print("🚀 Chargement page...")
        await page.goto(naka_url, wait_until="domcontentloaded", timeout=30000)

        clicked_lecture = False
        clicked_pub1    = False
        clicked_pub2    = False
        clicked_lancer  = False
        deadline        = time.time() + 60

        while time.time() < deadline:
            if m3u8_found:
                break

            # Ferme les onglets pub
            for pg in context.pages:
                if pg != page:
                    await pg.close()
                    print("   🗑️  Tab pub fermé")

            if not clicked_lecture:
                try:
                    el = page.locator("text=Lecture").first
                    if await el.is_visible(timeout=100):
                        await el.click()
                        clicked_lecture = True
                        print("   ✅ Clic 'Lecture'")
                except Exception:
                    pass

            if not clicked_pub1:
                try:
                    el = page.locator("text=Regarder la pub").first
                    if await el.is_visible(timeout=100):
                        await el.click()
                        clicked_pub1 = True
                        print("   ✅ Clic 'Pub 1'")
                except Exception:
                    pass
            elif clicked_pub1 and not clicked_pub2:
                try:
                    el = page.locator("text=Regarder la pub").first
                    if await el.is_visible(timeout=100):
                        await el.click()
                        clicked_pub2 = True
                        print("   ✅ Clic 'Pub 2'")
                except Exception:
                    pass

            if not clicked_lancer:
                try:
                    el = page.locator("text=Lancer la lecture").first
                    if await el.is_visible(timeout=100):
                        await el.click()
                        clicked_lancer = True
                        print("   ✅ Clic 'Lancer la lecture'")
                except Exception:
                    pass

            for txt in ["Plus tard", "Fermer", "fermer", "plus tard"]:
                try:
                    el = page.locator(f"text={txt}").first
                    if await el.is_visible(timeout=100):
                        await el.click()
                except Exception:
                    pass

            await asyncio.sleep(0.05)

        await browser.close()

    if m3u8_found:
        print(f"✅ M3U8 : {m3u8_found}")
        return m3u8_found

    print("❌ M3U8 introuvable")
    return None

# ══════════════════════════════════════════════════════════════════
#  ENDPOINTS
# ══════════════════════════════════════════════════════════════════

@app.get("/m3u8/movie/{tmdb_id}")
async def get_movie_stream(tmdb_id: str):
    print(f"\n{'='*55}\n🎬 Film — {tmdb_id}\n{'='*55}")
    url = await get_m3u8_url(tmdb_id, "movie")
    if not url:
        raise HTTPException(status_code=404, detail="M3U8 introuvable")
    return JSONResponse({"url": url}, headers={"Cache-Control": "no-store"})

@app.get("/m3u8/tv/{tmdb_id}/{season}/{episode}")
async def get_tv_stream(tmdb_id: str, season: int, episode: int):
    print(f"\n{'='*55}\n📺 Série — {tmdb_id} S{season}E{episode}\n{'='*55}")
    url = await get_m3u8_url(tmdb_id, "tv", season, episode)
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
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)
