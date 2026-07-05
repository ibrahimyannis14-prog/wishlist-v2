# Backend Wishlist — Scraping avec Scrapling

Ce dossier contient le backend Python qui remplace l'appel direct à Microlink.
Il scrape la page produit (image, boutique, titre, prix, disponibilité) et
renvoie du JSON à `index.html`.

## 1. Installation locale

Scrapling nécessite **Python 3.10 ou plus récent**.

```bash
cd backend
python3 -m venv venv
source venv/bin/activate        # Windows : venv\Scripts\activate

# Installe Flask, Scrapling + tout ce qu'il faut pour les fetchers
pip install -r requirements.txt

# IMPORTANT : installe les navigateurs utilisés par StealthyFetcher
# (obligatoire même si vous n'utilisez que le fetcher "simple" au départ,
# car scrapling importe ce module au démarrage)
scrapling install
```

`scrapling install` télécharge les binaires du navigateur furtif utilisé pour
contourner les protections anti-bot (Cloudflare, etc.) — c'est normal que ce
soit volumineux (plusieurs centaines de Mo).

## 2. Lancer en local

```bash
python app.py
```

Le serveur écoute sur `http://localhost:5000`. Testez :

```bash
curl "http://localhost:5000/scrape?url=https://www.exemple-boutique.com/produit/123"
curl "http://localhost:5000/health"
```

Réponse attendue de `/scrape` :

```json
{
  "success": true,
  "url": "...",
  "title": "Nom du produit",
  "image": "https://.../photo.jpg",
  "shop": "Nomdelaboutique",
  "price": 49.9,
  "availability": "instock",
  "used_stealth_fetcher": false
}
```

## 3. Déploiement sur Render

### Option A — via `render.yaml` (recommandé)

1. Poussez ce dossier `backend/` dans votre dépôt GitHub (à côté de
   `index.html`, par exemple dans `wishlist/backend/`).
2. Sur [render.com](https://render.com) → **New** → **Blueprint** → sélectionnez
   votre repo. Render détecte automatiquement `render.yaml` et crée le service.
3. Dans les variables d'environnement du service, remplacez `ALLOWED_ORIGIN`
   par l'URL exacte de votre GitHub Pages
   (ex : `https://votre-pseudo.github.io`).

### Option B — manuellement

1. **New** → **Web Service** → connectez votre repo GitHub.
2. **Root Directory** : `backend` (si le dossier n'est pas à la racine du repo).
3. **Build Command** :
   ```
   pip install -r requirements.txt && scrapling install
   ```
4. **Start Command** :
   ```
   gunicorn app:app --bind 0.0.0.0:$PORT --timeout 60 --workers 2
   ```
5. **Plan** : Free (suffisant pour commencer).
6. Variables d'environnement à ajouter :
   - `ALLOWED_ORIGIN` = `https://votre-pseudo.github.io`
   - `ENABLE_SELF_PING` = `1`
   - `SELF_PING_INTERVAL_SECONDS` = `600`

Render fournit automatiquement `RENDER_EXTERNAL_URL` et `PORT`, pas besoin de
les définir vous-même.

## 4. Le "tick automatique" anti-veille

Le plan gratuit de Render met un service web en veille après ~15 minutes sans
requête. `app.py` lance un thread en arrière-plan (`_self_ping_loop`) qui
s'auto-ping toutes les 10 minutes (`SELF_PING_INTERVAL_SECONDS`) via
`RENDER_EXTERNAL_URL` pour rester éveillé.

C'est une solution simple, gratuite. Si vous préférez une solution plus
robuste et découplée du process web (recommandé pour la prod), utilisez plutôt
un **Render Cron Job** séparé qui appelle `/health` toutes les 10 minutes, ou
un service externe gratuit comme UptimeRobot / cron-job.org pointant vers
`https://VOTRE-SERVICE.onrender.com/health`. Dans ce cas, mettez
`ENABLE_SELF_PING=0` pour éviter le double ping.

## 5. Connecter `index.html`

Dans `index.html`, mettez à jour cette ligne avec l'URL réelle de votre
service Render :

```js
const BACKEND_URL = "https://wishlist-scraper.onrender.com";
```

## 6. Limites à connaître

- Le fetcher simple (`Fetcher`) est rapide mais peut échouer sur des sites
  très protégés → le code retombe alors automatiquement sur `StealthyFetcher`
  (navigateur headless furtif), plus lent mais plus robuste.
- `StealthyFetcher` consomme plus de RAM ; sur le plan gratuit Render (512 Mo),
  évitez de lancer plusieurs requêtes de scraping en parallèle.
- L'extraction du prix/disponibilité se base sur le JSON-LD `schema.org/Product`
  quand il existe (le plus fiable), sinon sur les balises OpenGraph, sinon sur
  une recherche de mots-clés ("rupture de stock", "sold out"...). Pour des
  boutiques spécifiques qui ne suivent aucun de ces standards, vous pouvez
  ajouter des règles dédiées dans `_extract_meta` / `_extract_jsonld`.
