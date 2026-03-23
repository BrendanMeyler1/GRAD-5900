$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$neo4jHome = Join-Path $repoRoot "tools\\neo4j-runtime\\neo4j-enterprise-5.24.0"
$javaHome = Join-Path $repoRoot "tools\\jdk17\\zulu17.58.21-ca-jdk17.0.15-win_x64"
$envPath = Join-Path $repoRoot ".env"
$stateDir = Join-Path $repoRoot "data\\neo4j"

if (!(Test-Path $neo4jHome)) {
    throw "Neo4j runtime not found at $neo4jHome"
}

if (!(Test-Path $javaHome)) {
    throw "Bundled JDK not found at $javaHome"
}

$env:JAVA_HOME = $javaHome
$env:NEO4J_HOME = $neo4jHome

if (!(Test-Path $stateDir)) {
    New-Item -ItemType Directory -Path $stateDir | Out-Null
}

$passwordLine = Get-Content $envPath | Where-Object { $_ -match '^NEO4J_PASSWORD=' } | Select-Object -First 1
if (-not $passwordLine) {
    throw "NEO4J_PASSWORD is missing from $envPath"
}

$password = $passwordLine -replace '^NEO4J_PASSWORD=', ''
if ([string]::IsNullOrWhiteSpace($password)) {
    throw "NEO4J_PASSWORD is blank in $envPath"
}

$authIni = Join-Path $neo4jHome "data\\dbms\\auth.ini"
if (Test-Path $authIni) {
    Write-Host "Neo4j auth is already initialized."
}
else {
    & (Join-Path $neo4jHome "bin\\neo4j-admin.bat") dbms set-initial-password $password
    Write-Host "Neo4j initial password configured."
}

& (Join-Path $neo4jHome "bin\\neo4j-admin.bat") server license --accept-evaluation
Write-Host "Neo4j evaluation license accepted."
