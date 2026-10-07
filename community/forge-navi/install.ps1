param(
    [string]$Destination = (Join-Path $env:LOCALAPPDATA "Forge-Navi-Community")
)

$ErrorActionPreference = "Stop"
$BaseUrl = "https://shdwolf579.github.io/community/forge-navi"
$ExeUrl = "$BaseUrl/dist/Forge-Navi-Community.exe"
$HashUrl = "$BaseUrl/dist/SHA256.txt"
$ExePath = Join-Path $Destination "Forge-Navi-Community.exe"
$HashPath = Join-Path $Destination "SHA256.txt"

Write-Host "Forge-Navi Community v0.2 Windows Installer" -ForegroundColor Cyan
Write-Host "Installing to: $Destination"

New-Item -ItemType Directory -Force -Path $Destination | Out-Null

Write-Host "  downloading standalone application"
Invoke-WebRequest -UseBasicParsing -Uri $ExeUrl -OutFile $ExePath

Write-Host "  downloading integrity hash"
Invoke-WebRequest -UseBasicParsing -Uri $HashUrl -OutFile $HashPath

$Expected = ((Get-Content $HashPath -Raw).Trim() -split '\s+')[0].ToLower()
$Actual = (Get-FileHash $ExePath -Algorithm SHA256).Hash.ToLower()

if ($Expected -ne $Actual) {
    Remove-Item $ExePath -Force -ErrorAction SilentlyContinue
    throw "Forge-Navi integrity verification failed. Expected $Expected but got $Actual."
}

Write-Host "  SHA-256 verified" -ForegroundColor Green

try {
    $Desktop = [Environment]::GetFolderPath("Desktop")
    $ShortcutPath = Join-Path $Desktop "Forge-Navi Community.lnk"
    $WshShell = New-Object -ComObject WScript.Shell
    $Shortcut = $WshShell.CreateShortcut($ShortcutPath)
    $Shortcut.TargetPath = $ExePath
    $Shortcut.WorkingDirectory = $Destination
    $Shortcut.Description = "Forge-Navi Community"
    $Shortcut.Save()
    Write-Host "  desktop shortcut created"
}
catch {
    Write-Warning "Could not create the desktop shortcut. Launch the EXE directly from $Destination."
}

Write-Host ""
Write-Host "Install complete." -ForegroundColor Green
Write-Host "Python is NOT required for the desktop app."
Write-Host "Launch Forge-Navi Community from the desktop shortcut."
Write-Host ""
Write-Host "Installed executable:"
Write-Host "  $ExePath"
