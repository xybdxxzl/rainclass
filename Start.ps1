param([switch]$Check)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
try {
    $venvPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $venvPython)) {
        Write-Host 'Preparing project Python environment...'
        $candidates = @()
        foreach ($name in @('python', 'python3')) {
            $command = Get-Command $name -ErrorAction SilentlyContinue
            if ($command) { $candidates += $command.Source }
        }
        $launcher = Get-Command py -ErrorAction SilentlyContinue
        if ($launcher) {
            foreach ($version in @('-3.12', '-3.11', '-3.10')) {
                try {
                    $found = & $launcher.Source $version -c 'import sys; print(sys.executable)' 2>$null
                    if ($LASTEXITCODE -eq 0) { $candidates += $found }
                } catch { continue }
            }
        }
        foreach ($root in @('HKCU:\Software\Python\PythonCore', 'HKLM:\Software\Python\PythonCore')) {
            if (Test-Path $root) {
                foreach ($item in Get-ChildItem $root) {
                    $install = Get-Item (Join-Path $item.PSPath 'InstallPath') -ErrorAction SilentlyContinue
                    if ($install) { $candidates += (Join-Path $install.GetValue('') 'python.exe') }
                }
            }
        }
        # Codex workspace runtime is a fallback when Python is not on PATH.
        $candidates += (Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe')
        $selected = $null
        foreach ($candidate in ($candidates | Select-Object -Unique)) {
            if (-not (Test-Path -LiteralPath $candidate)) { continue }
            & $candidate -c 'import sys; sys.exit(0 if (3,9) <= sys.version_info[:2] <= (3,12) else 1)' 2>$null
            if ($LASTEXITCODE -eq 0) { $selected = $candidate; break }
        }
        if (-not $selected) { throw 'Please install Python 3.10, 3.11 or 3.12, then run this launcher again.' }
        & $selected -m venv (Join-Path $PSScriptRoot '.venv')
        if ($LASTEXITCODE -ne 0) { throw 'Unable to create .venv.' }
    }
    $requirements = Join-Path $PSScriptRoot 'requirements.txt'
    $stamp = Join-Path $PSScriptRoot '.venv\requirements.sha256'
    $hash = (Get-FileHash -LiteralPath $requirements -Algorithm SHA256).Hash
    if (-not (Test-Path -LiteralPath $stamp) -or (Get-Content -LiteralPath $stamp -Raw).Trim() -ne $hash) {
        Write-Host 'Installing project dependencies (first launch requires Internet)...'
        & $venvPython -m pip install --disable-pip-version-check -r $requirements
        if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed. Check your network and retry.' }
        [IO.File]::WriteAllText($stamp, $hash)
    }
    if ($Check) {
        & $venvPython -B (Join-Path $PSScriptRoot 'check_startup.py')
    } else {
        & $venvPython (Join-Path $PSScriptRoot 'RainClassroomAssistant.py')
    }
    exit $LASTEXITCODE
} catch {
    Write-Host $_ -ForegroundColor Red
    exit 1
}
