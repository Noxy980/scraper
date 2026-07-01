import os
import time
import asyncio
import requests
from urllib.parse import quote
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from playwright.async_api import async_playwright
import uvicorn

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)

TMDB_KEY  = "bd7f03b084c8b2ad9de7229f1dcf6d36"
TMDB_BASE = "https://api.themoviedb.org/3"

NAKA_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://nakastream.tv/",
    "Origin":  "https://nakastream.tv",
    "Accept":  "application/json",
}

# ══════════════════════════════════════════════════════════════════
#  HEALTH CHECK
# ══════════════════════════════════════════════════════════════════

@app.get("/health")
@app.get("/")
async def health_check():
    return JSONResponse(
        status_code=200,
        content={"status": "ok", "message": "Nexora FR API is running"}
    )

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
            print(f"   📋 TMDB → {title} | poster: {poster}")
            return {"title": title, "poster": poster}
    except Exception as e:
        print(f"   ⚠️  TMDB erreur : {e}")
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

            tv_aliases    = ["tv", "serie", "series", "show", "tvseries", "tvshow"]
            movie_aliases = ["movie", "film"]
            aliases       = tv_aliases if media_type == "tv" else movie_aliases

            for item in results:
                itype = str(item.get("type") or item.get("media_type") or "").lower()
                iid   = str(item.get("id")   or item.get("_id") or "")
                if any(a in itype for a in aliases) and iid:
                    print(f"   🎯 Naka ID trouvé : {iid}")
                    return iid

            for item in results:
                iid = str(item.get("id") or item.get("_id") or "")
                if iid:
                    print(f"   🎯 Naka ID fallback : {iid}")
                    return iid

    except Exception as e:
        print(f"   ❌ Naka search erreur : {e}")

    print(f"   ⚠️  Naka ID non trouvé, fallback tmdb_id : {tmdb_id}")
    return tmdb_id

# ══════════════════════════════════════════════════════════════════
#  PLAYWRIGHT — extraction M3U8
# ══════════════════════════════════════════════════════════════════

