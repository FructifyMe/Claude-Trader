' ============================================================
' Runs the trader bot silently in the background (no console window).
' Logs all output to data\startup.log so crashes aren't silent.
' ============================================================
Set fso = CreateObject("Scripting.FileSystemObject")
Set WshShell = CreateObject("WScript.Shell")

Dim botDir
botDir = fso.GetParentFolderName(WScript.ScriptFullName)
WshShell.CurrentDirectory = botDir

Dim logFile
logFile = botDir & "\data\startup.log"

WshShell.Run "cmd /c run_trader.bat --background > """ & logFile & """ 2>&1", 0, False
