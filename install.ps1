<#
  Сборка xlamBOT: программа, затем установщик.

    powershell -ExecutionPolicy Bypass -File install.ps1

  Что делает:
    1. переносимый Tesseract кладётся в vendor\tesseract, если его там нет
       (в репозитории его нет - это чужая сборка на 163 МБ);
    2. PyInstaller собирает dist\xlamBOT;
    3. Inno Setup собирает dist\xlamBOT-Setup-<версия>.exe.

  Что нужно на машине сборщика: Python с зависимостями проекта и Inno Setup 6.
#>

[CmdletBinding()]
param(
    # Пропустить PyInstaller и собрать установщик из текущего dist\xlamBOT
    [switch]$InstallerOnly
)

$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

$ProjectRoot = $PSScriptRoot
$DistDir = Join-Path $ProjectRoot 'dist'
$AppDir = Join-Path $DistDir 'xlamBOT'

function Write-Step($text) {
    Write-Host ''
    Write-Host "== $text" -ForegroundColor Cyan
}

function Get-Version {
    $versionText = Get-Content (Join-Path $ProjectRoot 'version.py') -Raw
    if ($versionText -match '__version__\s*=\s*["'']([^"'']+)["'']') { return $Matches[1] }
    throw 'Version missing from version.py'
}

# ── 1. переносимый Tesseract ────────────────────────────────────────────────
function Ensure-Tesseract {
    $target = Join-Path $ProjectRoot 'vendor\tesseract'
    if (Test-Path (Join-Path $target 'tesseract.exe')) {
        Write-Step "Tesseract уже на месте: $target"
        return
    }

    $sources = @(
        'C:\Program Files\Tesseract-OCR',
        'C:\Program Files (x86)\Tesseract-OCR',
        (Join-Path $env:LOCALAPPDATA 'Programs\Tesseract-OCR')
    )
    $source = $sources | Where-Object { Test-Path (Join-Path $_ 'tesseract.exe') } | Select-Object -First 1
    if (-not $source) {
        throw 'Tesseract не найден. Установите его (https://github.com/UB-Mannheim/tesseract/wiki) и запустите скрипт снова.'
    }

    Write-Step "Копирую Tesseract из $source"
    New-Item -ItemType Directory -Force -Path $target, (Join-Path $target 'tessdata') | Out-Null

    # Только то, что нужно для распознавания цифр: exe, библиотеки и eng.
    # Инструменты обучения (lstmtraining, mftraining и прочее) весят десятки
    # мегабайт и в рантайме не используются.
    Get-ChildItem $source -File | Where-Object { $_.Extension -in '.dll', '.exe' } |
        Where-Object { $_.Name -eq 'tesseract.exe' } |
        ForEach-Object { Copy-Item $_.FullName $target -Force }
    Get-ChildItem $source -File -Filter '*.dll' | ForEach-Object { Copy-Item $_.FullName $target -Force }
    foreach ($name in @('eng.traineddata', 'eng.user-patterns', 'eng.user-words')) {
        $file = Join-Path $source "tessdata\$name"
        if (Test-Path $file) { Copy-Item $file (Join-Path $target 'tessdata') -Force }
    }

    $size = [math]::Round(((Get-ChildItem $target -Recurse -File | Measure-Object Length -Sum).Sum / 1MB), 1)
    Write-Host "  готово: $size МБ" -ForegroundColor Green
}

