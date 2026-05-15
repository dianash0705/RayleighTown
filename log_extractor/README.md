# Log Extractor (POC Commit 2)

A minimal manual script that:
1. Exports the Windows Security event log to a local `.evtx` file, or uses a custom log file you provide
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

You can combine both options if needed:

```powershell
./extract_and_upload_security_log.ps1 -BackendUrl "http://localhost:2222/api/logs/upload" -EndpointID "456" -CustomLogPath "C:\path\to\your\security.evtx"
```

Behavior notes:

- If `-CustomLogPath` is provided, the script uploads that file directly.
- If `-EndpointID` is omitted, the script uses `123`.
- If `-CustomLogPath` is omitted, the script exports the local Windows Security log to `log_extractor\out` first, then uploads it.
- The temporary `.evtx` file is removed only in the export flow.

If you get access-denied while reading Security logs, run PowerShell as Administrator.
