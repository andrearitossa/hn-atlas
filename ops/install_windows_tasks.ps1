# Run in Windows PowerShell as the Windows user who owns this WSL distro.
# The tasks start WSL at logon and at the scheduled times; WSL systemd timers
# alone cannot start a stopped WSL distro.
param(
    [Parameter(Mandatory = $true)][string]$Distro,
    [Parameter(Mandatory = $true)][string]$LinuxUser
)
$ErrorActionPreference = 'Stop'
if ($Distro -notmatch '^[A-Za-z0-9_.-]+$' -or $LinuxUser -notmatch '^[A-Za-z0-9_.-]+$') {
    throw 'Distro and LinuxUser must contain only letters, digits, underscore, dot, or dash.'
}
if ((Get-TimeZone).Id -ne 'W. Europe Standard Time') {
    throw 'Set the Windows time zone to Stockholm (W. Europe Standard Time) before installing these tasks.'
}
$principal = New-ScheduledTaskPrincipal -UserId ([Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 12) -MultipleInstances IgnoreNew
$dailyAction = New-ScheduledTaskAction -Execute 'wsl.exe' -Argument "-d $Distro -u $LinuxUser --exec systemctl --user start hackeratlas-daily.service"
$weeklyAction = New-ScheduledTaskAction -Execute 'wsl.exe' -Argument "-d $Distro -u $LinuxUser --exec systemctl --user start hackeratlas-weekly.service"
$dailyTriggers = @(
    (New-ScheduledTaskTrigger -AtLogOn -User $principal.UserId),
    (New-ScheduledTaskTrigger -Daily -At '06:30')
)
$weeklySettings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 12) -MultipleInstances IgnoreNew
$weeklyTriggers = @(
    (New-ScheduledTaskTrigger -AtLogOn -User $principal.UserId),
    (New-ScheduledTaskTrigger -Weekly -DaysOfWeek Sunday -At '18:00')
)
Register-ScheduledTask -TaskName 'HackerAtlas-Daily-WSL' -Action $dailyAction -Trigger $dailyTriggers -Principal $principal -Settings $settings -Force | Out-Null
Register-ScheduledTask -TaskName 'HackerAtlas-Weekly-WSL' -Action $weeklyAction -Trigger $weeklyTriggers -Principal $principal -Settings $weeklySettings -Force | Out-Null
Get-ScheduledTask -TaskName 'HackerAtlas-*-WSL' | Select-Object TaskName,State