# ── 2. программа ────────────────────────────────────────────────────────────
function Build-App {
    & (Join-Path $ProjectRoot '.venv\Scripts\python.exe') (Join-Path $ProjectRoot 'tools_build_metadata.py')
    if ($LASTEXITCODE -ne 0) { throw 'Version metadata generation failed' }
    Write-Step 'Собираю очередь бойцов по умолчанию'
    # Файл не в репозитории и без него на старте пусто, а играть нечем.
    & (Join-Path $ProjectRoot '.venv\Scripts\python.exe') `
        (Join-Path $ProjectRoot 'tools_make_default_queue.py')
    if ($LASTEXITCODE -ne 0) { throw "Очередь бойцов не собралась" }

    Write-Step 'Собираю программу (PyInstaller)'
    if (Test-Path $AppDir) {
        $resolvedAppDir = (Resolve-Path -LiteralPath $AppDir).Path
        $expectedAppDir = [IO.Path]::GetFullPath((Join-Path $ProjectRoot 'dist\xlamBOT'))
        if ($resolvedAppDir -ne $expectedAppDir) { throw 'Unsafe build output path' }
        Remove-Item -LiteralPath $resolvedAppDir -Recurse -Force
    }
    & (Join-Path $ProjectRoot '.venv\Scripts\python.exe') -m PyInstaller `
        (Join-Path $ProjectRoot 'xlambot.spec') --noconfirm --clean
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller упал с кодом $LASTEXITCODE" }
    if (-not (Test-Path (Join-Path $AppDir 'xlamBOT.exe'))) { throw 'xlamBOT.exe не появился в dist' }

    $queue = Join-Path $AppDir '_internal\latest_brawler_data.json'
    if (-not (Test-Path $queue)) {
        throw 'В сборке нет очереди бойцов'
    }
    # Считаем через ConvertFrom-Json, а не через @( ... ).Count: обёртка
    #PowerShell вокруг результата конвертации ведёт себя по-разному в 5.1 и 7,
    #и из-за этого проверка молча показывала не то число.
    $parsed = Get-Content $queue -Raw -Encoding UTF8 | ConvertFrom-Json
    $count = @($parsed).Length
    Write-Host "  очередь в сборке: $count бойцов" -ForegroundColor Green
    if ($count -lt 50) {
        throw "В сборке всего $count бойцов - очередь собралась неверно"
    }

    $size = [math]::Round(((Get-ChildItem $AppDir -Recurse -File | Measure-Object Length -Sum).Sum / 1MB), 1)
    Write-Host "  готово: $size МБ" -ForegroundColor Green
}

# ── 3. установщик ───────────────────────────────────────────────────────────
function Find-Iscc {
    $candidates = @(
        (Join-Path ${env:ProgramFiles(x86)} 'Inno Setup 6\ISCC.exe'),
        (Join-Path $env:ProgramFiles 'Inno Setup 6\ISCC.exe'),
        (Join-Path $env:LOCALAPPDATA 'Programs\Inno Setup 6\ISCC.exe')
    )
    $found = $candidates | Where-Object { Test-Path $_ } | Select-Object -First 1
    if (-not $found) { return $null }
    return $found
}

function Build-Installer {
    $exeVersion = (Get-Item -LiteralPath (Join-Path $AppDir 'xlamBOT.exe')).VersionInfo.ProductVersion
    if ($exeVersion -ne (Get-Version)) { throw 'EXE version differs from version.py; rebuild the application first' }
    $iscc = Find-Iscc
    if (-not $iscc) {
        throw 'Inno Setup не найден. Установите: winget install JRSoftware.InnoSetup'
    }

    Write-Step 'Собираю установщик (Inno Setup)'
    & $iscc (Join-Path $ProjectRoot 'installer.iss')
    if ($LASTEXITCODE -ne 0) { throw "Inno Setup упал с кодом $LASTEXITCODE" }

    $setup = Get-ChildItem $DistDir -Filter 'xlamBOT-Setup-*.exe' |
        Sort-Object LastWriteTime | Select-Object -Last 1
    if (-not $setup) { throw 'Установщик не появился в dist' }

    Write-Host ''
    Write-Host "Готово: $($setup.FullName)" -ForegroundColor Green
    Write-Host "Размер: $([math]::Round($setup.Length / 1MB, 1)) МБ" -ForegroundColor Green
}

# ── main ────────────────────────────────────────────────────────────────────
Ensure-Tesseract
if (-not $InstallerOnly) { Build-App }
Build-Installer
