param(
    [string]$Destination = (Join-Path (Get-Location) "Forge-Navi-Community")
)

$ErrorActionPreference = "Stop"
$BaseUrl = "https://shdwolf579.github.io/community/forge-navi"

$Files = @(
    "README.md",
    "ARCHITECTURE.md",
    "forge_navi_community.py",
    "forge_navi_desktop.py",
    "token_navi_community.py",
    "Launch Forge-Navi.cmd",
    "demo/token-requirements.json",
    "demo/custom/cards/d/demo_recruiter.txt",
    "demo/custom/tokens/demo_scout.txt"
)

Write-Host "Forge-Navi + Token-Navi Community v0.2" -ForegroundColor Cyan
Write-Host "Installing to: $Destination"

foreach ($Relative in $Files) {
    $Target = Join-Path $Destination $Relative
    $Directory = Split-Path $Target -Parent
    New-Item -ItemType Directory -Force -Path $Directory | Out-Null
    $Url = "$BaseUrl/$($Relative -replace '\\','/')"
    Write-Host "  downloading $Relative"
    Invoke-WebRequest -UseBasicParsing -Uri $Url -OutFile $Target
}

try {
    $Desktop = [Environment]::GetFolderPath("Desktop")
    $ShortcutPath = Join-Path $Desktop "Forge-Navi Community.lnk"
    $WshShell = New-Object -ComObject WScript.Shell
    $Shortcut = $WshShell.CreateShortcut($ShortcutPath)
    $Shortcut.TargetPath = Join-Path $Destination "Launch Forge-Navi.cmd"
    $Shortcut.WorkingDirectory = $Destination
    $Shortcut.Description = "Forge-Navi Community"
    $Shortcut.Save()
    Write-Host "Desktop shortcut created: $ShortcutPath"
}
catch {
    Write-Warning "Could not create desktop shortcut. Use 'Launch Forge-Navi.cmd' in the install folder."
}

Write-Host ""
Write-Host "Install complete." -ForegroundColor Green
Write-Host "Launch:"
Write-Host "  Double-click Forge-Navi Community on your desktop"
Write-Host "  or run Launch Forge-Navi.cmd"
Write-Host ""
Write-Host "CLI demo:"
Write-Host ('  cd "{0}"' -f $Destination)
Write-Host "  py -3 forge_navi_community.py audit demo/custom --report demo/audit-report.json"
Write-Host "  py -3 forge_navi_community.py handoff demo/custom demo/generated-token-requirements.json"
Write-Host "  py -3 token_navi_community.py validate demo/generated-token-requirements.json"
Write-Host "  py -3 forge_navi_community.py package demo/custom dist/demo-forge-package.zip"
