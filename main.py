from fastapi import FastAPI, HTTPException, Query, Body
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Literal
import uvicorn

# --- scraping / requests
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
from urllib.parse import urlparse, parse_qs, urljoin, quote
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
import time

# --- selenium (igual ao teste.py, com Service correto)
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.common.exceptions import TimeoutException

import os, time
# from webdriver_manager.core.utils import ChromeType

# =========================
# MODELS
# =========================
class NewsArticle(BaseModel):
    title: str = Field(..., description="Título da notícia")
    source: str = Field("", description="Fonte da notícia")
    url: str = Field("", description="URL da notícia (ORIGINAL do Google News)")
    time_text: str = Field("", description="Texto do tempo (ex: '2 horas atrás')")
    published_at: str = Field("", description="Data e hora da notícia (ISO format)")
    description: str = Field("", description="Descrição/snippet")


class SearchResponse(BaseModel):
    success: bool
    person_name: str
    days_back: int
    max_results: int
    total_found: int
    articles: List[NewsArticle]
    message: str = ""


class ErrorResponse(BaseModel):
    success: bool = False
    error: str
    details: str = ""


class ResolveOneResponse(BaseModel):
    original: str
    final: Optional[str] = None
    method: Literal["requests", "selenium", "unchanged", "error"]
    error: Optional[str] = None


class ResolveBatchRequest(BaseModel):
    urls: List[str]
    use_selenium: bool = True
    timeout: int = 15
    max_workers: int = 6


class ResolveBatchResponse(BaseModel):
    success: bool = True
    results: List[ResolveOneResponse]


# =========================
# RESOLVER (EXATAMENTE teste.py)
# =========================
def resolve_final_url_like_testepy(google_news_url: str, use_selenium: bool = True, timeout: int = 15) -> ResolveOneResponse:
    """
    1) requests.get(..., allow_redirects=True)
    2) se continuar em news.google.com e use_selenium=True, Selenium headless
    """
    if not google_news_url:
        return ResolveOneResponse(original="", final=None, method="error", error="empty url")

    # requests
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "https://news.google.com/",
        }
        r = requests.get(google_news_url, headers=headers, allow_redirects=True, timeout=timeout)
        if r.url and "news.google.com" not in r.url:
            return ResolveOneResponse(original=google_news_url, final=r.url, method="requests")
    except Exception as e:
        req_err = str(e)
    else:
        req_err = None

    # selenium (fallback)
    # selenium (fallback)
    if use_selenium:
        driver = None
        try:
            options = Options()
            options.add_argument("--headless=new")
            options.add_argument("--no-sandbox")
            options.add_argument("--disable-dev-shm-usage")
            options.add_argument("--disable-gpu")
            options.add_argument("--no-zygote")
            options.add_argument("--disable-software-rasterizer")
            options.add_argument("--hide-scrollbars")
            options.add_argument("--window-size=1920,1080")
            options.add_argument("--user-agent=Mozilla/5.0")

            # 👉 binários dentro do container
            options.binary_location = os.getenv("CHROME_BIN", "/usr/bin/chromium")
            service = Service(os.getenv("CHROMEDRIVER_PATH", "/usr/bin/chromedriver"))

            # 👉 não espere render completo
            options.page_load_strategy = "eager"   # ("none" também funciona)

            driver = webdriver.Chrome(service=service, options=options)

            # tempo para a navegação; se estourar, ainda tentamos pegar current_url
            driver.set_page_load_timeout(timeout)

            try:
                driver.get(google_news_url)
            except TimeoutException:
                # ignore — normalmente já redirecionou
                pass

            # dá um respiro pro redirect JS concluir
            time.sleep(2.0)

            final_url = driver.current_url
            if final_url and "news.google.com" not in final_url:
                return ResolveOneResponse(original=google_news_url, final=final_url, method="selenium")

        except Exception as e:
            return ResolveOneResponse(
                original=google_news_url,
                final=None,
                method="error",
                error=f"selenium: {e}"
            )
        finally:
            try:
                if driver:
                    driver.quit()
            except Exception:
                pass


    # sem mudança
    return ResolveOneResponse(
        original=google_news_url,
        final=google_news_url,
        method="unchanged",
        error=req_err
    )


