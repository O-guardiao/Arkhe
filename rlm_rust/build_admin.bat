@echo off
echo ============================================
echo    RLM Rust Build - Windows Defender Fix
echo ============================================
echo.
echo O Windows Defender esta bloqueando o build do Rust.
echo Execute este script como ADMINISTRADOR para:
echo 1. Adicionar exclusao para o diretorio de build
echo 2. Compilar o projeto Rust
echo.
echo Pressione qualquer tecla para continuar...
pause > nul

echo.
echo [1/3] Adicionando exclusao do Windows Defender...
powershell -Command "Add-MpPreference -ExclusionPath 'C:\dev\rlm_rust'" 2>nul
powershell -Command "Add-MpPreference -ExclusionPath '%USERPROFILE%\Desktop\neural\rlm-main\rlm_rust'" 2>nul

echo.
echo [2/3] Limpando cache do Cargo...
cd /d C:\dev\rlm_rust
cargo clean

echo.
echo [3/3] Compilando em modo release...
cargo build --release

if %ERRORLEVEL% EQU 0 (
    echo.
    echo ============================================
    echo    BUILD SUCESSO!
    echo ============================================
    echo.
    echo DLL gerada em:
    echo C:\dev\rlm_rust\target\release\rlm_rust.dll
    echo.
    echo Para instalar no Python:
    echo   copy target\release\rlm_rust.dll rlm_rust.pyd
    echo   pip install -e .
) else (
    echo.
    echo ============================================
    echo    BUILD FALHOU
    echo ============================================
    echo.
    echo Tente desabilitar o Windows Defender temporariamente:
    echo 1. Abra Windows Security
    echo 2. Virus ^& threat protection
    echo 3. Manage settings
    echo 4. Desabilite Real-time protection
    echo 5. Execute este script novamente
)

pause
