param(
    [int]$EventId = 3,
    [string]$Folder = '',
    [string]$SecretsFile = '.env.relay',
    [string]$RelayUrl = 'https://dtfxnuxluuwvjdcyxmnc.supabase.co/functions/v1/relay',
    [switch]$InstallTask,
    [switch]$Monitor
)

$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$projectRoot = (Resolve-Path -LiteralPath $PSScriptRoot).Path
$python = Join-Path $projectRoot 'venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) { throw 'The project virtual environment is missing.' }
if (-not $Folder) { $Folder = 'G:\Aathif & sahana\aathif and sahana' }
$Folder = (Resolve-Path -LiteralPath $Folder).Path
if (-not (Test-Path -LiteralPath $SecretsFile)) { throw "Missing worker settings file: $SecretsFile" }

# Confirm the local event and existing worker credentials agree before starting.
$preflight = @"
import sqlite3,sys
from pathlib import Path
from dotenv import dotenv_values
import requests
from app.core.firebase import get_firebase_app
from app.core.firebase_config import get_firebase_settings
con=sqlite3.connect(Path('event_photos.db').resolve().as_uri()+'?mode=ro',uri=True)
row=con.execute('select e.event_name from events e join users u on u.id=e.owner_id where e.id=? and u.is_active=1',(int(sys.argv[1]),)).fetchone()
if not row: raise SystemExit('The local event is missing or its owner is inactive.')
firebase=get_firebase_settings()
if firebase.enabled:
    get_firebase_app()
    print('Firebase Admin initialized.',flush=True)
else:
    print('Firebase is disabled.',flush=True)
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

function Get-ProjectPythonProcesses {
    $processes = @(Get-CimInstance Win32_Process | Where-Object { $_.Name -match '^python(w)?\.exe$' })
    @($processes | Where-Object {
        $item = $_
        $parent = $processes | Where-Object { $_.ProcessId -eq $item.ParentProcessId } | Select-Object -First 1
        ($item.CommandLine -and $item.CommandLine.Contains($projectRoot)) -or
        ($parent -and $parent.CommandLine -and $parent.CommandLine.Contains($projectRoot))
    })
}

$projectProcesses = Get-ProjectPythonProcesses
$eventPattern = '--event-id(?:=|\s+)' + $EventId + '(?:\s|$)'
$importerPattern = 'app\.services\.camera_importer.*' + $eventPattern
$backendPattern = 'uvicorn\s+app\.main:app'
$relayPattern = 'app\.services\.relay_worker.*' + $eventPattern
$staleImporters = @($projectProcesses | Where-Object {
    $_.CommandLine -match $importerPattern -and
    $_.CommandLine.IndexOf($Folder, [StringComparison]::OrdinalIgnoreCase) -lt 0
})
foreach ($process in $staleImporters) {
    Stop-Process -Id $process.ProcessId -Force -ErrorAction Stop
    Write-Host "Stopped importer for a different folder (PID $($process.ProcessId))."
}
if ($staleImporters.Count) {
    Start-Sleep -Seconds 2
    $projectProcesses = Get-ProjectPythonProcesses
}

$logFolder = Join-Path $env:TEMP 'event-photo-finder-services'
New-Item -ItemType Directory -Path $logFolder -Force | Out-Null

function Start-ServiceIfMissing {
    param([string]$Name, [string]$Pattern, [string[]]$Arguments)
    $currentProcesses = Get-ProjectPythonProcesses
    $existing = @($currentProcesses | Where-Object { $_.CommandLine -match $Pattern })
    if ($Name -eq 'backend') {
        if (Test-BackendHealth) {
            Write-Host 'backend is already healthy.'
            return
        }
        if ($existing.Count -and (Wait-BackendHealthy)) {
            Write-Host 'backend finished starting and is healthy.'
            return
        }
        if ($existing.Count) {
            Stop-ProjectService $Pattern
            if (-not (Wait-BackendPortFree)) {
                throw 'Port 8000 is still occupied after stopping the project backend.'
            }
        } else {
            $listener = Get-NetTCPConnection -State Listen -LocalPort 8000 -ErrorAction SilentlyContinue |
                Select-Object -First 1
            if ($listener) {
                throw "Port 8000 is already in use by PID $($listener.OwningProcess); backend was not started."
            }
        }
    } elseif ($existing.Count) {
        Write-Host "$Name is already running."
        return
    }
    $stamp = Get-Date -Format 'yyyyMMdd-HHmmss-fff'
    $logBase = Join-Path $logFolder ($Name + '-' + $stamp)
    $started = Start-Process -FilePath $python -ArgumentList $Arguments -WorkingDirectory $projectRoot -WindowStyle Hidden -RedirectStandardOutput ($logBase + '.stdout.log') -RedirectStandardError ($logBase + '.stderr.log') -PassThru
    Write-Host "$Name started (PID $($started.Id))."
}

