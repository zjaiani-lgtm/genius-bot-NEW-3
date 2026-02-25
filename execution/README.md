# Elite Long-Only Spot Bot (Binance + Bybit)

## Run locally
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# fill keys

python -m elite_bot live --exchange binance
# or
python -m elite_bot live --exchange bybit
