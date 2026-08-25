$ErrorActionPreference = "Stop"
# Python lookup: py -3.12, then py -3.11, then python (>= 3.11).
# Starts local read-only HTTP for the review page. This is not a website.
# Keep this file ASCII except the UTF-8 BOM so Windows PowerShell 5.1 can parse it.

function Resolve-PythonCommand {
    if (Get-Command py -ErrorAction SilentlyContinue) {
        try {
            & py -3.12 -c "import sys; assert sys.version_info >= (3, 11)" 2>$null
            if ($LASTEXITCODE -eq 0) {
                return [PSCustomObject]@{ Exe = "py"; Args = @("-3.12") }
            }
        } catch {}
        try {
            & py -3.11 -c "import sys; assert sys.version_info >= (3, 11)" 2>$null
            if ($LASTEXITCODE -eq 0) {
                return [PSCustomObject]@{ Exe = "py"; Args = @("-3.11") }
            }
        } catch {}
    }
    if (Get-Command python -ErrorAction SilentlyContinue) {
        & python -c "import sys; assert sys.version_info >= (3, 11)"
        if ($LASTEXITCODE -eq 0) {
            return [PSCustomObject]@{ Exe = "python"; Args = @() }
        }
    }
    throw "Python 3.11 or 3.12 was not found. Install it and enable the Python launcher or PATH."
}

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot
$PythonCommand = Resolve-PythonCommand
Write-Host "Using $($PythonCommand.Exe) $($PythonCommand.Args -join ' ')"

$DatabasePath = Join-Path $ProjectRoot "var\jingwei-demo.sqlite3"
if (-not (Test-Path $DatabasePath)) {
    throw "Demo database not found at $DatabasePath. Run scripts\demo_windows.ps1 first."
}

$BannerArgs = @()
$BannerArgs += $PythonCommand.Args
$BannerArgs += @(Join-Path $PSScriptRoot "print_windows_banner.py")
$BannerArgs += @("serve")
& $PythonCommand.Exe @BannerArgs
if ($LASTEXITCODE -ne 0) {
    throw "Failed to print the serve banner."
}

& $PythonCommand.Exe @($PythonCommand.Args) -m app.cli --db $DatabasePath serve --host 127.0.0.1 --port 8000 --open "http://127.0.0.1:8000/"
