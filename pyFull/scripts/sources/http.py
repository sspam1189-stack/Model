import requests
from bs4 import BeautifulSoup

HEADERS = {
    "user-agent": "Mozilla/5.0 (compatible; nba-picks-bot/1.0)",
    "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
}


def fetch_text(url):
    res = requests.get(url, headers=HEADERS)
    if res.status_code != 200:
        raise Exception(f"HTTP {res.status_code} for {url}")
    return res.text


def html(url, text):
    return {"url": url, "$": BeautifulSoup(text, "html.parser")}
