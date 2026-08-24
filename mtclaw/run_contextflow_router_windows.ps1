param(
    [string]$SourceConfig = "$env:USERPROFILE\.function-router\config.json",
    [string]$Python = "$env:LOCALAPPDATA\Programs\Python\Python314\python.exe",
    [string]$Bash = "D:\Javaee\Git\bin\bash.exe"
)

$ErrorActionPreference = "Stop"

$scriptDirectory = Split-Path -Parent $MyInvocation.MyCommand.Path
$contextFlowHome = (Resolve-Path (Join-Path $scriptDirectory "..")).Path
$contextFlowConfig = Join-Path $scriptDirectory "config.contextflow.json"
$functionRouterSource = Join-Path (Split-Path -Parent $contextFlowHome) "MTClaw"

foreach ($requiredPath in @($SourceConfig, $Python, $Bash, $contextFlowConfig, $functionRouterSource)) {
    if (-not (Test-Path -LiteralPath $requiredPath)) {
        throw "Required path not found: $requiredPath"
    }
}

$source = Get-Content -Raw -LiteralPath $SourceConfig | ConvertFrom-Json

function Get-RequiredConfigValue {
    param(
        [object]$Value,
        [string]$Name
    )

    if (-not ($Value -is [string]) -or [string]::IsNullOrWhiteSpace($Value)) {
        throw "Missing required value in source config: $Name"
    }
    return $Value
}

$env:CONTEXTFLOW_HOME = $contextFlowHome
$env:CONTEXTFLOW_PYTHON = $Python.Replace("\", "/")
$env:CONTEXTFLOW_ROUTING_BASE_URL = Get-RequiredConfigValue $source.routing.base_url "routing.base_url"
$env:CONTEXTFLOW_ROUTING_MODEL = Get-RequiredConfigValue $source.routing.model "routing.model"
$env:CONTEXTFLOW_ROUTING_API_KEY = Get-RequiredConfigValue $source.routing.api_key "routing.api_key"
$env:CONTEXTFLOW_UPSTREAM_BASE_URL = Get-RequiredConfigValue $source.upstream.base_url "upstream.base_url"
$env:CONTEXTFLOW_UPSTREAM_MODEL = Get-RequiredConfigValue $source.upstream.model "upstream.model"
$env:CONTEXTFLOW_UPSTREAM_API_KEY = Get-RequiredConfigValue $source.upstream.api_key "upstream.api_key"
$env:FR_SHELL_BIN = $Bash
$env:FR_DISABLE_BUILTIN_TOOLS = "1"
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONPATH = $functionRouterSource

Write-Host "Starting ContextFlow Router on http://127.0.0.1:18790 with 22 dedicated tools."
& $Python -m function_router.server --config $contextFlowConfig
