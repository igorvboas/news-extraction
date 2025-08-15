import requests
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
import time

def get_final_url_complete(google_news_url):
    """
    Tenta múltiplos métodos para obter a URL final
    """
    
    # Método 1: Requests simples
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        response = requests.get(google_news_url, headers=headers, allow_redirects=True, timeout=10)
        if response.url != google_news_url and "news.google.com" not in response.url:
            return response.url
    except:
        pass
    
    # Método 2: Selenium como fallback
    try:
        chrome_options = Options()
        chrome_options.add_argument('--headless')
        chrome_options.add_argument('--no-sandbox')
        
        driver = webdriver.Chrome(options=chrome_options)
        driver.get(google_news_url)
        time.sleep(3)  # Aguarda redirecionamento
        
        final_url = driver.current_url
        driver.quit()
        
        if "news.google.com" not in final_url:
            return final_url
            
    except Exception as e:
        if 'driver' in locals():
            driver.quit()
    
    return None

# Uso
google_url = "https://news.google.com/read/CBMioAFBVV95cUxQSHlqTGktQnJkTzFWTk8zM3lHeUY2Rjd6NWdwQ2NkOWNPUTJ4VXRXa0tkdHl0amN4QXI1dkZGaTM5OFhVVDV3UjFVMjhoSUowc3RVWGZFbTdIVGxJaUdROWkyamJYcG5VcXdxQ2I2T0RfWlhTQmpxbVZHaUVUZlZkUGJsQ1NUVmFzNWpSSU5zMEJmSDBzNzVGbG5uUnMxdWlk?hl=pt-BR&gl=BR&ceid=BR%3Apt-419"
final_url = get_final_url_complete(google_url)
print(f"URL final: {final_url}")