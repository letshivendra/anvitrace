# Single-Command Launcher for ANVITRACE Demo Workstation
# Ensures Ollama is active, model is present, sets environment, starts FastAPI with 1 worker, and prints /api/health.

param (
    [string]$Model = "llama3.2:3b",
    [int]$Timeout = 30,
    [string]$DemoMode = "0",
    [int]$Port = 8000
)

$ErrorActionPreference = "Continue"

Write-Host "==================================================================" -ForegroundColor Cyan
Write-Host " ANVITRACE - Forensic Workstation and Case Explainer Demo Runner" -ForegroundColor Cyan
Write-Host "==================================================================" -ForegroundColor Cyan

# 1. Check Ollama Service
Write-Host "[1/5] Checking local Ollama service on http://localhost:11434..." -NoNewline
$ollamaRunning = $false
try {
    $resp = Invoke-RestMethod -Uri "http://localhost:11434/api/tags" -Method Get -TimeoutSec 3 -ErrorAction SilentlyContinue
    if ($resp -and $resp.models) {
        $ollamaRunning = $true
        Write-Host " [ONLINE]" -ForegroundColor Green
    }
} catch {
    Write-Host " [OFFLINE]" -ForegroundColor Yellow
}

if (-not $ollamaRunning) {
    Write-Host "      Attempting to launch Ollama background service..." -ForegroundColor Yellow
    $ollamaCmd = Get-Command ollama -ErrorAction SilentlyContinue
    if ($ollamaCmd) {
        Start-Process -FilePath "ollama" -ArgumentList "serve" -WindowStyle Hidden
        Start-Sleep -Seconds 3
        try {
            $resp = Invoke-RestMethod -Uri "http://localhost:11434/api/tags" -Method Get -TimeoutSec 5 -ErrorAction SilentlyContinue
            if ($resp) {
                Write-Host "      Ollama daemon successfully started." -ForegroundColor Green
                $ollamaRunning = $true
            }
        } catch {
            Write-Host "      Could not reach Ollama after start attempt. Falling back to template mode." -ForegroundColor Yellow
        }
    } else {
        Write-Host "      Ollama binary not found in PATH. Explainer will run in template mode." -ForegroundColor Yellow
    }
}

# 2. Check Model Pulled
if ($ollamaRunning) {
    Write-Host "[2/5] Checking model $Model..." -NoNewline
    $hasModel = $false
    try {
        $tags = Invoke-RestMethod -Uri "http://localhost:11434/api/tags" -Method Get -TimeoutSec 3
        foreach ($m in $tags.models) {
            if ($m.name -eq $Model -or $m.name -like "$Model*") {
                $hasModel = $true
                break
            }
        }
    } catch {}

    if ($hasModel) {
        Write-Host " [FOUND]" -ForegroundColor Green
    } else {
        Write-Host " [NOT FOUND]" -ForegroundColor Yellow
        Write-Host "      Pulling model $Model (this may take a few minutes)..." -ForegroundColor Cyan
        & ollama pull $Model
    }
} else {
    Write-Host "[2/5] Skipping model check (Ollama offline; template mode active)." -ForegroundColor DarkGray
}

# 3. Set Environment
Write-Host "[3/5] Configuring environment variables..."
$env:OLLAMA_MODEL = if ($env:OLLAMA_MODEL) { $env:OLLAMA_MODEL } else { $Model }
$env:OLLAMA_TIMEOUT = if ($env:OLLAMA_TIMEOUT) { $env:OLLAMA_TIMEOUT } else { [string]$Timeout }
$env:DEMO_MODE = if ($env:DEMO_MODE) { $env:DEMO_MODE } else { $DemoMode }
Write-Host "      OLLAMA_MODEL   = $env:OLLAMA_MODEL" -ForegroundColor Gray
Write-Host "      OLLAMA_TIMEOUT = $env:OLLAMA_TIMEOUT" -ForegroundColor Gray
Write-Host "      DEMO_MODE      = $env:DEMO_MODE" -ForegroundColor Gray

# 4. Resolve Python executable
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$projectRoot = Split-Path -Parent $scriptDir
$backendDir = Join-Path $projectRoot "backend"

$pythonPath = "python"
$knownPython = "C:\Users\offic\AppData\Local\Programs\Python\Python313\python.exe"
if (Test-Path $knownPython) {
    $pythonPath = $knownPython
}

# 5. Start Backend Process (Single Uvicorn Worker)
Write-Host "[4/5] Starting FastAPI backend with 1 worker on port $Port..." -ForegroundColor Cyan

$portActive = $false
try {
    $healthTest = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/health" -Method Get -TimeoutSec 2 -ErrorAction SilentlyContinue
    if ($healthTest -and $healthTest.status -eq "ok") {
        $portActive = $true
        Write-Host "      Backend is already running on port $Port." -ForegroundColor Green
    }
} catch {}

if (-not $portActive) {
    Start-Process -FilePath $pythonPath `
        -ArgumentList "-m uvicorn app.main:app --host 127.0.0.1 --port $Port --workers 1" `
        -WorkingDirectory $backendDir `
        -WindowStyle Hidden

    Write-Host "      Waiting for backend initialization and model warmup..."
    $maxWait = 25
    $waited = 0
    while ($waited -lt $maxWait) {
        Start-Sleep -Seconds 1
        $waited++
        try {
            $h = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/health" -Method Get -TimeoutSec 2 -ErrorAction SilentlyContinue
            if ($h -and $h.status -eq "ok") {
                break
            }
        } catch {}
    }
}

# 6. Query and Print /api/health JSON
Write-Host "[5/5] Querying /api/health status..." -ForegroundColor Cyan
try {
    $healthJson = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/health" -Method Get -TimeoutSec 5
    Write-Host "`nSYSTEM HEALTH STATUS:" -ForegroundColor Green
    $healthJson | ConvertTo-Json -Depth 4 | Write-Host -ForegroundColor White
} catch {
    Write-Host "Could not retrieve /api/health: $_" -ForegroundColor Red
}

Write-Host "`n==================================================================" -ForegroundColor Cyan
Write-Host " Workstation is operational at: http://127.0.0.1:$Port" -ForegroundColor Green
Write-Host "==================================================================" -ForegroundColor Cyan
