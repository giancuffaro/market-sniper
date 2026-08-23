' MARKET SNIPER - start with NO window at all.
'
' Runs the normal launcher completely hidden. Everything lives in the system
' tray: right-click the green crosshair to open either app, view the sync log,
' or quit. Nothing on the taskbar, nothing on the desktop.
'
' The regular "START MARKET SNIPER.bat" still works if you want to watch the
' console - use that one when something is wrong and you need to see why.
'
' To stop it: tray icon -> Quit Market Sniper, or run "STOP EVERYTHING.bat".

Option Explicit

Dim shell, fso, here, launcher, f, found

Set shell = CreateObject("WScript.Shell")
Set fso   = CreateObject("Scripting.FileSystemObject")

here = fso.GetParentFolderName(WScript.ScriptFullName)
shell.CurrentDirectory = here

' Find the launcher by its ASCII tail. Its filename starts with an emoji, and
' matching that literally across codepages is asking for trouble.
found = ""
For Each f In fso.GetFolder(here).Files
    If InStr(1, f.Name, "START MARKET SNIPER.bat", vbTextCompare) > 0 Then
        found = f.Path
    End If
Next

If found = "" Then
    MsgBox "Could not find START MARKET SNIPER.bat in:" & vbCrLf & here, _
           vbExclamation, "Market Sniper"
    WScript.Quit 1
End If

' 0 = hidden window, False = do not wait for it to finish.
shell.Run """" & found & """", 0, False
