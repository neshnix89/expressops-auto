<# 
ops_push.ps1 — Push files from company laptop to GitHub via API.
No Git required. Uses GitHub REST API with Personal Access Token.

Usage: powershell -ExecutionPolicy Bypass -File ops_push.ps1 -Message "your commit message"
Or from ops.bat: ops push "commit message"
#>

param(
    [string]$Message = "sync from company laptop",
    [string]$ConfigFile = "$PSScriptRoot\.ops_config"
)

$ErrorActionPreference = "Stop"

# ── Config ──
$REPO_OWNER = "neshnix89"
$REPO_NAME = "expressops-auto"
$BRANCH = "main"
$LOCAL_ROOT = "C:\Users\tmoghanan\Documents\AI\expressops-auto"

# Folders/files to skip
$SKIP_PATTERNS = @(
    "\.ops_config",
    "\.git",
    "__pycache__",
    "\.pyc$",
    "debug_",
    "result_",
    "discover_",
    "\.db$",
    "\.tmp$",
    "edge_cookies",
    "phase_b_pw\d+\.py",
    "debug_m3",
    "import pyodbc\.py"
)

# ── Load token from config ──
if (Test-Path $ConfigFile) {
    $TOKEN = (Get-Content $ConfigFile -Raw).Trim()
} else {
    Write-Host "First-time setup. Saving GitHub token..." -ForegroundColor Yellow
    $TOKEN = Read-Host "Paste your GitHub token (ghp_...)"
    $TOKEN | Out-File $ConfigFile -Encoding utf8 -NoNewline
    Write-Host "Token saved to $ConfigFile (keep this file private!)" -ForegroundColor Green
}

$HEADERS = @{
    "Authorization" = "Bearer $TOKEN"
    "Accept" = "application/vnd.github+json"
    "X-GitHub-Api-Version" = "2022-11-28"
}
$API = "https://api.github.com/repos/$REPO_OWNER/$REPO_NAME"

# ── Get current commit SHA ──
Write-Host "`n=== Pushing to $REPO_OWNER/$REPO_NAME ($BRANCH) ===" -ForegroundColor Cyan
Write-Host "Message: $Message`n"

$ref = Invoke-RestMethod -Uri "$API/git/ref/heads/$BRANCH" -Headers $HEADERS
$commitSha = $ref.object.sha
$commit = Invoke-RestMethod -Uri "$API/git/commits/$commitSha" -Headers $HEADERS
$treeSha = $commit.tree.sha
Write-Host "Current commit: $($commitSha.Substring(0,8))"

# ── Scan local files ──
$files = Get-ChildItem -Path $LOCAL_ROOT -Recurse -File | Where-Object {
    $rel = $_.FullName.Replace($LOCAL_ROOT + "\", "").Replace("\", "/")
    $skip = $false
    foreach ($pattern in $SKIP_PATTERNS) {
        if ($rel -match $pattern) { $skip = $true; break }
    }
    -not $skip
}

Write-Host "Files to push: $($files.Count)"

# ── Create blobs for each file ──
$treeItems = @()
$count = 0
foreach ($file in $files) {
    $rel = $file.FullName.Replace($LOCAL_ROOT + "\", "").Replace("\", "/")
    $count++
    
    # Read file as base64
    $bytes = [System.IO.File]::ReadAllBytes($file.FullName)
    $b64 = [Convert]::ToBase64String($bytes)
    
    # Create blob
    $blobBody = @{
        content = $b64
        encoding = "base64"
    } | ConvertTo-Json
    
    $blob = Invoke-RestMethod -Uri "$API/git/blobs" -Method Post -Headers $HEADERS -Body $blobBody -ContentType "application/json"
    
    $treeItems += @{
        path = $rel
        mode = "100644"
        type = "blob"
        sha = $blob.sha
    }
    
    Write-Host "  [$count/$($files.Count)] $rel" -ForegroundColor Gray
}

# ── Create tree ──
Write-Host "`nCreating tree..."
$treeBody = @{
    base_tree = $treeSha
    tree = $treeItems
} | ConvertTo-Json -Depth 5

$newTree = Invoke-RestMethod -Uri "$API/git/trees" -Method Post -Headers $HEADERS -Body $treeBody -ContentType "application/json"
Write-Host "Tree: $($newTree.sha.Substring(0,8))"

# ── Create commit ──
Write-Host "Creating commit..."
$commitBody = @{
    message = $Message
    tree = $newTree.sha
    parents = @($commitSha)
} | ConvertTo-Json

$newCommit = Invoke-RestMethod -Uri "$API/git/commits" -Method Post -Headers $HEADERS -Body $commitBody -ContentType "application/json"
Write-Host "Commit: $($newCommit.sha.Substring(0,8))"

# ── Update branch ref ──
Write-Host "Updating branch..."
$refBody = @{
    sha = $newCommit.sha
} | ConvertTo-Json

Invoke-RestMethod -Uri "$API/git/refs/heads/$BRANCH" -Method Patch -Headers $HEADERS -Body $refBody -ContentType "application/json" | Out-Null

Write-Host "`n=== Pushed successfully! ===" -ForegroundColor Green
Write-Host "View: https://github.com/$REPO_OWNER/$REPO_NAME" -ForegroundColor Cyan
