# Regenerar CSS de Tailwind (solo si cambias clases en plantillas HTML)
# Requiere tailwindcss.exe en la raíz del proyecto (descarga automática la primera vez).

$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

if (-not (Test-Path 'tailwindcss.exe')) {
    Write-Host 'Descargando Tailwind standalone CLI...'
    Invoke-WebRequest `
        -Uri 'https://github.com/tailwindlabs/tailwindcss/releases/download/v3.4.17/tailwindcss-windows-x64.exe' `
        -OutFile 'tailwindcss.exe'
}

.\tailwindcss.exe -c tailwind.config.js -i ./assets/tailwind-input.css -o ./login/static/css/tailwind.css --minify
python scripts/check_tailwind_css.py
Write-Host 'Listo: login/static/css/tailwind.css actualizado.'
