import requests
from bs4 import BeautifulSoup
import urllib.parse
from datetime import datetime, timedelta
import time
import json
from typing import List, Dict, Optional
import re

class GoogleNewsScraper:
    def __init__(self, user_agent: str = None):
        """
        Inicializa o scraper do Google News
        
        Args:
            user_agent: User agent personalizado para as requisições
        """
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
        """
        Converte texto de tempo relativo para datetime
        
        Args:
            time_text: Texto como "18 horas atrás", "2 dias atrás", etc.
            
        Returns:
            datetime object ou None se não conseguir parsear
        """
        try:
            time_text = time_text.lower().strip()
            now = datetime.now()
            
            # Padrões para diferentes formatos de tempo
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
                        return now - timedelta(days=value * 30)  # Aproximação
                    elif unit == 'years':
                        return now - timedelta(days=value * 365)  # Aproximação
            
            return None
            
        except Exception as e:
            print(f"Erro ao parsear tempo: {time_text} - {e}")
            return None
    
    def _is_within_days(self, article_time: datetime, days: int) -> bool:
        """
        Verifica se o artigo está dentro do período de dias especificado
        
        Args:
            article_time: Datetime do artigo
            days: Número de dias para filtrar
            
        Returns:
            True se estiver dentro do período, False caso contrário
        """
        if not article_time:
            return False
            
        cutoff_date = datetime.now() - timedelta(days=days)
        return article_time >= cutoff_date
    
    def search_news(self, person_name: str, days: int = 30, max_results: int = 50) -> List[Dict]:
        """
        Busca notícias de uma pessoa específica no Google News
        
        Args:
            person_name: Nome da pessoa para buscar
            days: Número de dias anteriores para buscar (padrão: 30)
            max_results: Número máximo de resultados (padrão: 50)
            
        Returns:
            Lista de dicionários contendo informações das notícias
        """
        try:
            # Codifica o nome para URL
            query = urllib.parse.quote(person_name)
            
            # Constrói a URL de busca
            search_url = f"{self.base_url}?q={query}&hl=pt-BR&gl=BR&ceid=BR:pt-419"
            
            print(f"Buscando notícias para: {person_name}")
            print(f"URL: {search_url}")
            
            # Faz a requisição
            response = self.session.get(search_url)
            response.raise_for_status()
            
            # Parse do HTML
            soup = BeautifulSoup(response.content, 'html.parser')
            
            # Busca por artigos - o Google News usa estruturas complexas
            articles = []
            
            # Tenta diferentes seletores que o Google News pode usar
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
                # Fallback: busca por links que contenham texto
                found_articles = soup.find_all(['a', 'div', 'article'], 
                                             string=re.compile(person_name, re.IGNORECASE))
            
            print(f"Encontrados {len(found_articles)} elementos potenciais")
            
            for article_element in found_articles[:max_results * 2]:  # Busca mais para filtrar depois
                try:
                    article_data = self._extract_article_data(article_element, person_name)
                    
                    if article_data and article_data.get('title'):
                        # Verifica se está dentro do período de dias
                        article_time = article_data.get('datetime')
                        if not days or not article_time or self._is_within_days(article_time, days):
                            articles.append(article_data)
                            
                            if len(articles) >= max_results:
                                break
                                
                except Exception as e:
                    print(f"Erro ao processar artigo: {e}")
                    continue
            
            print(f"Encontradas {len(articles)} notícias relevantes")
            return articles[:max_results]
            
        except requests.RequestException as e:
            print(f"Erro na requisição: {e}")
            return []
        except Exception as e:
            print(f"Erro inesperado: {e}")
            return []
    
    def _extract_article_data(self, element, person_name: str) -> Optional[Dict]:
        """
        Extrai dados de um elemento de artigo
        
        Args:
            element: Elemento HTML do BeautifulSoup
            person_name: Nome da pessoa para verificar relevância
            
        Returns:
            Dicionário com dados do artigo ou None
        """
        try:
            article_data = {}
            
            # Busca título
            title_selectors = ['h3', 'h4', '.JtKRv', '.ipQwMb', '.mCBkyc']
            title = None
            
            for selector in title_selectors:
                title_element = element.select_one(selector)
                if title_element:
                    title = title_element.get_text(strip=True)
                    break
            
            if not title:
                # Tenta pegar o texto do próprio elemento se for um link
                if element.name == 'a':
                    title = element.get_text(strip=True)
            
            if not title or len(title) < 10:
                return None
            
            # Verifica se o título contém o nome da pessoa
            if person_name.lower() not in title.lower():
                return None
            
            article_data['title'] = title
            
            # Busca URL
            link_element = element if element.name == 'a' else element.find('a')
            if link_element and link_element.get('href'):
                href = link_element.get('href')
                if href.startswith('./'):
                    href = 'https://news.google.com' + href[1:]
                elif href.startswith('/'):
                    href = 'https://news.google.com' + href
                article_data['url'] = href
            
            # Busca fonte
            source_selectors = ['.wEwyrc', '.vr1PYe', '.CEMjEf']
            for selector in source_selectors:
                source_element = element.select_one(selector)
                if source_element:
                    article_data['source'] = source_element.get_text(strip=True)
                    break
            
            # Busca tempo
            time_selectors = ['.r0bn4c', '.WW6dff', 'time']
            for selector in time_selectors:
                time_element = element.select_one(selector)
                if time_element:
                    time_text = time_element.get_text(strip=True)
                    article_data['time_text'] = time_text
                    article_data['datetime'] = self._parse_time_ago(time_text)
                    break
            
            # Busca descrição/snippet
            desc_selectors = ['.st', '.Y3v8qd']
            for selector in desc_selectors:
                desc_element = element.select_one(selector)
                if desc_element:
                    article_data['description'] = desc_element.get_text(strip=True)
                    break
            
            return article_data
            
        except Exception as e:
            print(f"Erro ao extrair dados do artigo: {e}")
            return None


def main():
    """Função principal para demonstrar o uso do scraper"""
    
    # Inicializa o scraper
    scraper = GoogleNewsScraper()
    
    # Parâmetros de busca
    person_name = "Renato Cariani"  # Altere aqui o nome da pessoa
    days_back = 30  # Últimos 30 dias
    max_results = 20  # Máximo 20 resultados
    
    print(f"Iniciando busca por notícias de '{person_name}' dos últimos {days_back} dias...")
    print("-" * 80)
    
    # Busca as notícias
    articles = scraper.search_news(person_name, days_back, max_results)
    
    if articles:
        print(f"\nEncontradas {len(articles)} notícias:\n")
        
        for i, article in enumerate(articles, 1):
            print(f"📰 Notícia {i}:")
            print(f"   Título: {article.get('title', 'N/A')}")
            print(f"   Fonte: {article.get('source', 'N/A')}")
            print(f"   Tempo: {article.get('time_text', 'N/A')}")
            if article.get('url'):
                print(f"   URL: {article['url']}")
            if article.get('description'):
                print(f"   Descrição: {article['description'][:100]}...")
            print("-" * 50)
            
        # Salva em JSON
        output_file = f"noticias_{person_name.replace(' ', '_')}.json"
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(articles, f, ensure_ascii=False, indent=2, default=str)
        
        print(f"\n✅ Resultados salvos em: {output_file}")
        
    else:
        print("❌ Nenhuma notícia encontrada.")


if __name__ == "__main__":
    # Adiciona delay para evitar rate limiting
    time.sleep(1)
    main()