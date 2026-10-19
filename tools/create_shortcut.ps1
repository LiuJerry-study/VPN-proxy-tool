$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $root
$ws = New-Object -ComObject WScript.Shell
Get-ChildItem -LiteralPath $root -Filter '*.lnk' | ForEach-Object { Remove-Item -LiteralPath $_.FullName -Force }
$actualExe = (Get-ChildItem -LiteralPath $root -Filter '*.exe' | Select-Object -First 1).FullName
$actualVbs = (Get-ChildItem -LiteralPath $root -Filter '*.vbs' | Select-Object -First 1).FullName
$shortcutName = [string][char]0x8F7B + [char]0x8FDE + ' VPN.lnk'
$shortcutPath = Join-Path $root $shortcutName
$shortcut = $ws.CreateShortcut($shortcutPath)
if ($actualExe) {
    $shortcut.TargetPath = $actualExe
    $shortcut.Arguments = ''
    $shortcut.WindowStyle = 1
    $shortcut.IconLocation = $actualExe + ',0'
} elseif ($actualVbs) {
    $shortcut.TargetPath = Join-Path $env:SystemRoot 'System32\wscript.exe'
    $shortcut.Arguments = '"' + $actualVbs + '"'
    $shortcut.WindowStyle = 7
    $shortcut.IconLocation = (Join-Path $root 'assets\icon.ico') + ',0'
} else {
    throw 'Neither exe nor VBS launcher found'
}
$shortcut.WorkingDirectory = $root
$shortcut.Description = ([string][char]0x8F7B + [char]0x8FDE + ' VPN')
$shortcut.Save()
$check = $ws.CreateShortcut($shortcutPath)
Write-Output ('created=' + $shortcutPath)
Write-Output ('target=' + $check.TargetPath)
Write-Output ('target_exists=' + (Test-Path -LiteralPath $check.TargetPath))
Write-Output ('icon=' + $check.IconLocation)
Write-Output ('name_hex=' + ((([System.Text.Encoding]::UTF8.GetBytes((Get-Item -LiteralPath $shortcutPath).Name) | ForEach-Object { $_.ToString('X2') }) -join ' ')))
