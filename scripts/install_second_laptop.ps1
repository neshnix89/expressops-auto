<#
.SYNOPSIS
    One-shot installer for the MO Ref-order-no monitor on a SECOND laptop.

.DESCRIPTION
    Downloads the expressops-auto project from GitHub (or copies it from a
    local/network path), writes a config.yaml pre-filled with the SHARED state
    paths, and optionally registers the 09:15-17:00 scheduled task.

    The shared state is what makes two laptops safe: both machines read/write
    one history + one alert queue, so they never overwrite each other's JIRA
    tables and an issue alert is sent once (with failover if one laptop is off).

    Re-runnable: it refreshes the code and leaves an existing config.yaml alone
    unless -Force is given.

.EXAMPLE
    # pilot scope (default: the primary laptop's pilot containers)
    powershell -ExecutionPolicy Bypass -File .\install_second_laptop.ps1

.EXAMPLE
    # fleet-wide, matching a primary laptop with an empty pilot list
    powershell -ExecutionPolicy Bypass -File .\install_second_laptop.ps1 -FleetWide

.EXAMPLE
    # private repo: supply a GitHub personal access token
    .\install_second_laptop.ps1 -GitHubToken ghp_xxx

.EXAMPLE
    # no GitHub access at all: install from a copy on the shared drive
    .\install_second_laptop.ps1 -FromPath "Y:\...\Live MO status triggering\expressops-auto"
#>
[CmdletBinding()]
param(
    [string]$InstallDir = "$env:USERPROFILE\Documents\AI\expressops-auto",
    [string]$Branch     = "main",
    [string]$GitHubToken,
    [string]$FromPath,
    [string]$SharedBase = "Y:\88-Technology-Innovation-SEA\_Public\ePMC_PCBA_NPI_Run_Sched\e-File for NPI\Live MO status triggering",
    # Pilot scope. MUST match the primary laptop, or this machine would write to
    # containers the primary is deliberately not touching.
    [string[]]$PilotContainers = @("NPIOTHER-5589", "NPIOTHER-5322"),
    # Use -FleetWide for no restriction. Passing -PilotContainers @() does NOT
    # work through `powershell -File`: the outer shell expands @() to zero
    # arguments, so the parameter is left with no value and the call fails.
    [switch]$FleetWide,
    [switch]$Force,
    [switch]$SkipSchedule
)

$ErrorActionPreference = "Stop"

if ($FleetWide) { $PilotContainers = @() }
# Drop blanks so -PilotContainers "" also means fleet-wide.
$PilotContainers = @($PilotContainers | Where-Object { $_ -and $_.Trim() })

$repoZip = "https://github.com/neshnix89/expressops-auto/archive/refs/heads/$Branch.zip"

function Say($m) { Write-Host "[install] $m" }
function Warn($m) { Write-Host "[install] $m" -ForegroundColor Yellow }
function Die($m) { Write-Host "[install] ERROR: $m" -ForegroundColor Red; exit 1 }

Say "target: $InstallDir"

# ── 1. Prerequisites ────────────────────────────────────────────────
$py = $null
foreach ($c in @("$env:LOCALAPPDATA\Programs\Python\Python312\python.exe", "python", "py")) {
    try {
        $v = & $c --version 2>&1
        if ($LASTEXITCODE -eq 0 -and $v -match "Python 3") { $py = $c; break }
    } catch { }
}
if (-not $py) { Die "Python 3 not found. Install Python 3.12, then re-run." }
Say "python: $py  ($(& $py --version 2>&1))"

# The ODBC DSN is what the tool uses to read M3. Without it, live runs fail.
$dsnOk = $false
try {
    $dsnOk = [bool](Get-OdbcDsn -Name "ODSSG" -ErrorAction SilentlyContinue)
} catch { }
if ($dsnOk) { Say "ODBC DSN 'ODSSG': found" }
else { Warn "ODBC DSN 'ODSSG' NOT found - live M3 reads will fail until IT adds it." }

if (Test-Path $SharedBase) { Say "shared folder: reachable" }
else { Warn "shared folder NOT reachable: $SharedBase  (map the Y: drive)" }

