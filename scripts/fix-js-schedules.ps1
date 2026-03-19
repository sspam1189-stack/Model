# Run this as Administrator to update JS task schedules
# Right-click -> Run with PowerShell (as Admin)

Unregister-ScheduledTask -TaskName "NBA+Full Run Daily" -Confirm:$false
Unregister-ScheduledTask -TaskName "NBA+Full Recap Daily" -Confirm:$false
Unregister-ScheduledTask -TaskName "NCAA Run Daily" -Confirm:$false

$a1 = New-ScheduledTaskAction -Execute "C:\Users\HenryVM\Desktop\Model\scripts\trigger-nba-picks.bat"
$t1 = New-ScheduledTaskTrigger -Daily -At "6:00PM"
Register-ScheduledTask -TaskName "NBA+Full Run Daily" -Action $a1 -Trigger $t1

$a2 = New-ScheduledTaskAction -Execute "C:\Users\HenryVM\Desktop\Model\scripts\trigger-nba-recap.bat"
$t2 = New-ScheduledTaskTrigger -Daily -At "10:30AM"
Register-ScheduledTask -TaskName "NBA+Full Recap Daily" -Action $a2 -Trigger $t2

$a3 = New-ScheduledTaskAction -Execute "C:\Users\HenryVM\Desktop\Model\scripts\trigger-ncaa-picks.bat"
$t3 = New-ScheduledTaskTrigger -Daily -At "10:30AM"
Register-ScheduledTask -TaskName "NCAA Run Daily" -Action $a3 -Trigger $t3

Write-Host "`nUpdated schedules:"
@("NBA+Full Run Daily", "NBA+Full Recap Daily", "NCAA Run Daily") | ForEach-Object {
    $task = Get-ScheduledTask -TaskName $_
    Write-Host "  $_ -> $($task.Triggers[0].StartBoundary)"
}
Read-Host "Press Enter to close"
