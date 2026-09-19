$ErrorActionPreference = "Stop"

$root = $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"
$stdoutLog = Join-Path $root "bot.stdout.log"
$stderrLog = Join-Path $root "bot.stderr.log"
$watchdogLog = Join-Path $root "bot.watchdog.log"

if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "Python della virtualenv non trovato: $python"
}

while ($true) {
    $started = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    try {
        $process = Start-Process -FilePath $python `
            -ArgumentList @("-m", "translator_bot") `
            -WorkingDirectory $root `
            -RedirectStandardOutput $stdoutLog `
            -RedirectStandardError $stderrLog `
            -PassThru -Wait
        $exitCode = $process.ExitCode
        Add-Content -LiteralPath $watchdogLog `
            -Value "$started bot terminato con codice $exitCode; riavvio tra 5 secondi."
    }
    catch {
        Add-Content -LiteralPath $watchdogLog `
            -Value "$started watchdog: $($_.Exception.GetType().Name); riavvio tra 5 secondi."
    }
    Start-Sleep -Seconds 5
}