# =========================
# SCRAPER (BUSCA)
# =========================
class GoogleNewsScraper:
    def __init__(self):
        self.base_url = "https://news.google.com/search"
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
            "Connection": "keep-alive",
            "Referer": "https://news.google.com/",
        })

    @staticmethod
    def _normalize_gnews_href(href: str) -> str:
        if not href:
            return href
        if href.startswith("./"):
            return "https://news.google.com" + href[1:]
        if href.startswith("/"):
            return "https://news.google.com" + href
        return href

    @staticmethod
    def _parse_time_ago(time_text: str) -> Optional[datetime]:
        try:
            t = (time_text or "").lower().strip()
            now = datetime.now()
            rules = [
                (r"(\d+)\s*minuto[s]?\s*atrás", "minutes", 1),
                (r"(\d+)\s*hora[s]?\s*atrás", "hours", 1),
                (r"(\d+)\s*dia[s]?\s*atrás", "days", 1),
                (r"(\d+)\s*semana[s]?\s*atrás", "days", 7),
                (r"(\d+)\s*m[eê]s(?:es)?\s*atrás", "days", 30),
                (r"(\d+)\s*ano[s]?\s*atrás", "days", 365),
            ]
            for pattern, unit, factor in rules:
                m = re.search(pattern, t)
                if m:
                    val = int(m.group(1)) * factor
                    return now - timedelta(**{unit: val})
            return None
        except Exception:
            return None

    @staticmethod
    def _is_within_days(dt: Optional[datetime], days: int) -> bool:
        if not dt:
            return False
        return dt >= (datetime.now() - timedelta(days=days))

    def search_news(self, person_name: str, days: int, max_results: int) -> List[Dict]:
        q = quote(person_name)
        url = f"{self.base_url}?q={q}&hl=pt-BR&gl=BR&ceid=BR:pt-419"
        r = self.session.get(url, timeout=30)
        r.raise_for_status()
        soup = BeautifulSoup(r.content, "html.parser")

        candidates = soup.select("article") or soup.select(".xrnccd") or []
        if not candidates:
            candidates = soup.find_all(["article", "div"])

        results: List[Dict] = []
        for el in candidates:
            if len(results) >= max_results:
                break

            # título
            title = None
            for sel in ["h3", "h4", "a.DY5T1d", ".JtKRv", ".ipQwMb", ".mCBkyc"]:
                node = el.select_one(sel)
                if node:
                    title = node.get_text(strip=True)
                    break
            if not title and el.name == "a":
                title = el.get_text(strip=True)
            if not title or len(title) < 6:
                continue

            # link (ORIGINAL do Google)
            link_el = el if el.name == "a" else el.find("a", href=True)
            url = ""
            if link_el:
                url = self._normalize_gnews_href(link_el.get("href", ""))

            # fonte
            source = ""
            for sel in [".wEwyrc", ".vr1PYe", ".CEMjEf"]:
                node = el.select_one(sel)
                if node:
                    source = node.get_text(strip=True)
                    break

            # tempo
            time_text, dt = "", None
            for sel in [".r0bn4c", ".WW6dff", "time"]:
                node = el.select_one(sel)
                if node:
                    time_text = node.get_text(strip=True)
                    dt = self._parse_time_ago(time_text)
                    break

            # descrição
            desc = ""
            for sel in [".Y3v8qd", ".st"]:
                node = el.select_one(sel)
                if node:
                    desc = node.get_text(strip=True)
                    break

            # filtro por tempo
            if days and dt and not self._is_within_days(dt, days):
                continue

            results.append({
                "title": title,
                "source": source,
                "url": url,  # <-- mantém o ORIGINAL do Google
                "time_text": time_text,
                "datetime": dt,
                "description": desc,
            })

        return results[:max_results]


