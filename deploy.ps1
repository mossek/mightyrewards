<#
  Deploy Mighty Car-Wash Rewards to Azure.

  First time:   .\deploy.ps1            (creates everything, asks for the PINs)
  Code update:  .\deploy.ps1 -UpdateOnly

  Needs: Azure CLI (az login done) and Node.js LTS (for the Static Web Apps deploy tool).
#>
param(
    [string]$ResourceGroup = "mighty-rewards-rg",
    [string]$DataRegion = "southafricanorth",   # customer data stays in South Africa
    [string]$AppRegion = "westeurope",          # nearest region that offers Static Web Apps
    [string]$AppName = "mighty-rewards",
    [string]$AdminPin,
    [string]$StaffPin,
    [switch]$UpdateOnly
)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

function Invoke-Az {
    # az prints warnings on stderr; don't let PowerShell treat those as errors, only a failed exit code.
    $ErrorActionPreference = "Continue"
    $out = & az @args --only-show-errors 2>&1 | Where-Object { $_ -isnot [System.Management.Automation.ErrorRecord] }
    if ($LASTEXITCODE -ne 0) { throw "az $($args[0..2] -join ' ') failed" }
    $out
}

if (-not $UpdateOnly) {
    $adminPin = if ($AdminPin) { $AdminPin } else { Read-Host "Choose the OWNER PIN (6 or more digits, only you should know it)" }
    if ($adminPin -notmatch '^\d{6,}$') { throw "The owner PIN must be 6 or more digits." }
    $staffPin = if ($StaffPin) { $StaffPin } else { Read-Host "Choose the first STAFF PIN (exactly 4 digits)" }
    if ($staffPin -notmatch '^\d{4}$') { throw "The staff PIN must be exactly 4 digits." }

    Write-Host "Creating resource group..."
    Invoke-Az group create -n $ResourceGroup -l $DataRegion -o none

    $storage = "mightyrw" + (Get-Random -Minimum 100000 -Maximum 999999)
    Write-Host "Creating storage account $storage in $DataRegion..."
    Invoke-Az storage account create -n $storage -g $ResourceGroup -l $DataRegion --sku Standard_LRS --kind StorageV2 `
        --min-tls-version TLS1_2 --allow-blob-public-access false -o none
    $conn = Invoke-Az storage account show-connection-string -n $storage -g $ResourceGroup --query connectionString -o tsv

    Write-Host "Creating Static Web App (Free) in $AppRegion..."
    Invoke-Az staticwebapp create -n $AppName -g $ResourceGroup -l $AppRegion --sku Free -o none
    $hostName = Invoke-Az staticwebapp show -n $AppName -g $ResourceGroup --query defaultHostname -o tsv

    $bytes = New-Object byte[] 36
    [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    $secret = [Convert]::ToBase64String($bytes)

    Write-Host "Saving settings..."
    Invoke-Az staticwebapp appsettings set -n $AppName -g $ResourceGroup -o none --setting-names `
        "STORAGE_CONNECTION=$conn" "TOKEN_SECRET=$secret" "ADMIN_PIN=$adminPin" "STAFF_PIN=$staffPin" `
        "PUBLIC_URL=https://$hostName" "BUSINESS_NAME=Mighty Car-Wash" "BUSINESS_LOCATION=Bolobedu Matipane Molai" `
        "WA_PROVIDER=off"
}

$hostName = Invoke-Az staticwebapp show -n $AppName -g $ResourceGroup --query defaultHostname -o tsv
$token = Invoke-Az staticwebapp secrets list -n $AppName -g $ResourceGroup --query properties.apiKey -o tsv

$ErrorActionPreference = "Continue"   # pip/npm print warnings on stderr; only exit codes count

# The upload doesn't install the server's Python packages, so bundle them (Linux builds for Azure).
Write-Host "Bundling server packages..."
$site = Join-Path $PSScriptRoot "api\.python_packages\lib\site-packages"
if (Test-Path (Join-Path $site "azure\data\tables")) {
    Write-Host "  already bundled (delete api\.python_packages to re-bundle)"
} else {
    python -m pip install --quiet --disable-pip-version-check --target $site --platform manylinux2014_x86_64 `
        --python-version 3.11 --implementation cp --only-binary=:all: azure-data-tables
    if ($LASTEXITCODE -ne 0) { throw "Bundling the server packages failed" }
}

# Install the Static Web Apps upload tool once, in its own folder (npx's temp folder gets locked on some PCs).
$tools = Join-Path $env:LOCALAPPDATA "mighty-rewards-tools"
$swa = Join-Path $tools "node_modules\.bin\swa.cmd"
if (-not (Test-Path $swa)) {
    Write-Host "Installing the upload tool..."
    npm install --prefix $tools @azure/static-web-apps-cli@2 --no-audit --no-fund --loglevel=error
    if ($LASTEXITCODE -ne 0) { throw "Installing the upload tool failed" }
}

Write-Host "Uploading the app and server..."
& $swa deploy ./app --api-location ./api --api-language python --api-version 3.11 `
    --deployment-token $token --env production --no-use-keychain
if ($LASTEXITCODE -ne 0) { throw "Deploy failed" }

Write-Host ""
Write-Host "Done. Open https://$hostName on the counter phone and add it to the home screen."
