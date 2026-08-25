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
    throw "Python 3.11 or 3.12 was not found. Install it and enable the Python launcher or PATH."
}

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot
$PythonCommand = Resolve-PythonCommand
Write-Host "Using $($PythonCommand.Exe) $($PythonCommand.Args -join ' ')"

& $PythonCommand.Exe @($PythonCommand.Args) -m unittest discover -s tests -v
if ($LASTEXITCODE -ne 0) {
    throw "Automated tests failed."
}

Write-Host "Windows handoff verification passed."
