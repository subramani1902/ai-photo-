param(
    [int]$EventId = 3,
    [string]$Folder = '',
    [string]$SecretsFile = '.env.relay',
    [string]$RelayUrl = 'https://dtfxnuxluuwvjdcyxmnc.supabase.co/functions/v1/relay'
)

$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$projectRoot = (Resolve-Path -LiteralPath $PSScriptRoot).Path
$python = Join-Path $projectRoot 'venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) { throw 'The project virtual environment is missing.' }
if (-not $Folder) { $Folder = Join-Path $projectRoot 'Aathif-Sahana' }
$Folder = (Resolve-Path -LiteralPath $Folder).Path
if (-not (Test-Path -LiteralPath $SecretsFile)) { throw "Missing worker settings file: $SecretsFile" }

# Confirm the local event and existing worker credentials agree before starting.
$preflight = @"
import sqlite3,sys
from pathlib import Path
from dotenv import dotenv_values
import requests
con=sqlite3.connect(Path('event_photos.db').resolve().as_uri()+'?mode=ro',uri=True)
row=con.execute('select e.event_name from events e join users u on u.id=e.owner_id where e.id=? and u.is_active=1',(int(sys.argv[1]),)).fetchone()
if not row: raise SystemExit('The local event is missing or its owner is inactive.')
secret=dotenv_values(sys.argv[2]).get('PHOTO_FINDER_WORKER_SECRET')
if not secret: raise SystemExit('The worker secret is missing from the settings file.')
print('Using local event '+sys.argv[1]+': '+row[0],flush=True)
response=None
try:
    response=requests.get(sys.argv[3].rstrip('/')+'/worker/showcase',headers={'Authorization':'Bearer '+secret},timeout=(3,5),allow_redirects=False)
except requests.RequestException as exc:
    print('Temporary relay connection issue ('+type(exc).__name__+'). Existing services stay running; the worker will retry.',flush=True)
if response is not None:
    if response.status_code!=200: raise SystemExit('Relay authentication failed (HTTP '+str(response.status_code)+').')
    if response.json().get('event_name')!=row[0]: raise SystemExit('The settings file belongs to a different event.')
"@
& $python -u -B -c $preflight $EventId $SecretsFile $RelayUrl
if ($LASTEXITCODE -ne 0) { throw 'The selected event settings need attention. Existing services were not changed.' }

$processes = @(Get-CimInstance Win32_Process | Where-Object { $_.Name -match '^python(w)?\.exe$' })
$projectProcesses = @($processes | Where-Object {
    $item = $_
    $parent = $processes | Where-Object { $_.ProcessId -eq $item.ParentProcessId } | Select-Object -First 1
    ($item.CommandLine -and $item.CommandLine.Contains($projectRoot)) -or
    ($parent -and $parent.CommandLine -and $parent.CommandLine.Contains($projectRoot))
})
$logFolder = Join-Path $env:TEMP 'event-photo-finder-services'
New-Item -ItemType Directory -Path $logFolder -Force | Out-Null
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss-fff'

function Start-ServiceIfMissing {
    param([string]$Name, [string]$Pattern, [string[]]$Arguments)
    $existing = @($projectProcesses | Where-Object { $_.CommandLine -match $Pattern })
    if ($existing.Count) {
        Write-Host "$Name is already running."
        return
    }
    $logBase = Join-Path $logFolder ($Name + '-' + $stamp)
    $started = Start-Process -FilePath $python -ArgumentList $Arguments -WorkingDirectory $projectRoot -WindowStyle Hidden -RedirectStandardOutput ($logBase + '.stdout.log') -RedirectStandardError ($logBase + '.stderr.log') -PassThru
    Write-Host "$Name started (PID $($started.Id))."
}

$eventPattern = '--event-id(?:=|\s+)' + $EventId + '(?:\s|$)'
Start-ServiceIfMissing 'backend' 'uvicorn\s+app\.main:app' @('-u','-m','uvicorn','app.main:app','--host','127.0.0.1','--port','8000')
Start-ServiceIfMissing 'importer' ('app\.services\.camera_importer.*' + $eventPattern) @('-u','-m','app.services.camera_importer','--folder',('"{0}"' -f $Folder),'--event-id',"$EventId")
Start-ServiceIfMissing 'relay' ('app\.services\.relay_worker.*' + $eventPattern) @('-u','-m','app.services.relay_worker','--event-id',"$EventId",'--secrets-file',('"{0}"' -f $SecretsFile),'--relay-url',$RelayUrl)

$healthy = $false
for ($attempt = 0; $attempt -lt 15; $attempt++) {
    try {
        $health = Invoke-RestMethod -Uri 'http://127.0.0.1:8000/health' -TimeoutSec 3
        if ($health.status -eq 'healthy') { $healthy = $true; break }
    } catch { Start-Sleep -Seconds 2 }
}
if (-not $healthy) { throw "The backend did not become healthy. Check logs in $logFolder" }
Write-Host 'Backend health check passed.'
& $python -B -m utilities.check_photo_ingest --folder $Folder --event-id $EventId --once
if ($LASTEXITCODE -ne 0) { Write-Warning 'The importer may still be starting. See its log for progress.' }
Write-Host "Services are running. New images are indexed automatically. Logs: $logFolder"
Write-Host 'Open https://sm-photo-studio.vercel.app and refresh the page before searching.'
