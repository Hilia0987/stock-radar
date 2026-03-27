@echo off
chcp 65001 >nul
set PYTHON=C:\Users\iwthr\AppData\Local\Programs\Python\Python314\python.exe
set DIR=%~dp0

echo.
echo ========================================
echo   StockRadar ダッシュボード
echo ========================================
echo.

:: 最新の portfolio.json を取得
echo [1/5] GitHubから最新データを取得中...
cd /d "%DIR%"
git pull
echo.

:: 依存パッケージ確認（初回のみインストール）
%PYTHON% -c "import yfinance, pandas, rich, plotext" 2>nul
if errorlevel 1 (
    echo [依存パッケージをインストール中...]
    %PYTHON% -m pip install yfinance pandas pytz pyyaml rich plotext apscheduler click -q
    echo.
)

:: ウォッチリスト表示
echo [2/5] ウォッチリスト ^& シグナル...
echo ----------------------------------------
%PYTHON% "%DIR%main.py" scan
echo.

:: ポートフォリオ表示
echo [3/5] ポートフォリオ損益...
echo ----------------------------------------
%PYTHON% "%DIR%main.py" portfolio
echo.

:: チャート表示（銘柄ごと）
echo [4/5] チャート表示...
echo ----------------------------------------
for %%T in (7203.T 6758.T 9984.T NVDA INTC SOFI) do (
    echo.
    echo  -- %%T --
    %PYTHON% "%DIR%main.py" chart %%T --period 3mo
)

:: 最新レポート（schedule_agent.py の出力）
echo.
echo [5/5] 最新売買レポート...
echo ----------------------------------------
%PYTHON% "%DIR%schedule_agent.py"

echo.
echo ========================================
echo   完了
echo ========================================
pause
