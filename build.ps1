$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$buildPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $buildPython)) { python -m venv .venv }
& $buildPython -X utf8 -m pip install -r requirements-lock.txt
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
& $buildPython -X utf8 prepare_release.py
if ($LASTEXITCODE -ne 0) { throw 'Release preparation failed.' }
& $buildPython -X utf8 -m pytest test_local.py test_desktop.py -q
if ($LASTEXITCODE -ne 0) { throw 'Tests failed.' }
& $buildPython -X utf8 -m PyInstaller --noconfirm MosaicDesk.spec
if ($LASTEXITCODE -ne 0) { throw 'Build failed.' }
$uiCheck = Start-Process -FilePath (Join-Path $PSScriptRoot 'dist\MosaicDesk.exe') -ArgumentList '--check-ui' -WindowStyle Hidden -Wait -PassThru
if ($uiCheck.ExitCode -ne 0) { throw 'Packaged UI check failed.' }
Write-Output 'Completed: dist\MosaicDesk.exe'
