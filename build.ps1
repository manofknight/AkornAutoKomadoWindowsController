# Run on Windows 11 with Python 3.11+ and Inno Setup 6 installed.
python -m pip install -r requirements.txt
pyinstaller --onefile --windowed --name AkornController app.py
& "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe" installer.iss
Write-Host "Installer: installer\AkornControllerSetup.exe"
