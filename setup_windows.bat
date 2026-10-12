@echo off
rem 初回だけ実行する。Python 3.11 以降と FFmpeg が必要。もう一度実行しても大丈夫。
chcp 65001 > nul
cd /d %~dp0
where ffmpeg > nul 2>&1 || echo [注意] ffmpeg が見つかりません。FFmpeg を入れて PATH に追加するか、環境変数 VFC_FFMPEG_DIR を設定してください。
if not exist .venv\Scripts\python.exe (
  py -3.11 -m venv .venv || python -m venv .venv
)
if not exist .venv\Scripts\python.exe goto fail_venv
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
if errorlevel 1 goto fail_pip
echo.
echo 準備ができました。run_companion.bat で起動します。
pause
exit /b 0

:fail_venv
echo.
echo [エラー] Python の環境を作れませんでした。Python 3.11 以降を入れるときに「Add to PATH」にチェックしてください。
pause
exit /b 1

:fail_pip
echo.
echo [エラー] 必要なライブラリを入れられませんでした。上に出たメッセージをそのまま送ってください。
pause
exit /b 1
