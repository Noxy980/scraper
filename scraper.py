import os
import re
import requests
import httpx
from urllib.parse import quote
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
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

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/120.0.0.0 Safari/537.36",
    "Referer":         "https://nakastream.tv/",
    "Origin":          "https://nakastream.tv",
    "Accept":          "application/json, text/html, */*",
    "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
}

# ══════════════════════════════════════════════════════════════════
#  HEALTH CHECK
# ══════════════════════════════════════════════════════════════════

@app.get("/")
async def root():
    return JSONResponse(status_code=200, content={"status": "ok"})

@app.get("/health")
async def health_check():
    return JSONResponse(status_code=200, content={"status": "ok"})

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
            print(f"   📋 TMDB → {title}")
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
            headers=HEADERS,
            timeout=10,
        )
        print(f"   🔍 Naka search status : {res.status_code}")
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
                    print(f"   🎯 Naka ID fallback : {iid}")
                    return iid

    except Exception as e:
        print(f"   ❌ Naka search : {e}")

    return tmdb_id

# ══════════════════════════════════════════════════════════════════
#  HELPERS
# ══════════════════════════════════════════════════════════════════

def find_m3u8_in_dict(data, depth=0):
    if depth > 10:
        return None
    if isinstance(data, str):
        if ".m3u8" in data and data.startswith("http"):
            return data
    elif isinstance(data, dict):
        for key, val in data.items():
            if isinstance(val, str) and ".m3u8" in val:
                return val
            result = find_m3u8_in_dict(val, depth + 1)
            if result:
                return result
    elif isinstance(data, list):
        for item in data:
            result = find_m3u8_in_dict(item, depth + 1)
            if result:
                return result
    return None


def extract_m3u8_from_html(html: str):
    patterns = [
        r'https?://[^\s"\'<>]+\.m3u8[^\s"\'<>]*',
        r'"file"\s*:\s*"(https?://[^"]+\.m3u8[^"]*)"',
        r"'file'\s*:\s*'(https?://[^']+\.m3u8[^']*)'",
        r'"src"\s*:\s*"(https?://[^"]+\.m3u8[^"]*)"',
        r'source\s*:\s*["\']?(https?://[^\s"\']+\.m3u8[^\s"\']*)',
        r'hls\.loadSource\(["\']([^"\']+\.m3u8[^"\']*)["\']',
        r'Hls\.loadSource\(["\']([^"\']+\.m3u8[^"\']*)["\']',
    ]
    for pattern in patterns:
        matches = re.findall(pattern, html)
        for match in matches:
            if ".m3u8" in match and match.startswith("http"):
                return match
    return None

# ══════════════════════════════════════════════════════════════════
#  ÉTAPE 1 — API directe
# ══════════════════════════════════════════════════════════════════

async def get_m3u8_from_naka_api(
    naka_id, media_type, title, poster, season=None, episode=None
):
    endpoints = (
        [
            f"https://nakastream.tv/api/v1/stream/tv/{naka_id}/{season}/{episode}",
            f"https://nakastream.tv/api/v1/video/tv/{naka_id}/{season}/{episode}",
            f"https://nakastream.tv/api/v1/episode/{naka_id}/{season}/{episode}/stream",
            f"https://nakastream.tv/api/v1/contents/{naka_id}/stream?season={season}&episode={episode}",
            f"https://nakastream.tv/api/v1/watch/{naka_id}?type=tv&season={season}&episode={episode}",
        ]
        if media_type == "tv"
        else [
            f"https://nakastream.tv/api/v1/stream/movie/{naka_id}",
            f"https://nakastream.tv/api/v1/video/movie/{naka_id}",
            f"https://nakastream.tv/api/v1/movie/{naka_id}/stream",
            f"https://nakastream.tv/api/v1/contents/{naka_id}/stream?type=movie",
            f"https://nakastream.tv/api/v1/watch/{naka_id}?type=movie",
        ]
    )

    async with httpx.AsyncClient(
        headers=HEADERS, follow_redirects=True, timeout=15
    ) as client:
        for ep in endpoints:
            try:
                print(f"   🔎 {ep}")
                r = await client.get(ep)
                print(f"      → {r.status_code}")
                if r.status_code == 200:
                    try:
                        m3u8 = find_m3u8_in_dict(r.json())
                        if m3u8:
                            return m3u8
                    except Exception:
                        pass
                    m3u8 = extract_m3u8_from_html(r.text)
                    if m3u8:
                        return m3u8
            except Exception as e:
                print(f"      → erreur : {e}")
    return None

# ══════════════════════════════════════════════════════════════════
#  ÉTAPE 2 — Page player
# ══════════════════════════════════════════════════════════════════

async def get_m3u8_from_player_page(
    naka_id, media_type, title, poster, season=None, episode=None
):
    base = "https://nakastream.tv/player"
    url  = (
        f"{base}?title={quote(title)}&id={naka_id}"
        f"&poster={quote(poster)}&type=tv&season={season}&episode={episode}"
        if media_type == "tv"
        else f"{base}?title={quote(title)}&id={naka_id}"
             f"&poster={quote(poster)}&type=movie"
    )
    print(f"   🔗 {url}")

    async with httpx.AsyncClient(
        headers=HEADERS, follow_redirects=True, timeout=20
    ) as client:
        try:
            r = await client.get(url)
            print(f"   📄 status : {r.status_code}")
            if r.status_code != 200:
                return None

            html = r.text
            m3u8 = extract_m3u8_from_html(html)
            if m3u8:
                return m3u8

            for iframe_url in re.findall(r'<iframe[^>]+src=["\']([^"\']+)["\']', html):
                if not iframe_url.startswith("http"):
                    iframe_url = "https://nakastream.tv" + iframe_url
                try:
                    ir = await client.get(iframe_url)
                    if ir.status_code == 200:
                        m3u8 = extract_m3u8_from_html(ir.text)
                        if m3u8:
                            return m3u8
                except Exception:
                    pass

            for src in re.findall(r'<script[^>]+src=["\']([^"\']+)["\']', html):
                if any(k in src for k in ["player", "video", "stream", "hls"]):
                    if not src.startswith("http"):
                        src = "https://nakastream.tv" + src
                    try:
                        jr = await client.get(src)
                        if jr.status_code == 200:
                            m3u8 = extract_m3u8_from_html(jr.text)
                            if m3u8:
                                return m3u8
                    except Exception:
                        pass

        except Exception as e:
            print(f"   ❌ {e}")
    return None

# ══════════════════════════════════════════════════════════════════
#  ORCHESTRATEUR
# ══════════════════════════════════════════════════════════════════

async def get_m3u8_url(tmdb_id, media_type="movie", season=None, episode=None):
    meta    = get_tmdb_meta(tmdb_id, media_type)
    title   = meta["title"]
    poster  = meta["poster"]
    naka_id = get_naka_id(title, media_type, tmdb_id)

    print(f"📂 {'Série' if media_type == 'tv' else 'Film'} — {title} | {naka_id}")

    m3u8 = await get_m3u8_from_naka_api(naka_id, media_type, title, poster, season, episode)
    if m3u8:
        print(f"✅ M3U8 : {m3u8}")
        return m3u8

    m3u8 = await get_m3u8_from_player_page(naka_id, media_type, title, poster, season, episode)
    if m3u8:
        print(f"✅ M3U8 : {m3u8}")
        return m3u8

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
#  MAIN — Railway lit $PORT automatiquement
# ══════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    print(f"🚀 Démarrage sur port {port}")
    uvicorn.run("scraper:app", host="0.0.0.0", port=port)
