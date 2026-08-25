$ErrorActionPreference = "Stop"
# Python lookup: py -3.12, then py -3.11, then python (>= 3.11).
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
    throw "Python 3.11 or 3.12 was not found."
}

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot
$PythonCommand = Resolve-PythonCommand
Write-Host "Using $($PythonCommand.Exe) $($PythonCommand.Args -join ' ')"

$DatabaseDirectory = Join-Path $ProjectRoot "var"
$DatabasePath = Join-Path $DatabaseDirectory "jingwei-demo.sqlite3"
New-Item -ItemType Directory -Force -Path $DatabaseDirectory | Out-Null
if (Test-Path $DatabasePath) {
    throw "Demo database already exists at $DatabasePath. Move or rename it before rerunning; the script will not overwrite it."
}

& $PythonCommand.Exe @($PythonCommand.Args) -m app.cli --db $DatabasePath import-sample samples/synthetic_case/consulting_fixture_v0.1.json
if ($LASTEXITCODE -ne 0) { throw "Sample import failed." }

$ExportPath = Join-Path $DatabaseDirectory "synthetic-internal.docx"
& $PythonCommand.Exe @($PythonCommand.Args) -m app.cli --db $DatabasePath export P-DEMO-001 word --out $ExportPath
if ($LASTEXITCODE -ne 0) { throw "Word export failed." }

$BannerArgs = @()
$BannerArgs += $PythonCommand.Args
$BannerArgs += @(Join-Path $PSScriptRoot "print_windows_banner.py")
$BannerArgs += @("demo", $DatabasePath, $ExportPath)
& $PythonCommand.Exe @BannerArgs
if ($LASTEXITCODE -ne 0) {
    throw "Failed to print the demo banner."
}