# ── 2. Get the code ─────────────────────────────────────────────────
New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null

if ($FromPath) {
    if (-not (Test-Path $FromPath)) { Die "FromPath not found: $FromPath" }
    Say "copying from $FromPath"
    Copy-Item -Path (Join-Path $FromPath '*') -Destination $InstallDir -Recurse -Force
} else {
    $tmp = Join-Path $env:TEMP ("eo_install_" + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $tmp | Out-Null
    $zip = Join-Path $tmp "repo.zip"
    Say "downloading $Branch from GitHub..."
    try {
        if ($GitHubToken) {
            # Private repo: the API endpoint honours a token; the plain
            # codeload URL does not.
            $api = "https://api.github.com/repos/neshnix89/expressops-auto/zipball/$Branch"
            Invoke-WebRequest -Uri $api -OutFile $zip -Headers @{
                Authorization = "Bearer $GitHubToken"
                "User-Agent"  = "expressops-install"
            } -UseBasicParsing
        } else {
            # Python's urllib picks up the corporate proxy automatically, which
            # is why the existing sync uses it rather than Invoke-WebRequest.
            & $py -c "import urllib.request,sys; urllib.request.urlretrieve(sys.argv[1], sys.argv[2])" $repoZip $zip
            if ($LASTEXITCODE -ne 0) { throw "download failed" }
        }
    } catch {
        Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
        Die @"
Could not download the repo.
  * If it is PRIVATE, re-run with:  -GitHubToken <your GitHub PAT>
  * Or install from a copy instead: -FromPath "<path to an expressops-auto folder>"
Original error: $($_.Exception.Message)
"@
    }
    Say "extracting..."
    Expand-Archive -Path $zip -DestinationPath $tmp -Force
    $src = (Get-ChildItem -Path $tmp -Directory | Select-Object -First 1).FullName
    Copy-Item -Path (Join-Path $src '*') -Destination $InstallDir -Recurse -Force
    Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
}
Say "code installed."

# ── 2b. Python dependencies ─────────────────────────────────────────
# A fresh Python has none of these; the primary laptop only had them from
# earlier tasks. Without them the dry run dies on `ModuleNotFoundError`.
$req = Join-Path $InstallDir "requirements.txt"
if (Test-Path $req) {
    Say "installing Python dependencies..."
    & $py -m pip install --disable-pip-version-check -q -r $req
    if ($LASTEXITCODE -ne 0) {
        Warn "pip install reported an error. Behind a proxy, try:"
        Warn "  $py -m pip install --proxy http://<proxy>:<port> -r `"$req`""
    }
}
# Verify what actually imports, rather than trusting pip's exit code.
$missing = & $py -c @"
import importlib, sys
need = {'yaml': 'PyYAML', 'requests': 'requests', 'pyodbc': 'pyodbc'}
print(' '.join(pkg for mod, pkg in need.items()
                if importlib.util.find_spec(mod) is None))
"@
if ($missing -and $missing.Trim()) {
    Die "missing Python package(s): $missing`nInstall them, then re-run this installer."
}
Say "dependencies OK (yaml, requests, pyodbc)."

# ── 3. config.yaml ──────────────────────────────────────────────────
$cfgPath = Join-Path $InstallDir "config\config.yaml"
if ((Test-Path $cfgPath) -and -not $Force) {
    Say "config.yaml already exists - left untouched (use -Force to rewrite)."
} else {
    Write-Host ""
    Say "config.yaml - a few values are needed:"
    $jiraPat   = Read-Host "  JIRA Personal Access Token (JIRA - Profile - Personal Access Tokens)"
    $spaceLink = Read-Host "  Webex space link (Webex - the space - Copy space link), or blank to disable Webex"
    $webexOn   = if ([string]::IsNullOrWhiteSpace($spaceLink)) { "false" } else { "true" }

    $stateDir = Join-Path $SharedBase "state"
    $queueFile = Join-Path $SharedBase "webex_queue.json"
    $pilotYaml = if ($PilotContainers -and $PilotContainers.Count) {
        "[" + (($PilotContainers | ForEach-Object { '"' + $_ + '"' }) -join ", ") + "]"
    } else { "[]" }

    $cfg = @"
# ExpressOPS config - SECOND LAPTOP
# state_dir + webex.queue_file MUST match the other laptop exactly: that shared
# state is what stops the two machines overwriting each other's JIRA tables and
# double-sending alerts.
mode: live

jira:
  base_url: "https://pfjira.pepperl-fuchs.com"
  pat: "$jiraPat"
  verify_ssl: false

confluence:
  base_url: "https://pfteamspace.pepperl-fuchs.com"
  pat: ""
  space_key: "EUDEMHTM0021"

m3:
  dsn: "ODSSG"
  schema: "PFODS"

edm:
  python_exe: ""
  schema: "ADMEDP"
  connection_string: ""

logging:
  level: "INFO"
  log_dir: "logs"

mo_ref_order_monitor:
  username: "ExpressOPS MO Monitor"
  jql: 'issue in relation("filter=25423", "Project Parent", Tasks, Deviations, level1) AND "Product Type" = "SMT PCBA" AND "NPI Location" = "Singapore" ORDER BY created ASC'
  mo_number_regex: '\b(70\d{8})\b'
  no_status_label: "No Status"
  state_dir: '$stateDir'
  # Must match the primary laptop's list, or this machine writes to containers
  # the primary is not touching. Empty = fleet-wide.
  pilot_containers: $pilotYaml
  issue_regex: '(?i)IS\s*$'
  webex:
    enabled: $webexOn
    transport: "desktop"
    space_link: '$spaceLink'
    open_delay_seconds: 6
    type_delay_seconds: 2
    queue_file: '$queueFile'
    max_age_hours: 12
"@
    New-Item -ItemType Directory -Force -Path (Split-Path $cfgPath) | Out-Null
    Set-Content -Path $cfgPath -Value $cfg -Encoding UTF8
    Say "config.yaml written."
}

# ── 4. Verify ───────────────────────────────────────────────────────
Write-Host ""
Say "verifying (read-only dry run)..."
Push-Location $InstallDir
$env:PYTHONIOENCODING = "utf-8"
& $py -m tasks.mo_ref_order_monitor.main --live --dry-run
$rc = $LASTEXITCODE
Pop-Location
if ($rc -ne 0) { Die "dry run failed (exit $rc). Fix the above before scheduling." }
Say "dry run OK - nothing was written."

# ── 5. Schedule ─────────────────────────────────────────────────────
# Delegate to setup_mo_ref_order_schedule.ps1 rather than calling schtasks
# directly: schtasks cannot set AllowStartIfOnBatteries or StartWhenAvailable,
# and without those a laptop silently skips every slot while unplugged and
# never catches up after sleep — observed on the primary machine.
if ($SkipSchedule) {
    Say "skipping schedule (-SkipSchedule)."
} else {
    $sched = Join-Path $InstallDir "scripts\setup_mo_ref_order_schedule.ps1"
    if (-not (Test-Path $sched)) {
        Warn "scheduler script not found: $sched"
    } else {
        # 09:15 start for 7h45 -> :15/:45 slots, offset from the primary
        # laptop's :00/:30 so the two never run in the same minute.
        & powershell -NoProfile -ExecutionPolicy Bypass -File $sched `
            -StartTime "09:15" -DurationHours 7.75 `
            -Runner "run_mo_ref_order_monitor_portable.bat"
        if ($LASTEXITCODE -ne 0) { Warn "scheduling failed (exit $LASTEXITCODE)" }
    }
}

Write-Host ""
Say "DONE."
Say "  install : $InstallDir"
Say "  shared  : $SharedBase"
Say "  logs    : $InstallDir\logs\mo_ref_order_monitor_run.log"
Write-Host ""
Say "Webex note: keep the Webex desktop app running and signed in; the alert"
Say "briefly takes focus to type. If text arrives clipped, raise"
Say "type_delay_seconds in config.yaml."