async def get_m3u8_url(
    tmdb_id: str,
    media_type: str = "movie",
    season: int = None,
    episode: int = None,
) -> str | None:

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
        print(f"📂 Série  — {title} | id:{naka_id} | S{season}E{episode}")
    else:
        naka_url = (
            f"{base}?title={quote(title)}&id={naka_id}"
            f"&poster={quote(poster)}&type=movie"
        )
        print(f"📂 Film   — {title} | id:{naka_id}")

    print(f"   🔗 URL : {naka_url}")

    m3u8_found = None

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--disable-software-rasterizer",
                "--no-zygote",
                "--single-process",
                "--disable-background-networking",
                "--disable-default-apps",
                "--disable-sync",
                "--disable-extensions",
                "--disable-features=TranslateUI",
                "--disable-ipc-flooding-protection",
                "--disable-blink-features=AutomationControlled",
                "--autoplay-policy=no-user-gesture-required",
                "--mute-audio",
            ],
        )

        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"
            ),
            ignore_https_errors=True,
        )

        # Bloque les ressources inutiles
        await context.route(
            "**/*.{png,jpg,jpeg,gif,webp,woff,woff2,ttf,svg,ico}",
            lambda r: r.abort(),
        )
        await context.route("**/*doubleclick*",       lambda r: r.abort())
        await context.route("**/*googlesyndication*", lambda r: r.abort())
        await context.route("**/*google-analytics*",  lambda r: r.abort())
        await context.route("**/*googletagmanager*",  lambda r: r.abort())
        await context.route("**/*facebook*",          lambda r: r.abort())

        page = await context.new_page()

        def on_request(request):
            nonlocal m3u8_found
            url = request.url
            if ".m3u8" in url and ".ts" not in url and m3u8_found is None:
                print(f"   🎯 M3U8 intercepté : {url}")
                m3u8_found = url

        page.on("request", on_request)

        print("🚀 Chargement de la page Nakastream...")
        try:
            await page.goto(naka_url, wait_until="domcontentloaded", timeout=30000)
        except Exception as e:
            print(f"   ⚠️  goto erreur (non bloquant) : {e}")

        clicked = {
            "lecture": False,
            "pub1":    False,
            "pub2":    False,
            "lancer":  False,
        }

        deadline = time.time() + 60

        while time.time() < deadline:
            if m3u8_found:
                break

            for pg in context.pages:
                if pg != page:
                    try:
                        await pg.close()
                        print("   🗑️  Onglet pub fermé")
                    except Exception:
                        pass

            if not clicked["lecture"]:
                try:
                    el = page.locator("text=Lecture").first
                    if await el.is_visible(timeout=100):
                        await el.click()
                        clicked["lecture"] = True
                        print("   ✅ Clic 'Lecture'")
                except Exception:
                    pass

            if not clicked["pub1"]:
                try:
                    el = page.locator("text=Regarder la pub").first
                    if await el.is_visible(timeout=100):
                        await el.click()
                        clicked["pub1"] = True
                        print("   ✅ Clic 'Pub 1'")
                except Exception:
                    pass
            elif clicked["pub1"] and not clicked["pub2"]:
                try:
                    el = page.locator("text=Regarder la pub").first
                    if await el.is_visible(timeout=100):
                        await el.click()
                        clicked["pub2"] = True
                        print("   ✅ Clic 'Pub 2'")
                except Exception:
                    pass

            if not clicked["lancer"]:
                try:
                    el = page.locator("text=Lancer la lecture").first
                    if await el.is_visible(timeout=100):
                        await el.click()
                        clicked["lancer"] = True
                        print("   ✅ Clic 'Lancer la lecture'")
                except Exception:
                    pass

            for txt in ["Plus tard", "Fermer", "fermer", "plus tard", "SKIP", "Skip"]:
                try:
                    el = page.locator(f"text={txt}").first
                    if await el.is_visible(timeout=100):
                        await el.click()
                        print(f"   ✅ Clic '{txt}'")
                except Exception:
                    pass

            await asyncio.sleep(0.05)

        await browser.close()

    if m3u8_found:
        print(f"✅ M3U8 trouvé : {m3u8_found}")
        return m3u8_found

    print("❌ M3U8 introuvable après 60s")
    return None

# ══════════════════════════════════════════════════════════════════
#  ENDPOINTS
# ══════════════════════════════════════════════════════════════════

@app.get("/m3u8/movie/{tmdb_id}")
async def get_movie_stream(tmdb_id: str):
    print(f"\n{'='*55}\n🎬  Film — tmdb_id={tmdb_id}\n{'='*55}")
    url = await get_m3u8_url(tmdb_id, "movie")
    if not url:
        raise HTTPException(status_code=404, detail="M3U8 introuvable pour ce film")
    return JSONResponse({"url": url}, headers={"Cache-Control": "no-store"})


@app.get("/m3u8/tv/{tmdb_id}/{season}/{episode}")
async def get_tv_stream(tmdb_id: str, season: int, episode: int):
    print(f"\n{'='*55}\n📺  Série — tmdb_id={tmdb_id} S{season}E{episode}\n{'='*55}")
    url = await get_m3u8_url(tmdb_id, "tv", season, episode)
    if not url:
        raise HTTPException(status_code=404, detail="M3U8 introuvable pour cet épisode")
    return JSONResponse({"url": url}, headers={"Cache-Control": "no-store"})


@app.get("/m3u8/{tmdb_id}")
async def get_stream_legacy(tmdb_id: str):
    return await get_movie_stream(tmdb_id)


# ══════════════════════════════════════════════════════════════════
#  MAIN — uniquement pour dev local
# ══════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("=" * 55)
    print("   🎬  Nexora FR — Streaming API  ⚡")
    print("=" * 55)
    print("   GET /health")
    print("   GET /m3u8/movie/{tmdb_id}")
    print("   GET /m3u8/tv/{tmdb_id}/{season}/{episode}")
    print("=" * 55)
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("scraper:app", host="0.0.0.0", port=port)
