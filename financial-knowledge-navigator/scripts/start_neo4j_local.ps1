$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$neo4jHome = Join-Path $repoRoot "tools\\neo4j-runtime\\neo4j-enterprise-5.24.0"
$javaHome = Join-Path $repoRoot "tools\\jdk17\\zulu17.58.21-ca-jdk17.0.15-win_x64"

if (!(Test-Path $neo4jHome)) {
    throw "Neo4j runtime not found at $neo4jHome"
}

if (!(Test-Path $javaHome)) {
    throw "Bundled JDK not found at $javaHome"
}

$env:JAVA_HOME = $javaHome
$env:NEO4J_HOME = $neo4jHome

& (Join-Path $PSScriptRoot "setup_neo4j_local.ps1")
& (Join-Path $neo4jHome "bin\\neo4j.bat") start
