# Startup

Requires Python 3.11+, Git and Docker.

**1. Clone the repository**

```bash
git clone https://github.com/Sandeepdwivediii/booking.git
cd booking
```

**2. Create and activate a virtual environment**

Windows (PowerShell):

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
```

macOS / Linux:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

**3. Install dependencies**

```bash
pip install -r requirements.txt
```

**4. Start PostgreSQL**

Copy `.env.example` to `.env` and set `POSTGRES_PASSWORD` to the database password, then:

```bash
docker compose -f docker/docker-compose.yml up -d db
```

**5. Run the application**

```bash
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8001
```

**6. Open** http://127.0.0.1:8001
