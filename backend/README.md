# Minimal Backend (POC Commit 1)

## Run

```bash
pip install -r requirements.txt
python app.py
```

## Upload API

- **Route:** `POST /api/logs/upload`
- **Content-Type:** `multipart/form-data`
- **File field name:** `log_file`

Example:

```bash
curl -X POST http://localhost:5000/api/logs/upload \
  -F "log_file=@sample.log"
```
