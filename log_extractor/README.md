# Log Extractor (POC Commit 2)

A minimal manual script that:
1. Exports the Windows Security event log to a local `.evtx` file
2. Uploads it to the backend (`POST /api/logs/upload`) with `endpointID=123` and `logID=0`
3. Deletes the temporary exported file

## Run

```powershell
cd log_extractor
if not works: Set-ExecutionPolicy -Scope Process Bypass
./extract_and_upload_security_log.ps1
```

Optional backend override:

```powershell
./extract_and_upload_security_log.ps1 -BackendUrl "http://localhost:5000/api/logs/upload"
```

If you get access-denied while reading Security logs, run PowerShell as Administrator.
