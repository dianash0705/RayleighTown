# Log Extractor (POC Commit 2)

A minimal manual script that:
1. Exports the Windows Security event log to a local `.evtx` file, or uses a custom `.evtx` or `.json` log file you provide
2. Uploads it to the backend (`POST /api/logs/upload`) with a configurable `endpointID` and `logID=0`
3. Deletes the temporary exported file only when the script created it

## Run

```powershell
cd log_extractor
if not works: Set-ExecutionPolicy -Scope Process Bypass
./extract_and_upload_security_log.ps1
```

Optional backend override:

```powershell
./extract_and_upload_security_log.ps1 -BackendUrl "http://localhost:2222/api/logs/upload"
```

Set a custom endpoint ID:

```powershell
./extract_and_upload_security_log.ps1 -EndpointID "456"
```

Use a custom log file instead of exporting Security:

```powershell
./extract_and_upload_security_log.ps1 -CustomLogPath "C:\path\to\your\security.evtx"
```

JSON logs are supported too:

```powershell
./extract_and_upload_security_log.ps1 -CustomLogPath "C:\path\to\your\aptsimulator.json"
```

If you upload a JSON file, the script will attempt to read the first JSON record's `SourceName` (or `source`/`Source`) field and send it to the server as `sourceName`. The backend will map that name to a configured `logID` using `backend/log_source_map.json` and record the mapped `logID` in the database. This lets JSON uploads be labeled by their original source without changing the default `logID` behavior for EVTX exports.

You can combine both options if needed:

```powershell
./extract_and_upload_security_log.ps1 -BackendUrl "http://localhost:2222/api/logs/upload" -EndpointID "456" -CustomLogPath "C:\path\to\your\security.evtx"
```

Behavior notes:

- If `-CustomLogPath` is provided, the script uploads that file directly.
- If `-EndpointID` is omitted, the script uses `123`.
- If `-CustomLogPath` is omitted, the script exports the local Windows Security log to `log_extractor\out` first, then uploads it.
- The temporary `.evtx` file is removed only in the export flow.
- The backend accepts both `.evtx` and JSON Lines files as long as they contain `EventID` and a timestamp field.

If you get access-denied while reading Security logs, run PowerShell as Administrator.
