param([string]$GradlePath = '', [switch]$Offline)
$ErrorActionPreference = 'Stop'
$projectRoot = $PSScriptRoot
$tempRoot = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath())
$workDir = Join-Path $tempRoot ('RainClassroomAndroid-' + [guid]::NewGuid().ToString('N'))
try {
    if ($workDir -match '[^\x00-\x7F]') {
        throw 'The temporary path contains non-ASCII characters. Set TEMP to an ASCII-only path and retry.'
    }
    if (-not $env:ANDROID_HOME -and -not $env:ANDROID_SDK_ROOT) {
        $defaultSdk = Join-Path $env:LOCALAPPDATA 'Android\Sdk'
        if (Test-Path -LiteralPath $defaultSdk) { $env:ANDROID_HOME = $defaultSdk }
        else { throw 'Android SDK not found. Install Android Studio and SDK 35, or set ANDROID_HOME.' }
    }
    New-Item -ItemType Directory -Force -Path (Join-Path $workDir 'app') | Out-Null
    foreach ($file in @('settings.gradle','build.gradle','gradle.properties','gradlew','gradlew.bat')) {
        Copy-Item -LiteralPath (Join-Path $projectRoot $file) -Destination $workDir
    }
    Copy-Item -LiteralPath (Join-Path $projectRoot 'gradle') -Destination $workDir -Recurse
    Copy-Item -LiteralPath (Join-Path $projectRoot 'app\build.gradle') -Destination (Join-Path $workDir 'app')
    Copy-Item -LiteralPath (Join-Path $projectRoot 'app\src') -Destination (Join-Path $workDir 'app') -Recurse
    Push-Location -LiteralPath $workDir
    try {
        $gradleArgs = @('--no-daemon', ':app:assembleDebug')
        if ($Offline) { $gradleArgs += '--offline' }
        if ($GradlePath) { & $GradlePath @gradleArgs }
        else { & (Join-Path $workDir 'gradlew.bat') @gradleArgs }
        if ($LASTEXITCODE -ne 0) { throw 'Android build failed. Review the output above.' }
    } finally { Pop-Location }
    $output = Join-Path $projectRoot 'app\build\outputs\apk\debug'
    New-Item -ItemType Directory -Force -Path $output | Out-Null
    Copy-Item -LiteralPath (Join-Path $workDir 'app\build\outputs\apk\debug\app-debug.apk') -Destination (Join-Path $output 'app-debug.apk') -Force
    Write-Host "APK ready: $(Join-Path $output 'app-debug.apk')"
} catch {
    Write-Host $_ -ForegroundColor Red
    exit 1
} finally {
    $resolved = [System.IO.Path]::GetFullPath($workDir)
    if ($resolved.StartsWith($tempRoot, [System.StringComparison]::OrdinalIgnoreCase) -and
        [System.IO.Path]::GetFileName($resolved).StartsWith('RainClassroomAndroid-') -and
        (Test-Path -LiteralPath $resolved)) {
        Remove-Item -LiteralPath $resolved -Recurse -Force
    }
}
