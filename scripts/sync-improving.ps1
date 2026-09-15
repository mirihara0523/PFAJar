# Explicit file list only. Default is read-only; -Apply enables deployment.
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string[]]$Files,
    [switch]$Apply,
    [string]$Node = 'C:\Users\mirih\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe'
)
$ErrorActionPreference = 'Stop'
$source = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$target = 'D:\Claude\masonjar-7.0.2-MC.1-improving-win32-x64'
$app = Join-Path $target 'resources\app'
$coord = 'D:\Claude\masonjar-7.0.2-MC.1-improving-win32-x64_coordination'
function Hash($p) { (Get-FileHash -LiteralPath $p -Algorithm SHA256).Hash }
function SafePath($root, $relative) {
    if ([IO.Path]::IsPathRooted($relative) -or $relative.Contains(':')) { throw "Relative path required: $relative" }
    $full = [IO.Path]::GetFullPath((Join-Path $root $relative))
    if (!$full.StartsWith($root + '\', [StringComparison]::OrdinalIgnoreCase)) { throw "Path escapes root: $relative" }
    $cursor = $full
    while ($cursor) {
        if (Test-Path -LiteralPath $cursor) {
            if ((Get-Item -LiteralPath $cursor -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw "Reparse point rejected: $cursor" }
        }
        $cursor = [IO.Path]::GetDirectoryName($cursor)
    }
    return $full
}
if (!(Test-Path -LiteralPath (Join-Path $app 'package.json'))) { throw 'Unpacked target app missing; full build required.' }
if ((Get-Content (Join-Path $source 'package.json') -Raw) -ne (Get-Content (Join-Path $app 'package.json') -Raw)) { throw 'package.json differs; review dependencies and perform full build.' }
$plan = @()
foreach ($file in ($Files | Sort-Object -Unique)) {
    $rel = $file.Replace('/', '\')
    if ($rel -notmatch '^(?:main|update_manager)\.js$|^(?:py|js|pages|css)\\[^:]+\.(?:py|js|html|css)$' -or $rel -match '(^|\\)\.\.(\\|$)') {
        throw "Not a supported runtime file: $file. Compile TypeScript first; dependency/assets/deletion changes need a separate build review."
    }
    $src = SafePath $source $rel
    $dst = SafePath $app $rel
    if (!(Test-Path -LiteralPath $src -PathType Leaf)) { throw "Source missing: $rel" }
    $old = if (Test-Path -LiteralPath $dst) { Hash $dst } else { $null }
    $new = Hash $src
    if ($old -ne $new) { $plan += [pscustomobject]@{File=$rel;Source=$src;Destination=$dst;Before=$old;After=$new} }
}
if (!$plan.Count) { Write-Output 'No changes in selected files.'; return }
$plan | Select-Object File,Before,After | Format-Table -AutoSize
if (!$Apply) { Write-Output 'Preview only. Run with -Apply after relevant regression tests pass.'; return }
# Fail closed if process inspection is unavailable. Never stop user processes.
$busy = @(Get-CimInstance Win32_Process | Where-Object {
    # The invoking PowerShell command naturally contains $target as an
    # argument. Exclude it while retaining the check for the app and its children.
    $_.ProcessId -ne $PID -and (
        ($_.ExecutablePath -and $_.ExecutablePath.StartsWith($target + '\', [StringComparison]::OrdinalIgnoreCase)) -or
        ($_.CommandLine -and $_.CommandLine.IndexOf($target, [StringComparison]::OrdinalIgnoreCase) -ge 0)
    )
})
if ($busy.Count) { throw 'Target app or child process is running. Close it before applying.' }
foreach ($entry in $plan) {
    if ($entry.File.EndsWith('.js')) { & $Node --check $entry.Source; if ($LASTEXITCODE) { throw 'JavaScript check failed.' } }
    if ($entry.File.EndsWith('.py')) {
        & (Join-Path $source '.venv\Scripts\python.exe') -c 'import ast,sys,tokenize; p=sys.argv[1]; ast.parse(tokenize.open(p).read(),filename=p)' $entry.Source
        if ($LASTEXITCODE) { throw 'Python check failed.' }
    }
}
$backup = Join-Path $coord ('deploy-backups\' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $backup -Force | Out-Null
$plan | ConvertTo-Json -Depth 4 | Set-Content (Join-Path $backup 'manifest.json') -Encoding UTF8
foreach ($entry in $plan) {
    if ($entry.Before) {
        $save = Join-Path $backup $entry.File
        New-Item -ItemType Directory -Path (Split-Path $save) -Force | Out-Null
        Copy-Item -LiteralPath $entry.Destination -Destination $save
        if ((Hash $save) -ne $entry.Before) { throw 'Target changed during backup; nothing applied.' }
    }
}
$touched = @()
try {
    foreach ($entry in $plan) {
        if ((Hash $entry.Source) -ne $entry.After) { throw 'Source changed during deployment.' }
        $touched += $entry
        New-Item -ItemType Directory -Path (Split-Path $entry.Destination) -Force | Out-Null
        Copy-Item -LiteralPath $entry.Source -Destination $entry.Destination -Force
        if ((Hash $entry.Destination) -ne $entry.After) { throw 'Deployed hash mismatch.' }
    }
} catch {
    foreach ($entry in $touched) {
        if ($entry.Before) { Copy-Item -LiteralPath (Join-Path $backup $entry.File) -Destination $entry.Destination -Force }
        elseif (Test-Path -LiteralPath $entry.Destination) { Remove-Item -LiteralPath $entry.Destination }
    }
    throw
}
Write-Output "$($plan.Count) files applied and SHA256 verified. Backup: $backup"
Write-Output 'Runtime verification remains required; syntax checks do not replace regression tests.'

