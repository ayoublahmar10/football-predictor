# ⚽ Football AI Predictor

AI-powered football match predictor for the top 5 European leagues. Analyzes upcoming fixtures using real stats and generates predictions with estimated odds — plus a combo ticket builder targeting a minimum total odds.

---

## Features

- **AI predictions per match** — Result (1X2), Goals (Over/Under 2.5), Both Teams to Score (BTTS)
- **Estimated decimal odds** and confidence level (high / medium / low) for each prediction
- **Combo ticket generator** — greedy selection of the most confident picks until a target total odds is reached (default ×100, up to 10 matches)
- **Combo history** — every generated combo is saved locally; view or delete past entries
- **Persistent prediction cache** — predictions are cached for 12 hours to preserve API quota
- **Rate-limited API client** — safely stays within the football-data.org free tier (10 req/min)

---

## Supported Leagues

| Code | League | Country |
|------|--------|---------|
| `PL` | Premier League | England |
| `PD` | La Liga | Spain |
| `BL1` | Bundesliga | Germany |
| `SA` | Serie A | Italy |
| `FL1` | Ligue 1 | France |

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | Python · FastAPI · Uvicorn |
| AI | Groq API (`llama-3.3-70b-versatile`) |
| Football data | football-data.org API v4 |
| Frontend | Vanilla JS · Single HTML file · Dark theme |
| HTTP client | httpx (async) |
| Data validation | Pydantic v2 |

---

## Getting Started

### 1. Clone the repo

```bash
git clone https://github.com/your-username/football-predictor.git
cd football-predictor
```

### 2. Create a virtual environment

```bash
python -m venv venv
source venv/Scripts/activate   # Windows
# source venv/bin/activate     # macOS / Linux
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Configure API keys

Copy `.env.example` to `.env` and fill in your keys:

```bash
cp .env.example .env
```

```env
FOOTBALL_DATA_KEY=your_football_data_key_here
GROQ_API_KEY=your_groq_key_here
CURRENT_SEASON=2025
```

- **football-data.org** — free API key at [football-data.org](https://www.football-data.org/client/register)
- **Groq** — free API key at [console.groq.com](https://console.groq.com)

### 5. Run the app

```bash
python -m uvicorn app.main:app --reload
```

Open [http://localhost:8000](http://localhost:8000) in your browser.

---

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/predictions` | AI predictions for multiple leagues |
| `GET` | `/predictions/{fixture_id}` | Prediction for a specific match |
| `GET` | `/combo` | Generate a combo ticket |
| `GET` | `/history` | List of past generated combos |
| `DELETE` | `/history/{id}` | Delete a history entry |
| `GET` | `/leagues` | List of supported leagues |
| `GET` | `/debug/fixtures/{code}` | Raw fixture data (debug) |
| `GET` | `/debug/predict-one/{code}` | Step-by-step prediction debug |

### Example requests

```bash
# Predictions for Premier League and La Liga, next 3 matches each
GET /predictions?leagues=PL,PD&next=3

# Combo: up to 10 picks, minimum total odds of 100
GET /combo?leagues=PL,PD,BL1,SA,FL1&next=5&max_picks=10&min_odds=100
```

---

## Project Structure

```
football-predictor/
├── app/
│   ├── config.py              # Settings & league codes
│   ├── main.py                # FastAPI app entry point
│   ├── models/
│   │   └── schemas.py         # Pydantic models
│   ├── routers/
│   │   └── predictions.py     # API endpoints + combo logic
│   └── services/
│       ├── football_api.py    # football-data.org client (rate-limited)
│       ├── predictor.py       # Groq LLM prediction engine + cache
│       └── history.py         # Combo history persistence
├── static/
│   └── index.html             # Frontend SPA
├── .env.example
├── requirements.txt
└── README.md
```

---

## Notes

- The free Groq tier has a **100k tokens/day** limit. The prediction cache prevents re-consuming tokens for the same fixture within 12 hours.
- The free football-data.org tier allows **10 requests/minute**. The client serializes requests with a 7-second minimum interval.
- Combo generation can take **3–6 minutes** depending on the number of leagues and fixtures selected (each fixture requires ~5 API calls).
- Local data (`data/` directory) is excluded from version control — predictions cache and combo history are stored only on your machine.
