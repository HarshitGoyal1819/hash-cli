# =============================================================================
# add_to_path.ps1
# Adds the hash-cli install directory to the system PATH on Windows.
# Run as Administrator after a manual / silent install.
#
#   Usage:
#     powershell -ExecutionPolicy Bypass -File add_to_path.ps1 [-InstallDir "C:\hash-cli"]
# =============================================================================

param(
    [string]$InstallDir = "$env:ProgramFiles\hash-cli"
)

$key = "HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager\Environment"
$currentPath = (Get-ItemProperty -Path $key -Name Path).Path

if ($currentPath -like "*$InstallDir*") {
    Write-Host "hash-cli is already in PATH: $InstallDir"
    exit 0
}

$newPath = $currentPath.TrimEnd(";") + ";$InstallDir"
Set-ItemProperty -Path $key -Name Path -Value $newPath

# Broadcast the change so new terminals pick it up immediately
$signature = @"
[DllImport("user32.dll", SetLastError = true, CharSet = CharSet.Auto)]
public static extern IntPtr SendMessageTimeout(
    IntPtr hWnd, uint Msg, UIntPtr wParam, string lParam,
    uint fuFlags, uint uTimeout, out UIntPtr lpdwResult);
"@
$type = Add-Type -MemberDefinition $signature -Name WinEnv -Namespace Win32 -PassThru
$result = [UIntPtr]::Zero
$HWND_BROADCAST  = [IntPtr]0xffff
$WM_SETTINGCHANGE = 0x001A
$type::SendMessageTimeout($HWND_BROADCAST, $WM_SETTINGCHANGE, [UIntPtr]::Zero,
    "Environment", 2, 5000, [ref]$result) | Out-Null

Write-Host "✓  Added to system PATH: $InstallDir"
Write-Host "   Open a new CMD or PowerShell window and type:  hash-cli"
