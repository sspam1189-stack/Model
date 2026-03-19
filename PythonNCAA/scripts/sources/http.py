import requests
from bs4 import BeautifulSoup


def fetch_text(url):
    res = requests.get(url, headers={
        "user-agent": "Mozilla/5.0 (compatible; ncaa-picks-bot/1.0)",
        "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
    })
    if not res.ok:
        raise Exception(f"HTTP {res.status_code} for {url}")
    return res.text


def html(url, text):
    return {"url": url, "$": BeautifulSoup(text, "html.parser")}
