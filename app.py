"""
Backend de scraping pour la Wishlist.
Remplace l'appel direct à Microlink par un scraping "maison" via Scrapling,
déployé sur Render.

Endpoints :
  GET /scrape?url=<url_du_produit>   -> infos extraites (image, boutique, titre, prix, disponibilité)
  GET /health                        -> ping utilisé pour garder le service Render éveillé

Scrapling doc : https://github.com/D4Vinci/Scrapling
"""

import os
import re
import json
import logging
import threading
import time
from urllib.parse import urlparse

import requests
from flask import Flask, request, jsonify
from flask_cors import CORS

# Fetcher "léger" (pas de navigateur headless) : rapide, suffisant pour la
# grande majorité des sites e-commerce qui exposent des balises OpenGraph /
# JSON-LD. Pour les sites qui bloquent les requêtes simples (Cloudflare,
# anti-bot poussé), on retombe sur StealthyFetcher (navigateur furtif).
from scrapling.fetchers import Fetcher, StealthyFetcher

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("wishlist-scraper")

app = Flask(__name__)
# En prod, remplacez "*" par le domaine exact de votre GitHub Pages,
# ex: CORS(app, origins=["https://votre-pseudo.github.io"])
CORS(app, origins=os.environ.get("ALLOWED_ORIGIN", "*"))

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

PRICE_RE = re.compile(r"(\d+[.,]?\d*)\s*(?:€|EUR)|(?:€|EUR)\s*(\d+[.,]?\d*)")


def _clean_price(raw):
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    raw = str(raw).replace("\xa0", " ").strip()
    m = PRICE_RE.search(raw)
    if not m:
        return None
    val = (m.group(1) or m.group(2)).replace(",", ".")
    try:
        return float(val)
    except ValueError:
        return None


def _extract_jsonld(page):
    """Cherche un bloc JSON-LD de type Product (le plus fiable pour prix/stock)."""
    result = {}
    try:
        scripts = page.css('script[type="application/ld+json"]::text')
    except Exception:
        scripts = []
    for raw in scripts:
        try:
            data = json.loads(raw)
        except Exception:
            continue
        candidates = data if isinstance(data, list) else [data]
        for node in candidates:
            if not isinstance(node, dict):
                continue
            graph = node.get("@graph")
            items = graph if isinstance(graph, list) else [node]
            for item in items:
                if not isinstance(item, dict):
                    continue
                item_type = item.get("@type", "")
                if isinstance(item_type, list):
                    is_product = "Product" in item_type
                else:
                    is_product = item_type == "Product"
                if not is_product:
                    continue
                offers = item.get("offers")
                if isinstance(offers, list):
                    offers = offers[0] if offers else {}
                if not isinstance(offers, dict):
                    offers = {}
                result.setdefault("title", item.get("name"))
                image = item.get("image")
                if isinstance(image, list):
                    image = image[0] if image else None
                if isinstance(image, dict):
                    image = image.get("url")
                result.setdefault("image", image)
                result.setdefault("price", offers.get("price"))
                availability = offers.get("availability", "")
                if availability:
                    result.setdefault(
                        "availability",
                        "outofstock" if "OutOfStock" in availability else "instock",
                    )
                result.setdefault("brand", (item.get("brand") or {}).get("name")
                                    if isinstance(item.get("brand"), dict) else item.get("brand"))
    return result


def _extract_meta(page):
    """Fallback générique via les balises OpenGraph / meta classiques."""
    def meta(selector):
        vals = page.css(selector + "::attr(content)")
        return vals[0] if vals else None

    return {
        "title": meta('meta[property="og:title"]') or meta('meta[name="twitter:title"]'),
        "image": meta('meta[property="og:image"]') or meta('meta[name="twitter:image"]'),
        "site_name": meta('meta[property="og:site_name"]'),
        "price": meta('meta[property="product:price:amount"]'),
        "availability": meta('meta[property="product:availability"]'),
    }


STOCK_KEYWORDS_OUT = [
    "rupture de stock", "épuisé", "indisponible", "sold out", "out of stock",
    "plus disponible", "hors stock",
]


def _guess_availability_from_text(page):
    try:
        text = " ".join(page.css("body::text")).lower()
    except Exception:
        text = ""
    for kw in STOCK_KEYWORDS_OUT:
        if kw in text:
            return "outofstock"
    return None


def guess_shop_from_url(url: str) -> str:
    host = urlparse(url).hostname or ""
    host = host.replace("www.", "")
    parts = host.split(".")
    name = parts[-2] if len(parts) > 2 else parts[0] if parts else ""
    return name.capitalize()


def scrape_url(url: str) -> dict:
    page = None
    used_stealth = False
    try:
        page = Fetcher.get(url, headers={"User-Agent": USER_AGENT}, timeout=15)
    except Exception as e:
        log.warning("Fetcher simple a échoué pour %s : %s", url, e)

    # Si le fetch simple échoue ou semble bloqué (page vide / status suspect),
    # on retente avec le navigateur furtif (plus lourd mais franchit les
    # protections anti-bot de type Cloudflare/Datadome).
    if page is None or getattr(page, "status", 200) >= 400:
        try:
            page = StealthyFetcher.fetch(url, headless=True, network_idle=True)
            used_stealth = True
        except Exception as e:
            log.error("StealthyFetcher a aussi échoué pour %s : %s", url, e)
            raise

    data = {}
    data.update(_extract_meta(page))
    jsonld = _extract_jsonld(page)
    for k, v in jsonld.items():
        if v:
            data[k] = v

    price = _clean_price(data.get("price"))
    availability = data.get("availability")
    if availability not in ("instock", "outofstock"):
        availability = _guess_availability_from_text(page) or "unknown"

    shop = data.get("site_name") or data.get("brand") or guess_shop_from_url(url)

    return {
        "success": True,
        "url": url,
        "title": (data.get("title") or "").strip()[:150] if data.get("title") else None,
        "image": data.get("image"),
        "shop": shop,
        "price": price,
        "availability": availability,  # "instock" | "outofstock" | "unknown"
        "used_stealth_fetcher": used_stealth,
    }


@app.route("/scrape", methods=["GET"])
def scrape():
    url = request.args.get("url", "").strip()
    if not url:
        return jsonify({"success": False, "error": "Paramètre 'url' manquant"}), 400
    try:
        result = scrape_url(url)
        return jsonify(result)
    except Exception as e:
        log.exception("Echec du scraping pour %s", url)
        return jsonify({"success": False, "error": str(e)}), 502


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "time": time.time()})


def _self_ping_loop():
    """
    Tick automatique : ping /health toutes les X minutes pour empêcher
    le service Render (plan gratuit) de se mettre en veille.
    RENDER_EXTERNAL_URL est injectée automatiquement par Render.
    """
    base_url = os.environ.get("RENDER_EXTERNAL_URL")
    interval = int(os.environ.get("SELF_PING_INTERVAL_SECONDS", "600"))  # 10 min
    if not base_url:
        log.info("RENDER_EXTERNAL_URL non défini : self-ping désactivé (exécution locale ?)")
        return
    while True:
        time.sleep(interval)
        try:
            requests.get(f"{base_url}/health", timeout=10)
            log.info("Self-ping OK")
        except Exception as e:
            log.warning("Self-ping échoué : %s", e)


if os.environ.get("ENABLE_SELF_PING", "1") == "1":
    threading.Thread(target=_self_ping_loop, daemon=True).start()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
