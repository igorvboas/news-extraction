from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from typing import List, Optional, Dict
import requests
from bs4 import BeautifulSoup
import urllib.parse
from datetime import datetime, timedelta
import re
import uvicorn
from fastapi.middleware.cors import CORSMiddleware

# Modelos Pydantic para request/response
class NewsArticle(BaseModel):
    title: str = Field(..., description="Título da notícia")
    source: str = Field("", description="Fonte da notícia")
    url: str = Field("", description="URL da notícia")
    time_text: str = Field("", description="Texto do tempo (ex: '2 horas atrás')")
    published_at: str = Field("", description="Data e hora da notícia (ISO format)")
    description: str = Field("", description="Descrição/snippet da notícia")

class SearchResponse(BaseModel):
    success: bool = Field(..., description="Status da operação")
    person_name: str = Field(..., description="Nome da pessoa pesquisada")
    days_back: int = Field(..., description="Número de dias pesquisados")
    max_results: int = Field(..., description="Número máximo de resultados solicitados")
    total_found: int = Field(..., description="Total de notícias encontradas")
    articles: List[NewsArticle] = Field(..., description="Lista de artigos encontrados")
    message: str = Field("", description="Mensagem adicional")

class ErrorResponse(BaseModel):
    success: bool = Field(False, description="Status da operação")
    error: str = Field(..., description="Mensagem de erro")
    details: str = Field("", description="Detalhes adicionais do erro")

# Classe do Scraper (adaptada da versão original)
class GoogleNewsScraper:
    def __init__(self, user_agent: str = None):
        self.base_url = "https://news.google.com/search"
        self.headers = {
            'User-Agent': user_agent or 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'pt-BR,pt;q=0.9,en;q=0.8',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
        }
        self.session = requests.Session()
        self.session.headers.update(self.headers)
    
    def _parse_time_ago(self, time_text: str) -> Optional[datetime]:
        try:
            time_text = time_text.lower().strip()
            now = datetime.now()
            
            patterns = {
                r'(\d+)\s*hora[s]?\s*atrás': 'hours',
                r'(\d+)\s*dia[s]?\s*atrás': 'days',
                r'(\d+)\s*semana[s]?\s*atrás': 'weeks',
                r'(\d+)\s*mês\s*atrás': 'months',
                r'(\d+)\s*meses\s*atrás': 'months',
                r'(\d+)\s*ano[s]?\s*atrás': 'years',
                r'(\d+)\s*minuto[s]?\s*atrás': 'minutes',
            }
            
            for pattern, unit in patterns.items():
                match = re.search(pattern, time_text)
                if match:
                    value = int(match.group(1))
                    
                    if unit == 'minutes':
                        return now - timedelta(minutes=value)
                    elif unit == 'hours':
                        return now - timedelta(hours=value)
                    elif unit == 'days':
                        return now - timedelta(days=value)
                    elif unit == 'weeks':
                        return now - timedelta(weeks=value)
                    elif unit == 'months':
                        return now - timedelta(days=value * 30)
                    elif unit == 'years':
                        return now - timedelta(days=value * 365)
            
            return None
            
        except Exception:
            return None
    
    def _is_within_days(self, article_time: datetime, days: int) -> bool:
        if not article_time:
            return False
        cutoff_date = datetime.now() - timedelta(days=days)
        return article_time >= cutoff_date
    
    def search_news(self, person_name: str, days: int = 30, max_results: int = 50) -> List[Dict]:
        try:
            query = urllib.parse.quote(person_name)
            search_url = f"{self.base_url}?q={query}&hl=pt-BR&gl=BR&ceid=BR:pt-419"
            
            print(f"Buscando notícias para: {person_name}")
            print(f"URL: {search_url}")
            
            response = self.session.get(search_url, timeout=30)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.content, 'html.parser')
            
            selectors = [
                'article',
                '[data-n-au]',
                '.JtKRv',
                '.xrnccd',
                '.WwrzSb',
                '.DY5T1d'
            ]
            
            found_articles = []
            for selector in selectors:
                found = soup.select(selector)
                if found:
                    found_articles = found
                    break
            
            if not found_articles:
                found_articles = soup.find_all(['a', 'div', 'article'], 
                                             string=re.compile(person_name, re.IGNORECASE))
            
            print(f"Encontrados {len(found_articles)} elementos potenciais")
            
            articles = []
            for article_element in found_articles[:max_results * 2]:
                try:
                    article_data = self._extract_article_data(article_element, person_name)
                    
                    if article_data and article_data.get('title'):
                        article_time = article_data.get('datetime')
                        if not days or not article_time or self._is_within_days(article_time, days):
                            # Converte datetime para string ISO
                            if article_time:
                                article_data['published_at'] = article_time.isoformat()
                            else:
                                article_data['published_at'] = ""
                            
                            # Remove o campo datetime original
                            if 'datetime' in article_data:
                                del article_data['datetime']
                            
                            # Garante que todos os campos necessários existam
                            article_data.setdefault('source', '')
                            article_data.setdefault('url', '')
                            article_data.setdefault('time_text', '')
                            article_data.setdefault('description', '')
                            
                            articles.append(article_data)
                            
                            if len(articles) >= max_results:
                                break
                                
                except Exception as e:
                    print(f"Erro ao processar artigo: {e}")
                    continue
            
            print(f"Encontradas {len(articles)} notícias relevantes")
            return articles[:max_results]
            
        except Exception as e:
            raise Exception(f"Erro na busca: {str(e)}")
    
    def _extract_article_data(self, element, person_name: str) -> Optional[Dict]:
        try:
            article_data = {}
            
            title_selectors = ['h3', 'h4', '.JtKRv', '.ipQwMb', '.mCBkyc']
            title = None
            
            for selector in title_selectors:
                title_element = element.select_one(selector)
                if title_element:
                    title = title_element.get_text(strip=True)
                    break
            
            if not title:
                if element.name == 'a':
                    title = element.get_text(strip=True)
            
            if not title or len(title) < 10:
                return None
            
            if person_name.lower() not in title.lower():
                return None
            
            article_data['title'] = title
            
            link_element = element if element.name == 'a' else element.find('a')
            if link_element and link_element.get('href'):
                href = link_element.get('href')
                if href.startswith('./'):
                    href = 'https://news.google.com' + href[1:]
                elif href.startswith('/'):
                    href = 'https://news.google.com' + href
                article_data['url'] = href
            
            source_selectors = ['.wEwyrc', '.vr1PYe', '.CEMjEf']
            for selector in source_selectors:
                source_element = element.select_one(selector)
                if source_element:
                    article_data['source'] = source_element.get_text(strip=True)
                    break
            
            time_selectors = ['.r0bn4c', '.WW6dff', 'time']
            for selector in time_selectors:
                time_element = element.select_one(selector)
                if time_element:
                    time_text = time_element.get_text(strip=True)
                    article_data['time_text'] = time_text
                    article_data['datetime'] = self._parse_time_ago(time_text)
                    break
            
            desc_selectors = ['.st', '.Y3v8qd']
            for selector in desc_selectors:
                desc_element = element.select_one(selector)
                if desc_element:
                    article_data['description'] = desc_element.get_text(strip=True)
                    break
            
            return article_data
            
        except Exception:
            return None

