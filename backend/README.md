# Minimal Backend (POC Commit 3)

## Structure

- `app.py`: creates Flask app, validates runtime environment, wires routes, and runs server
- `brain.py`: pure brain logic (data in -> alerts out), storage-agnostic
- `bootstrap.py`: one-time setup + runtime validation helpers
- `config.py`: paths/config constants
- `database.py`: SQLite schema, event inserts, and brain DB adapters
- `log_processors.py`: per-log parsing/extraction logic
- `log_registry.py`: supported `logID` registry
- `routes.py`: API route handlers
- `run_brain.py`: manual brain trigger script (on-demand)
- `setup_environment.py`: one-time environment setup command

## One-time setup (run once per machine/project)

```bash
pip install -r requirements.txt
python setup_environment.py
```

This creates persistent backend state (for example `backend/data/logs.db`).

## Optional: copy project to isolated test directory

From repository root:

```powershell
./setup_test_environment.ps1 -TargetPath "C:\temp\RayleighTownTest"
```

The script copies project files to the target location and excludes local/git artifacts.

## Run app (run any time)

```bash
python app.py
```

## Run brain manually (on demand)

```bash
python run_brain.py --endpointID 123
```

## Demo shortcut (from repository root)

```powershell
./run_demo_backend.ps1
```

This runs install + setup + app start in one command.
It always uses a local repository virtual environment (`.venv`) and creates it if needed.

## Upload API

- **Route:** `POST /api/logs/upload`
- **Content-Type:** `multipart/form-data`
- **Form fields:** `endpointID`, `logID`, `log_file`

On upload, backend will:
1. Resolve parser and whitelist by `logID`
2. Read the uploaded log file
3. Keep only whitelisted event IDs for that log type
4. Insert matching events into SQLite DB table `logs`

Currently supported log types:
- `0` = Windows Security (`.evtx`) with whitelist `4624`, `4625`, `4634`

Database path: `backend/data/logs.db`

## Brain (MVP Step 1)

The backend currently includes a very simple first-step brain module:

- Works per `endpointID`, per `nativeEventID`
- Rule: if an event type appears **4 or more times** for an endpoint, create one alert
- Triggering is **separate** from upload ingestion (not auto-run by `/api/logs/upload`)
- Brain is split into independent steps:
  1. fetch endpoint events via injected fetch function
  2. generate alerts in pure Python logic
  3. publish alerts via injected publish function
- SQLite-specific fetch/publish adapters are implemented in `database.py`
- Uses placeholder values for currently unknown fields (`tsBegin`, `tsEnd`, `periodTs`, `confidence`)

Current tables:

- `logs`: ingested events
- `alerts`: alert-level data (`alertID`, `endpointID`, `tsBegin`, `tsEnd`, `periodTs`, `confidence`)
- `eventAlertMap`: mapping rows (`eventID`, `alertID`, `confidence`)

Example:

```bash
curl -X POST http://localhost:5000/api/logs/upload \
  -F "endpointID=123" \
  -F "logID=0" \
  -F "log_file=@sample.log"
```
