# scripts/sources/http.py
# Simple HTTP helpers — Python equivalent of http.mjs

import requests
from bs4 import BeautifulSoup

HEADERS = {
    "user-agent": "Mozilla/5.0 (compatible; nba-picks-bot/1.0)",
    "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


def fetch_text(url):
    """Fetch a URL and return its text content."""
    res = requests.get(url, headers=HEADERS, timeout=30)
    if res.status_code != 200:
        raise Exception(f"HTTP {res.status_code} for {url}")
    return res.text


def html(url, text):
    """Parse HTML text into a BeautifulSoup object. Returns dict with url and soup."""
    return {"url": url, "soup": BeautifulSoup(text, "html.parser")}


def fetch_json(url, headers=None):
    """Fetch a URL and return parsed JSON."""
    res = requests.get(url, headers=headers or HEADERS, timeout=30)
    if res.status_code != 200:
        raise Exception(f"HTTP {res.status_code} for {url}")
    return res.json()
