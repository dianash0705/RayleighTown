# Minimal Backend (POC Commit 3)

## Structure

- `app.py`: thin runtime entrypoint only
- `app_factory.py`: creates Flask app and wires routes
- `bootstrap.py`: one-time setup + runtime validation helpers
- `config.py`: paths/config constants
- `database.py`: SQLite schema and inserts
- `log_processors.py`: per-log parsing/extraction logic
- `log_registry.py`: supported `logID` registry
- `routes.py`: API route handlers
- `setup_environment.py`: one-time environment setup command

## One-time setup (run once per machine/project)

```bash
pip install -r requirements.txt
python setup_environment.py
```

This creates persistent backend state (for example `backend/data/logs.db`).

## Run app (run any time)

```bash
python app.py
```

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

Example:

```bash
curl -X POST http://localhost:5000/api/logs/upload \
  -F "endpointID=123" \
  -F "logID=0" \
  -F "log_file=@sample.log"
```
