#!/usr/bin/env python3
"""Diagnostica accesso CardTrader: salva in data/ct_diag.json cosa risponde l'API (senza il token)."""
import json, os, urllib.request, urllib.error
from datetime import datetime, timezone
tok = os.environ.get("CARDTRADER_TOKEN", "").strip()
out = {"time": datetime.now(timezone.utc).isoformat(), "token_present": bool(tok), "token_len": len(tok),
       "token_looks_jwt": tok.count(".") == 2, "checks": []}
for path in ("/info", "/expansions", "/marketplace/products?blueprint_id=212875"):
    rec = {"path": path}
    try:
        req = urllib.request.Request("https://api.cardtrader.com/api/v2" + path,
                                     headers={"Authorization": "Bearer " + tok, "User-Agent": "mtg-prices/1.0", "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as r:
            body = r.read()
            rec.update(status=r.status, bytes=len(body), body=body[:200].decode("utf-8", "replace") if path == "/info" else None)
    except urllib.error.HTTPError as e:
        body = e.read()[:400].decode("utf-8", "replace")
        rec.update(status=e.code, server=e.headers.get("server"), cf_ray=bool(e.headers.get("cf-ray")),
                   content_type=e.headers.get("content-type"), body=body)
    except Exception as e:
        rec.update(error=str(e))
    out["checks"].append(rec)
os.makedirs("data", exist_ok=True)
json.dump(out, open("data/ct_diag.json", "w"), indent=1)
print(json.dumps(out, indent=1))
