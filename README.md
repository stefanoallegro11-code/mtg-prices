# MTG Price Hunter

Confronto automatico **Cardmarket (Trend)** vs **CardTrader Zero** (Inglese e Giapponese, condizione minima Slightly Played) per la lista carte di Manabox.

- I prezzi si aggiornano da soli **ogni 4 ore** (GitHub Actions); le Scoperte ogni 4 ore sfalsate di 2 ore; messaggi Telegram alternati ogni 2 ore.
- La pagina web è su GitHub Pages: `https://stefanoallegro11-code.github.io/mtg-prices/`
- Per ogni carta: immagine della stampa esatta (Scryfall) accanto all'immagine del blueprint CardTrader abbinato, così vedi subito se la versione è quella giusta.

## File

| File | Cosa fa |
|---|---|
| `cards.txt` | La lista carte (export testo di Manabox). Modificabile dalla pagina o da GitHub. |
| `scripts/update_prices.py` | Scarica i prezzi e abbina le versioni. |
| `.github/workflows/update.yml` | Lancia lo script ogni 4 ore, quando cambia la lista e su richiesta. |
| `data/prices.json` | Risultati dell'ultimo aggiornamento. |
| `data/history.json` | Storico giornaliero (60 giorni) per variazioni e grafici. |
| `index.html` | La pagina web. |

## Come viene trovata la versione giusta su CardTrader

1. Scryfall dà la stampa esatta dal set + numero di Manabox (ID Scryfall e ID Cardmarket).
2. Dall'indice di tutti i blueprint Magic di CardTrader (aggiornato una volta al giorno) si prendono i candidati con lo stesso ID Scryfall o Cardmarket, più quello indicato da TCGTracking.
3. Ogni candidato riceve un punteggio (ID Scryfall, ID Cardmarket, numero di collezione, nome). Vince il punteggio più alto. Se nessun ID corrisponde, si ripiega su set + numero.
4. Le carte abbinate senza conferma degli ID mostrano "⚠ verifica versione".

## Impostazioni (una volta sola)

1. **Secret CardTrader**: Settings → Secrets and variables → Actions → New repository secret → nome `CARDTRADER_TOKEN`, valore = token API CardTrader.
2. **GitHub Pages**: Settings → Pages → Source "Deploy from a branch" → `main` / `(root)` → Save.
3. **Token per la pagina** (per modificare la lista e premere "Aggiorna ora"): token fine-grained limitato a questo repository con *Contents: Read and write* e *Actions: Read and write*, da incollare in ⚙ Impostazioni sulla pagina.
4. *(Facoltativo)* Avvisi Telegram per le nuove occasioni (risparmio ≥ €3): secret `TELEGRAM_TOKEN` e `TELEGRAM_CHAT_ID`.
