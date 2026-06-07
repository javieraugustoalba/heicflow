# set-azure-appsettings.ps1
# Carga variables desde .env y las sube a Azure App Service.
# No subas este script con secretos generados a GitHub si luego lo modificas para escribir valores reales.

$ErrorActionPreference = "Stop"

# =========================
# CONFIGURA ESTO
# =========================
$ResourceGroup = "rg-heicflow-prod"
$WebAppName = "heicflow-prod"
$AzureBaseUrl = "https://heicflow-prod-g4bddfewdebddhbr.centralus-01.azurewebsites.net"
$EnvPath = ".env"

# =========================
# VALIDACIONES
# =========================
if (!(Test-Path $EnvPath)) {
    throw "No encontré el archivo .env en la ruta actual. Ejecuta este script desde la raíz del proyecto."
}

Write-Host "Leyendo variables desde $EnvPath..." -ForegroundColor Cyan

$appSettings = @{}

# Lee .env respetando líneas vacías y comentarios
Get-Content $EnvPath | ForEach-Object {
    $line = $_.Trim()

    if ([string]::IsNullOrWhiteSpace($line)) { return }
    if ($line.StartsWith("#")) { return }
    if (!$line.Contains("=")) { return }

    $parts = $line.Split("=", 2)
    $key = $parts[0].Trim()
    $value = $parts[1]

    if (![string]::IsNullOrWhiteSpace($key)) {
        $appSettings[$key] = $value
    }
}

# =========================
# OVERRIDES PARA AZURE
# =========================

# En Azure NO uses ngrok
$appSettings["BASE_URL"] = $AzureBaseUrl
$appSettings["PUBLIC_BASE_URL"] = $AzureBaseUrl

# Producción
$appSettings["FLASK_ENV"] = "production"
$appSettings["FLASK_DEBUG"] = "0"

# Build durante ZIP deploy
$appSettings["SCM_DO_BUILD_DURING_DEPLOYMENT"] = "true"

# SQLite persistente temporal en Azure App Service
# OJO: para producción real con pagos luego migramos a PostgreSQL
$appSettings["DATABASE_URL"] = "sqlite:////home/site/data/heicflow.db"

# Contacto: cambia esto si quieres antes de ejecutar
if (!$appSettings.ContainsKey("CONTACT_EMAIL") -or $appSettings["CONTACT_EMAIL"] -eq "support@your-domain.com") {
    $appSettings["CONTACT_EMAIL"] = "support@your-domain.com"
}

# SECRET_KEY: nunca dejes change-me en Azure
if (!$appSettings.ContainsKey("SECRET_KEY") -or $appSettings["SECRET_KEY"] -eq "change-me" -or [string]::IsNullOrWhiteSpace($appSettings["SECRET_KEY"])) {
    $bytes = New-Object byte[] 48
    [System.Security.Cryptography.RandomNumberGenerator]::Fill($bytes)
    $secret = [Convert]::ToBase64String($bytes)
    $appSettings["SECRET_KEY"] = $secret
    Write-Host "SECRET_KEY generado automáticamente para Azure." -ForegroundColor Yellow
}

# Asegura nombre app
$appSettings["APP_NAME"] = "HEICFlow"

# =========================
# CREAR JSON TEMPORAL
# =========================

$tempJson = Join-Path $env:TEMP "heicflow-azure-appsettings.json"

# Azure CLI acepta settings desde JSON con --settings @archivo.json
$appSettings | ConvertTo-Json -Depth 20 | Set-Content -Path $tempJson -Encoding UTF8

Write-Host "Archivo temporal creado: $tempJson" -ForegroundColor Cyan
Write-Host "Subiendo App Settings a Azure..." -ForegroundColor Cyan

# =========================
# SUBIR SETTINGS
# =========================
az webapp config appsettings set `
    --resource-group $ResourceGroup `
    --name $WebAppName `
    --settings "@$tempJson" `
    --output table

Write-Host "Configurando Startup Command..." -ForegroundColor Cyan

az webapp config set `
    --resource-group $ResourceGroup `
    --name $WebAppName `
    --startup-file "gunicorn --bind=0.0.0.0:`${PORT:-8000} --timeout 180 wsgi:app" `
    --output table

Write-Host "Reiniciando App Service..." -ForegroundColor Cyan

az webapp restart `
    --resource-group $ResourceGroup `
    --name $WebAppName

Write-Host ""
Write-Host "Listo. Variables cargadas en Azure." -ForegroundColor Green
Write-Host "URL: $AzureBaseUrl" -ForegroundColor Green
Write-Host ""
Write-Host "Ahora debes registrar esta URL en Google OAuth:" -ForegroundColor Yellow
Write-Host "Authorized JavaScript origins:"
Write-Host $AzureBaseUrl
Write-Host ""
Write-Host "Authorized redirect URIs:"
Write-Host "$AzureBaseUrl/auth/callback"
Write-Host ""
Write-Host "ePayco usará:"
Write-Host "$AzureBaseUrl/payment/epayco/response"
Write-Host "$AzureBaseUrl/webhooks/epayco/confirmation"