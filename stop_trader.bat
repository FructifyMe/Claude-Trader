@echo off 
taskkill /f /im python.exe /fi "WINDOWTITLE eq *auto-trader*" >nul 2>&1 
taskkill /f /fi "MODULES eq src.main" >nul 2>&1 
