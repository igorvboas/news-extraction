from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import List, Optional, Dict

import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
from urllib.parse import urlparse, parse_qs, urljoin, quote
import re
import uvicorn
import time

# === Selenium exatamente como no teste.py ===
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from webdriver_manager.chrome import ChromeDriverManager

from selenium.webdriver.chrome.service import Service

# =========================
# MODELS
# =========================
class NewsArticle(BaseModel):
    title: str = Field(..., description="Título da notícia")
    source: str = Field("", description="Fonte da notícia")
    url: str = Field("", description="URL da notícia (final, resolvida)")
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


# =========================
# RESOLVER -> EXATAMENTE A MESMA METODOLOGIA DO teste.py (com logs)
# =========================
def get_final_url_complete(google_news_url: str) -> Optional[str]:
    """
    1) requests.get(..., allow_redirects=True)
    2) se ainda for news.google.com, Selenium headless (driver.current_url)
    """
    if not google_news_url:
        print("RESOLVER: URL vazia")
        return None

    print(f"RESOLVER: iniciando para {google_news_url}")

    # 1) Requests
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "https://news.google.com/",
        }
        r = requests.get(google_news_url, headers=headers, allow_redirects=True, timeout=15)
        print(f"RESOLVER[requests]: status={r.status_code} final={r.url}")
        if r.url and "news.google.com" not in r.url:
            print(f"RESOLVER[requests]: OK -> {r.url}")
            return r.url
    except Exception as e:
        print(f"RESOLVER[requests]: erro {e!r}")

    # 2) Selenium
    try:
        chrome_options = Options()
        chrome_options.add_argument("--headless=new")
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")
        chrome_options.add_argument("--disable-gpu")
        chrome_options.add_argument("--window-size=1920,1080")
        chrome_options.add_argument(
            "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        )

        # driver = webdriver.Chrome(ChromeDriverManager().install(), options=chrome_options)
        
        service = Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=chrome_options)
        driver.set_page_load_timeout(20)
        driver.get(google_news_url)
        time.sleep(2.5)  # tempo para redirects JS
        final_url = driver.current_url
        print(f"RESOLVER[selenium]: final={final_url}")
        driver.quit()

        if final_url and "news.google.com" not in final_url:
            print(f"RESOLVER[selenium]: OK -> {final_url}")
            return final_url
    except Exception as e:
        print(f"RESOLVER[selenium]: erro {e!r}")
        try:
            driver.quit()
        except Exception:
            pass

    print("RESOLVER: não conseguiu sair de news.google.com")
    return None


# =========================
# SCRAPER (busca no Google News)
# =========================
class GoogleNewsScraper:
    def __init__(self):
        self.base_url = "https://news.google.com/search"
        # sessão só para BUSCA (não para resolver link)
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
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

            # link
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

            # filtro por janela de tempo
            if days and dt and not self._is_within_days(dt, days):
                continue

            results.append({
                "title": title,
                "source": source,
                "url": url,
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
    description="Busca notícias no Google News e resolve a URL final da matéria (requests + Selenium).",
    version="2.0.1",
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
      <p>Usa <code>requests</code> e, se necessário, <code>Selenium headless</code> — exatamente como no seu <code>teste.py</code>.</p>
      <p>Teste em <a href="/docs">/docs</a>.</p>
    </body></html>
    """


@app.get("/health")
async def health():
    return {"status": "ok", "ts": datetime.now().isoformat(), "version": "2.0.1"}


@app.get(
    "/search",
    response_model=SearchResponse,
    responses={400: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
)
async def search_news(
    person_name: str = Query(..., min_length=2, max_length=100, example="Renato Cariani"),
    days_back: int = Query(30, ge=1, le=365),
    max_results: int = Query(20, ge=1, le=100),
    resolve_final: bool = Query(True, description="Resolve a URL final via requests + Selenium (igual teste.py)"),
):
    """
    Para cada resultado do domínio news.google.com, chama get_final_url_complete(...)
    exatamente como no teste.py. Logs no console mostram por onde resolveu.
    """
    try:
        if not person_name.strip():
            raise HTTPException(status_code=400, detail="Nome da pessoa não pode estar vazio")

        raw_articles = scraper.search_news(
            person_name=person_name.strip(),
            days=days_back,
            max_results=max_results
        )

        articles: List[NewsArticle] = []
        for a in raw_articles:
            published_at = a.get("datetime").isoformat() if a.get("datetime") else ""
            a.pop("datetime", None)

            if resolve_final and isinstance(a.get("url"), str) and "news.google.com" in a["url"]:
                url_norm = scraper._normalize_gnews_href(a["url"])
                final = get_final_url_complete(url_norm)
                if final:
                    a["url"] = final

            try:
                articles.append(NewsArticle(**{
                    "title": a.get("title", ""),
                    "source": a.get("source", ""),
                    "url": a.get("url", ""),
                    "time_text": a.get("time_text", ""),
                    "published_at": published_at,
                    "description": a.get("description", ""),
                }))
            except Exception as e:
                print(f"API: erro ao montar NewsArticle: {e!r}")
                continue

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


if __name__ == "__main__":
    print("🚀 Iniciando Google News Scraper API (2.0.1)...")
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True, log_level="info")