# =========================
# FASTAPI
# =========================
app = FastAPI(
    title="Google News Scraper API",
    description="Busca notícias no Google News (URL original). Endpoint separado para resolver URL final (requests + Selenium).",
    version="3.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_credentials=True,
    allow_methods=["*"], allow_headers=["*"],
)

scraper = GoogleNewsScraper()


@app.get("/", response_class=HTMLResponse)
async def root():
    return """
    <html><head><title>Google News Scraper API</title></head>
    <body style="font-family:Arial;padding:24px">
      <h1>🗞️ Google News Scraper API</h1>
      <ul>
        <li><strong>/search</strong> → retorna URLs ORIGINAIS do Google News (rápido)</li>
        <li><strong>GET /resolve?u=URL</strong> → resolve uma URL (requests → Selenium)</li>
        <li><strong>POST /resolve</strong> com {"urls":[...]} → resolve em lote (paralelo)</li>
      </ul>
      <p>Veja <a href="/docs">/docs</a>.</p>
    </body></html>
    """


@app.get("/health")
async def health():
    return {"status": "ok", "ts": datetime.now().isoformat(), "version": "3.0.0"}


@app.get(
    "/search",
    response_model=SearchResponse,
    responses={400: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
)
async def search_news(
    person_name: str = Query(..., min_length=2, max_length=100, example="Renato Cariani"),
    days_back: int = Query(30, ge=1, le=365),
    max_results: int = Query(20, ge=1, le=100),
):
    """
    Importante: este endpoint devolve **apenas** a URL ORIGINAL do Google News.
    Use /resolve para converter em URL final do veículo.
    """
    try:
        if not person_name.strip():
            raise HTTPException(status_code=400, detail="Nome da pessoa não pode estar vazio")

        raw = scraper.search_news(
            person_name=person_name.strip(),
            days=days_back,
            max_results=max_results
        )

        articles: List[NewsArticle] = []
        for a in raw:
            published_at = a.get("datetime").isoformat() if a.get("datetime") else ""
            a.pop("datetime", None)

            articles.append(NewsArticle(**{
                "title": a.get("title", ""),
                "source": a.get("source", ""),
                "url": a.get("url", ""),  # mantém Google
                "time_text": a.get("time_text", ""),
                "published_at": published_at,
                "description": a.get("description", ""),
            }))

        return SearchResponse(
            success=True,
            person_name=person_name.strip(),
            days_back=days_back,
            max_results=max_results,
            total_found=len(articles),
            articles=articles,
            message=f"Encontradas {len(articles)} notícia(s)",
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro interno: {e}")


# ---------- RESOLVE (unitária) ----------
@app.get("/resolve", response_model=ResolveOneResponse, responses={400: {"model": ErrorResponse}})
async def resolve_one(
    u: str = Query(..., description="URL (normalmente news.google.com/read/...)"),
    use_selenium: bool = Query(True),
    timeout: int = Query(15, ge=3, le=60),
):
    if not u:
        raise HTTPException(status_code=400, detail="Parâmetro 'u' é obrigatório")
    result = resolve_final_url_like_testepy(u, use_selenium=use_selenium, timeout=timeout)
    return result


# ---------- RESOLVE (lote) ----------
@app.post("/resolve", response_model=ResolveBatchResponse, responses={400: {"model": ErrorResponse}})
async def resolve_batch(body: ResolveBatchRequest = Body(...)):
    if not body.urls:
        raise HTTPException(status_code=400, detail="Lista 'urls' vazia")

    results: List[ResolveOneResponse] = []
    # paralelismo simples (I/O bound)
    with ThreadPoolExecutor(max_workers=max(1, body.max_workers)) as ex:
        future_map = {
            ex.submit(resolve_final_url_like_testepy, u, body.use_selenium, body.timeout): u
            for u in body.urls
        }
        for fut in as_completed(future_map):
            try:
                results.append(fut.result())
            except Exception as e:
                u = future_map[fut]
                results.append(ResolveOneResponse(original=u, final=None, method="error", error=str(e)))

    return ResolveBatchResponse(results=results)


if __name__ == "__main__":
    print("🚀 Iniciando Google News Scraper API (3.0.0)...")
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True, log_level="info")
