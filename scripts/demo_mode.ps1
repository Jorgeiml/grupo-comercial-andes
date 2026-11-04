# Switches how the agent answers in the demo:
#   vivo      -> the LLM answers every time (default for the presentation: it shows it is real AI)
#   respaldo  -> repeated questions replay from the response cache (plan B if the network or OpenAI is slow)
# Only the LLM_CACHE_ENABLED line of .env changes; nothing else in .env is read aloud or touched.
#   powershell -ExecutionPolicy Bypass -File scripts/demo_mode.ps1 vivo
param([Parameter(Mandatory = $true)][ValidateSet("vivo", "respaldo")][string]$Mode)

$value = if ($Mode -eq "vivo") { "false" } else { "true" }
$envFile = Join-Path $PSScriptRoot "..\.env"
$lines = @(Get-Content $envFile -Encoding UTF8)
if ($lines -match "^LLM_CACHE_ENABLED=") {
    $lines = $lines -replace "^LLM_CACHE_ENABLED=.*", "LLM_CACHE_ENABLED=$value"
} else {
    $lines += "LLM_CACHE_ENABLED=$value"
}
# UTF-8 without BOM, so docker compose reads the first variable correctly
[System.IO.File]::WriteAllLines((Resolve-Path $envFile), $lines, (New-Object System.Text.UTF8Encoding $false))

Remove-Item Env:LLM_CACHE_ENABLED -ErrorAction SilentlyContinue  # a shell variable would override .env
docker compose up -d agent-api | Out-Null
$active = docker compose exec -T agent-api printenv LLM_CACHE_ENABLED
if ($Mode -eq "vivo") {
    Write-Output "Modo EN VIVO (cache=$active): la IA responde cada vez. Tarda unos segundos y muestra su costo real."
} else {
    Write-Output "Modo RESPALDO (cache=$active): las preguntas ya ensayadas salen de la cache, al instante y sin costo."
}
