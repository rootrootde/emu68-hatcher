@echo off
rem launch emu68hatcher from this source checkout, using a windows-only venv
rem (.venvwin) so a macos/linux .venv in the same dir stays untouched
pushd "%~dp0"
if errorlevel 1 (
    echo Could not open the source checkout.
    pause
    exit /b 1
)

rem a venv without pip is the leftover of a create that failed at the
rem ensurepip step - rebuild it
if exist ".venvwin\Scripts\python.exe" if not exist ".venvwin\Scripts\pip.exe" (
    echo Found a broken .venvwin - rebuilding
    rmdir /s /q ".venvwin"
)

if not exist ".venvwin\Scripts\python.exe" (
    echo First run - creating windows venv at .venvwin
    where python >nul 2>nul
    if errorlevel 1 ( py -m venv .venvwin ) else ( python -m venv .venvwin )
)

if not exist ".venvwin\Scripts\python.exe" (
    echo Could not create .venvwin - is python installed?
    goto :fail
)

rem reinstall whenever pyproject.toml differs from the copy taken at the
rem last install, so new dependencies reach an existing venv
if not exist ".venvwin\Scripts\emu68hatcher.exe" goto :install
fc /b "pyproject.toml" ".venvwin\pyproject.installed" >nul 2>nul
if errorlevel 1 goto :install
goto :run

:install
echo Installing emu68hatcher into .venvwin
".venvwin\Scripts\python.exe" -m pip install -e .
if errorlevel 1 (
    echo pip install failed.
    goto :fail
)
copy /y "pyproject.toml" ".venvwin\pyproject.installed" >nul

:run

".venvwin\Scripts\python.exe" -m emu68hatcher
if errorlevel 1 (
    echo.
    echo emu68hatcher exited with an error.
    goto :fail
)

popd
exit /b 0

:fail
pause
popd
exit /b 1
