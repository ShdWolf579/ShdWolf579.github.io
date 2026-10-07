param(
    [string]$Destination = (Join-Path (Get-Location) "Forge-Navi-Community")
)

$ErrorActionPreference = "Stop"
$BaseUrl = "https://shdwolf579.github.io/community/forge-navi"

$Files = @(
    "README.md",
    "forge_navi_community.py",
    "token_navi_community.py",\n    "forge_navi_desktop.py",\n    "Launch Forge-Navi.cmd",\n    "ARCHITECTURE.md",
    "demo/token-requirements.json",
    "demo/custom/cards/d/demo_recruiter.txt",
    "demo/custom/tokens/demo_scout.txt"
)

Write-Host "Forge-Navi + Token-Navi Community Demo" -ForegroundColor Cyan
Write-Host "Installing to: $Destination"

foreach ($Relative in $Files) {
    $Target = Join-Path $Destination $Relative
    $Directory = Split-Path $Target -Parent
    New-Item -ItemType Directory -Force -Path $Directory | Out-Null
    $Url = "$BaseUrl/$($Relative -replace '\\','/')"
    Write-Host "  downloading $Relative"
    Invoke-WebRequest -UseBasicParsing -Uri $Url -OutFile $Target
}

Write-Host ""
Write-Host "Install complete." -ForegroundColor Green
Write-Host "Launch:"\nWrite-Host "  Double-click Forge-Navi Community on your desktop"\nWrite-Host "  or run Launch Forge-Navi.cmd"\nWrite-Host ""\nWrite-Host "CLI demo:"
Write-Host ('  cd "{0}"' -f $Destination)
Write-Host "  py -3 forge_navi_community.py audit demo/custom --report demo/audit-report.json"
Write-Host "  py -3 forge_navi_community.py handoff demo/custom demo/generated-token-requirements.json"
Write-Host "  py -3 token_navi_community.py validate demo/generated-token-requirements.json"
Write-Host "  py -3 forge_navi_community.py package demo/custom dist/demo-forge-package.zip"
