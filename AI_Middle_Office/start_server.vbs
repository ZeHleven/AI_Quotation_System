Dim shell
Set shell = WScript.CreateObject("WScript.Shell")
shell.CurrentDirectory = "C:\\Users\\12521\\Desktop\\Clear_test\\AI_Middle_Office"
shell.Run """C:\Users\12521\miniconda3\python.exe"" -m uvicorn app.main:app --port 9000", 0, False