function Stop-ProjectService {
    param([string]$Pattern)
    $matchingProcesses = @(Get-ProjectPythonProcesses | Where-Object { $_.CommandLine -match $Pattern })
    foreach ($process in $matchingProcesses) {
        Stop-Process -Id $process.ProcessId -Force -ErrorAction Stop
    }
}

function Test-BackendHealth {
    try {
        $health = Invoke-RestMethod -Uri 'http://127.0.0.1:8000/health' -TimeoutSec 3
        return $health.status -eq 'healthy'
    } catch {
        return $false
    }
}

function Wait-BackendHealthy {
    for ($attempt = 0; $attempt -lt 15; $attempt++) {
        if (Test-BackendHealth) { return $true }
        Start-Sleep -Seconds 2
    }
    return $false
}

function Wait-BackendPortFree {
    for ($attempt = 0; $attempt -lt 10; $attempt++) {
        $listener = Get-NetTCPConnection -State Listen -LocalPort 8000 -ErrorAction SilentlyContinue |
            Select-Object -First 1
        if (-not $listener) { return $true }
        Start-Sleep -Seconds 1
    }
    return $false
}

Start-ServiceIfMissing 'backend' $backendPattern @('-u','-m','uvicorn','app.main:app','--host','127.0.0.1','--port','8000')
Start-ServiceIfMissing 'importer' $importerPattern @('-u','-m','app.services.camera_importer','--folder',('"{0}"' -f $Folder),'--event-id',"$EventId")
Start-ServiceIfMissing 'relay' $relayPattern @('-u','-m','app.services.relay_worker','--event-id',"$EventId",'--secrets-file',('"{0}"' -f $SecretsFile),'--relay-url',$RelayUrl)

if (-not (Wait-BackendHealthy)) { throw "The backend did not become healthy. Check logs in $logFolder" }
Write-Host 'Backend health check passed.'
& $python -B -m utilities.check_photo_ingest --folder $Folder --event-id $EventId --once
if ($LASTEXITCODE -ne 0) { Write-Warning 'The importer may still be starting. See its log for progress.' }
Write-Host "Services are running. New images are indexed automatically. Logs: $logFolder"
Write-Host 'Open https://sm-photo-studio.vercel.app and refresh the page before searching.'

if ($InstallTask) {
    $taskName = 'Event AI Photo Finder Services'
    $userId = [Security.Principal.WindowsIdentity]::GetCurrent().Name
    $actionArguments = '-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "{0}" -Monitor' -f $MyInvocation.MyCommand.Path
    $action = New-ScheduledTaskAction -Execute (Join-Path $PSHOME 'powershell.exe') -Argument $actionArguments
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User $userId
    $principal = New-ScheduledTaskPrincipal -UserId $userId -LogonType Interactive -RunLevel Limited
    $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit ([TimeSpan]::Zero) `
        -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -MultipleInstances IgnoreNew
    Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal `
        -Settings $settings -Description 'Keeps the local photo finder backend, importer, and relay running.' `
        -Force | Out-Null
    Start-ScheduledTask -TaskName $taskName
    Write-Host "Installed and started the '$taskName' task for $userId."
    return
}

if ($Monitor) {
    Write-Host 'Service supervisor is active. It checks every 15 seconds; stop it with Ctrl+C.'
    while ($true) {
        Start-Sleep -Seconds 15
        try {
            if (-not (Test-BackendHealth)) {
                Write-Warning 'Backend health check failed; restarting the backend.'
                Start-ServiceIfMissing 'backend' $backendPattern @('-u','-m','uvicorn','app.main:app','--host','127.0.0.1','--port','8000')
                if (-not (Wait-BackendHealthy)) { throw 'Backend did not recover.' }
            }
            Start-ServiceIfMissing 'importer' $importerPattern @('-u','-m','app.services.camera_importer','--folder',('"{0}"' -f $Folder),'--event-id',"$EventId")
            Start-ServiceIfMissing 'relay' $relayPattern @('-u','-m','app.services.relay_worker','--event-id',"$EventId",'--secrets-file',('"{0}"' -f $SecretsFile),'--relay-url',$RelayUrl)
        } catch {
            Write-Warning "Service recovery check failed ($($_.Exception.GetType().Name)); retrying."
        }
    }
}
