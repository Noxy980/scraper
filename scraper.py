import os
import re
import asyncio
import requests
import httpx
from urllib.parse import quote, urlencode
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
    "Referer":    "https://nakastream.tv/",
    "Origin":     "https://nakastream.tv",
    "Accept":     "application/json, text/html, */*",
    "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
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

            tv_aliases    = ["tv", "serie", "series", "show", "tvseries", "tvshow"]
            movie_aliases = ["movie", "film"]
            aliases       = tv_aliases if media_type == "tv" else movie_aliases

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
#  EXTRACTION M3U8 — Sans Playwright (requêtes HTTP directes)
# ══════════════════════════════════════════════════════════════════

def extract_m3u8_from_html(html: str) -> str | None:
    """Cherche un lien .m3u8 dans le HTML brut."""
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
            url = match if match.startswith("http") else match
            if ".m3u8" in url and ".ts" not in url:
                return url
    return None


def extract_stream_urls_from_html(html: str) -> list[str]:
    """Cherche tous les types d'URLs de stream."""
    patterns = [
        r'https?://[^\s"\'<>]+\.m3u8[^\s"\'<>]*',
        r'https?://[^\s"\'<>]+/hls/[^\s"\'<>]+',
        r'https?://[^\s"\'<>]+/stream/[^\s"\'<>]+',
        r'https?://[^\s"\'<>]+/video/[^\s"\'<>]+\.m3u8[^\s"\'<>]*',
    ]
    found = []
    for pattern in patterns:
        matches = re.findall(pattern, html)
        found.extend(matches)
    return list(set(found))


async def get_m3u8_from_naka_api(
    naka_id: str,
    media_type: str,
    title: str,
    poster: str,
    season: int = None,
    episode: int = None,
) -> str | None:
    """Tente de récupérer le M3U8 via les endpoints API de Nakastream."""

    # Endpoints API à tester dans l'ordre
    api_endpoints = []

    if media_type == "tv":
        api_endpoints = [
            f"https://nakastream.tv/api/v1/stream/tv/{naka_id}/{season}/{episode}",
            f"https://nakastream.tv/api/v1/video/tv/{naka_id}/{season}/{episode}",
            f"https://nakastream.tv/api/v1/episode/{naka_id}/{season}/{episode}/stream",
            f"https://nakastream.tv/api/v1/contents/{naka_id}/stream?season={season}&episode={episode}",
            f"https://nakastream.tv/api/v1/watch/{naka_id}?type=tv&season={season}&episode={episode}",
        ]
    else:
        api_endpoints = [
            f"https://nakastream.tv/api/v1/stream/movie/{naka_id}",
            f"https://nakastream.tv/api/v1/video/movie/{naka_id}",
            f"https://nakastream.tv/api/v1/movie/{naka_id}/stream",
            f"https://nakastream.tv/api/v1/contents/{naka_id}/stream?type=movie",
            f"https://nakastream.tv/api/v1/watch/{naka_id}?type=movie",
        ]

    async with httpx.AsyncClient(
        headers=HEADERS,
        follow_redirects=True,
        timeout=15,
    ) as client:
        for endpoint in api_endpoints:
            try:
                print(f"   🔎 Test API : {endpoint}")
                res = await client.get(endpoint)
                print(f"      → status {res.status_code}")

                if res.status_code == 200:
                    # Tente JSON
                    try:
                        data = res.json()
                        print(f"      → JSON : {str(data)[:200]}")

                        # Cherche récursivement une URL m3u8
                        m3u8 = find_m3u8_in_dict(data)
                        if m3u8:
                            print(f"   ✅ M3U8 via API JSON : {m3u8}")
                            return m3u8
                    except Exception:
                        pass

                    # Tente HTML/texte brut
                    text = res.text
                    m3u8 = extract_m3u8_from_html(text)
                    if m3u8:
                        print(f"   ✅ M3U8 via API HTML : {m3u8}")
                        return m3u8

            except Exception as e:
                print(f"      → erreur : {e}")
                continue

    return None


async def get_m3u8_from_player_page(
    naka_id: str,
    media_type: str,
    title: str,
    poster: str,
    season: int = None,
    episode: int = None,
) -> str | None:
    """Récupère la page player et cherche le M3U8 dans le HTML."""

    base = "https://nakastream.tv/player"
    if media_type == "tv":
        url = (
            f"{base}?title={quote(title)}&id={naka_id}"
            f"&poster={quote(poster)}&type=tv"
            f"&season={season}&episode={episode}"
        )
    else:
        url = (
            f"{base}?title={quote(title)}&id={naka_id}"
            f"&poster={quote(poster)}&type=movie"
        )

    print(f"   🔗 Player URL : {url}")

    async with httpx.AsyncClient(
        headers=HEADERS,
        follow_redirects=True,
        timeout=20,
    ) as client:
        try:
            res = await client.get(url)
            print(f"   📄 Player page status : {res.status_code}")

            if res.status_code == 200:
                html = res.text

                # Cherche directement le m3u8
                m3u8 = extract_m3u8_from_html(html)
                if m3u8:
                    return m3u8

                # Cherche des iframes ou sources embed
                iframe_urls = re.findall(
                    r'<iframe[^>]+src=["\']([^"\']+)["\']', html
                )
                for iframe_url in iframe_urls:
                    print(f"   🖼️  Iframe trouvé : {iframe_url}")
                    if not iframe_url.startswith("http"):
                        iframe_url = "https://nakastream.tv" + iframe_url
                    try:
                        iframe_res = await client.get(iframe_url)
                        if iframe_res.status_code == 200:
                            m3u8 = extract_m3u8_from_html(iframe_res.text)
                            if m3u8:
                                print(f"   ✅ M3U8 dans iframe : {m3u8}")
                                return m3u8
                    except Exception as e:
                        print(f"      → iframe erreur : {e}")

                # Cherche des scripts JS avec des URLs
                script_tags = re.findall(
                    r'<script[^>]*src=["\']([^"\']+)["\']', html
                )
                for script_url in script_tags:
                    if any(kw in script_url for kw in ["player", "video", "stream", "hls"]):
                        if not script_url.startswith("http"):
                            script_url = "https://nakastream.tv" + script_url
                        try:
                            print(f"   📜 Script JS : {script_url}")
                            js_res = await client.get(script_url)
                            if js_res.status_code == 200:
                                m3u8 = extract_m3u8_from_html(js_res.text)
                                if m3u8:
                                    print(f"   ✅ M3U8 dans JS : {m3u8}")
                                    return m3u8
                        except Exception as e:
                            print(f"      → script erreur : {e}")

        except Exception as e:
            print(f"   ❌ Player page erreur : {e}")

    return None


def find_m3u8_in_dict(data, depth=0) -> str | None:
    """Cherche récursivement une URL m3u8 dans un dict/list JSON."""
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

    if media_type == "tv":
        print(f"📂 Série  — {title} | id:{naka_id} | S{season}E{episode}")
    else:
        print(f"📂 Film   — {title} | id:{naka_id}")

    # Étape 1 : Tente les endpoints API directs
    print("\n--- Étape 1 : API directe ---")
    m3u8 = await get_m3u8_from_naka_api(
        naka_id, media_type, title, poster, season, episode
    )
    if m3u8:
        return m3u8

    # Étape 2 : Scrape la page player
    print("\n--- Étape 2 : Page player ---")
    m3u8 = await get_m3u8_from_player_page(
        naka_id, media_type, title, poster, season, episode
    )
    if m3u8:
        return m3u8

    print("❌ M3U8 introuvable")
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
#  MAIN
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
