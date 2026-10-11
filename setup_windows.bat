@echo off
rem 初回だけ実行する。Python 3.11 以降と FFmpeg が必要。
chcp 65001 > nul
cd /d %~dp0
where ffmpeg > nul 2>&1 || echo [注意] ffmpeg が見つかりません。FFmpeg を入れて PATH に追加するか、環境変数 VFC_FFMPEG_DIR を設定してください。
py -3.11 -m venv .venv || python -m venv .venv
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
echo.
echo 準備ができました。run_companion.bat で起動します。
pause
