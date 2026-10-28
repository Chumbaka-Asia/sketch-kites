@echo off
cd /d "%~dp0"
echo.
echo  Starting Sketch Kites hub server...
echo.

echo  Open http://localhost:8080/display.html in your browser to see the display.
echo  Share http://YOUR_IP:8080/upload.html with kiosks (find YOUR_IP with ipconfig).
echo  Press Ctrl+C to stop the server.
echo.
python scripts/server.py
