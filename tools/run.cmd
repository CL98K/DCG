@echo off
setlocal
rem DCG Schema Builder 启动器：在仓库根启动 tools/app.py，参数原样透传（如 --port 8790）
pushd "%~dp0.." || exit /b 1
python -X utf8 tools\app.py %*
set ERR=%ERRORLEVEL%
popd
exit /b %ERR%
