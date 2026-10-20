#!/usr/bin/env python3
"""Controllo accesso CardTrader. Salva SOLO codici di stato e messaggi di errore (mai il contenuto delle risposte riuscite)."""
import json, os, urllib.request, urllib.error
from datetime import datetime, timezone
tok = os.environ.get("CARDTRADER_TOKEN", "").strip()
out = {"time": datetime.now(timezone.utc).isoformat(), "checks": []}
for path in ("/info", "/expansions", "/marketplace/products?blueprint_id=212875", "/marketplace/products?expansion_id=2939&language=en&foil=true"):
    rec = {"path": path.split("?")[0]}
    for auth in (True, False):
        h = {"User-Agent": "mtg-prices/1.0", "Accept": "application/json"}
        if auth:
            h["Authorization"] = "Bearer " + tok
        try:
            with urllib.request.urlopen(urllib.request.Request("https://api.cardtrader.com/api/v2" + path, headers=h), timeout=30) as r:
                rec["auth" if auth else "noauth"] = r.status
        except urllib.error.HTTPError as e:
            body = e.read()[:300].decode("utf-8", "replace")
            rec["auth" if auth else "noauth"] = e.code
            rec[("err_auth" if auth else "err_noauth")] = {"server": e.headers.get("server"), "cf": bool(e.headers.get("cf-ray")),
                                                          "ctype": e.headers.get("content-type"), "body": body if "secret" not in body else "(nascosto)"}
        except Exception as e:
            rec["auth" if auth else "noauth"] = "errore: %s" % e
    out["checks"].append(rec)
os.makedirs("data", exist_ok=True)
json.dump(out, open("data/ct_check.json", "w"), indent=1)
print(json.dumps(out, indent=1))
