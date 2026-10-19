Option Explicit

Dim fso, shell, baseDir, pythonExe, commandLine
Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")

baseDir = fso.GetParentFolderName(WScript.ScriptFullName)
shell.CurrentDirectory = baseDir

pythonExe = FindExecutable("pythonw.exe")
If pythonExe = "" Then pythonExe = FindExecutable("python.exe")
If pythonExe = "" Then
    MsgBox "Python 3 was not found. Please install Python 3.10 or newer.", 16, "QingLian VPN"
    WScript.Quit 1
End If

commandLine = Chr(34) & pythonExe & Chr(34) & " " & Chr(34) & baseDir & "\app.py" & Chr(34)
shell.Run commandLine, 0, False

Function FindExecutable(fileName)
    Dim exec, line
    FindExecutable = ""
    On Error Resume Next
    Set exec = shell.Exec("where " & fileName)
    If Err.Number <> 0 Then
        Err.Clear
        Exit Function
    End If
    line = exec.StdOut.ReadLine
    If Err.Number = 0 Then FindExecutable = Trim(line)
    Err.Clear
    On Error GoTo 0
End Function
