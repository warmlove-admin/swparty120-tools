@echo off
start "" /B "tools_bin\ngrok\ngrok.exe" http 8000 --config=tools_bin\ngrok_config\ngrok.yml --log=stdout > data\ngrok_stdout.log 2>&1