# Inicialização da API
app = FastAPI(
    title="Google News Scraper API",
    description="API para buscar notícias de pessoas específicas no Google News",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc"
)

# Configuração CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Instância global do scraper
scraper = GoogleNewsScraper()

# Endpoints
@app.get("/", response_class=HTMLResponse)
async def root():
    """Página inicial com informações da API"""
    html_content = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Google News Scraper API</title>
        <style>
            body { font-family: Arial, sans-serif; margin: 40px; background-color: #f5f5f5; }
            .container { max-width: 800px; margin: 0 auto; background: white; padding: 30px; border-radius: 8px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }
            h1 { color: #333; border-bottom: 2px solid #4285f4; padding-bottom: 10px; }
            .endpoint { background: #f8f9fa; padding: 15px; margin: 10px 0; border-radius: 5px; border-left: 4px solid #4285f4; }
            .method { color: #28a745; font-weight: bold; }
            .url { color: #6c757d; font-family: monospace; }
            a { color: #4285f4; text-decoration: none; }
            a:hover { text-decoration: underline; }
            .example { background: #e9ecef; padding: 10px; border-radius: 4px; font-family: monospace; }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>🗞️ Google News Scraper API</h1>
            <p>API para buscar notícias de pessoas específicas no Google News com filtros de data e limite de resultados.</p>
            
            <h2>📋 Documentação</h2>
            <ul>
                <li><a href="/docs" target="_blank">Swagger UI</a> - Interface interativa</li>
                <li><a href="/redoc" target="_blank">ReDoc</a> - Documentação alternativa</li>
            </ul>
            
            <h2>🔧 Endpoints Disponíveis</h2>
            
            <div class="endpoint">
                <div><span class="method">GET</span> <span class="url">/search</span></div>
                <p>Busca notícias de uma pessoa específica</p>
                <strong>Parâmetros:</strong>
                <ul>
                    <li><code>person_name</code> (obrigatório) - Nome da pessoa</li>
                    <li><code>days_back</code> (opcional, padrão: 30) - Dias anteriores para buscar</li>
                    <li><code>max_results</code> (opcional, padrão: 20) - Máximo de resultados</li>
                </ul>
            </div>
            
            <div class="endpoint">
                <div><span class="method">GET</span> <span class="url">/health</span></div>
                <p>Verifica o status da API</p>
            </div>
            
            <h2>💡 Exemplo de Uso</h2>
            <div class="example">
                GET /search?person_name=Renato Cariani&days_back=7&max_results=10
            </div>
            
            <h2>🚀 Como usar</h2>
            <ol>
                <li>Acesse <a href="/docs">/docs</a> para testar interativamente</li>
                <li>Use qualquer cliente HTTP (curl, Postman, etc.)</li>
                <li>Integre com seu código usando a biblioteca requests do Python</li>
            </ol>
        </div>
    </body>
    </html>
    """
    return html_content

@app.get("/health")
async def health_check():
    """Endpoint para verificar se a API está funcionando"""
    return {
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "version": "1.0.0"
    }

@app.get(
    "/search",
    response_model=SearchResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Parâmetros inválidos"},
        500: {"model": ErrorResponse, "description": "Erro interno do servidor"},
    }
)
async def search_news(
    person_name: str = Query(
        ...,
        description="Nome da pessoa para buscar notícias",
        example="Renato Cariani",
        min_length=2,
        max_length=100
    ),
    days_back: int = Query(
        30,
        description="Número de dias anteriores para buscar (1-365)",
        ge=1,
        le=365,
        example=30
    ),
    max_results: int = Query(
        20,
        description="Número máximo de resultados (1-100)",
        ge=1,
        le=100,
        example=20
    )
):
    """
    Busca notícias de uma pessoa específica no Google News
    
    - **person_name**: Nome da pessoa (obrigatório)
    - **days_back**: Quantos dias anteriores buscar (1-365, padrão: 30)
    - **max_results**: Máximo de resultados (1-100, padrão: 20)
    
    Retorna uma lista de notícias com título, fonte, URL, data e descrição.
    """
    try:
        # Validações adicionais
        if not person_name.strip():
            raise HTTPException(
                status_code=400,
                detail="Nome da pessoa não pode estar vazio"
            )
        
        print(f"API: Buscando notícias para '{person_name}' - {days_back} dias - max {max_results}")
        
        # Busca as notícias
        articles_data = scraper.search_news(
            person_name=person_name.strip(),
            days=days_back,
            max_results=max_results
        )
        
        # Converte para objetos Pydantic
        articles = []
        for article_data in articles_data:
            try:
                article = NewsArticle(**article_data)
                articles.append(article)
            except Exception as e:
                print(f"Erro ao processar artigo: {e}")
                print(f"Dados do artigo: {article_data}")
                continue
        
        # Monta a resposta
        message = f"Encontradas {len(articles)} notícias" if articles else "Nenhuma notícia encontrada"
        
        response = SearchResponse(
            success=True,
            person_name=person_name.strip(),
            days_back=days_back,
            max_results=max_results,
            total_found=len(articles),
            articles=articles,
            message=message
        )
        
        return response
        
    except HTTPException:
        raise
    except Exception as e:
        print(f"Erro na API: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Erro interno: {str(e)}"
        )

@app.get("/search/summary")
async def search_summary(
    person_name: str = Query(..., description="Nome da pessoa"),
    days_back: int = Query(30, ge=1, le=365)
):
    """
    Versão resumida da busca - retorna apenas estatísticas
    """
    try:
        articles_data = scraper.search_news(person_name.strip(), days_back, 100)
        
        # Agrupa por fonte
        sources = {}
        for article in articles_data:
            source = article.get('source', 'Desconhecida')
            if source:  # Só conta fontes não vazias
                sources[source] = sources.get(source, 0) + 1
        
        return {
            "person_name": person_name.strip(),
            "days_back": days_back,
            "total_articles": len(articles_data),
            "sources_count": len(sources),
            "top_sources": dict(sorted(sources.items(), key=lambda x: x[1], reverse=True)[:5]) if sources else {}
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Executar o servidor
if __name__ == "__main__":
    print("🚀 Iniciando Google News Scraper API...")
    print("📖 Documentação: http://localhost:8000/docs")
    print("🏠 Página inicial: http://localhost:8000")
    print("❤️  Health check: http://localhost:8000/health")
    print("-" * 50)
    
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level="info"
    )