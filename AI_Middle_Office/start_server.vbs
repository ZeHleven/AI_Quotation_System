Dim shell
Dim fso
Dim workDir

Set shell = WScript.CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
workDir = fso.GetParentFolderName(WScript.ScriptFullName)

shell.CurrentDirectory = workDir
shell.Run "powershell.exe -NoProfile -ExecutionPolicy Bypass -File """ & workDir & "\start_watchdog.ps1"" -RetryIntervalSeconds 180 -MaxMinutes 60", 0, False
