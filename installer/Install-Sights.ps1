# Installs the ranging sights for War Thunder and selects them on every vehicle.
#
#   -Mode Reload      sights + reload indicator on every vehicle
#   -Mode NoReload    sights only
#   -Mode Uninstall   remove the sights and their settings again
#
# What it changes, for every account folder under
# Documents\My Games\WarThunder\Saves\<id>\production :
#   UserSights\<vehicle>\ranging_*.blk     the sight files (copied from ..\sights)
#   global.blk                             the vehicle's Sight settings entry
# global.blk is copied to global.blk.bak-<timestamp> before it is changed.
#
# The game must be closed: it rewrites global.blk when it exits.

param(
    [ValidateSet('Reload', 'NoReload', 'Uninstall')]
    [string]$Mode = 'Reload',
    [string]$SavesRoot,          # for testing: use this folder instead of Documents
    [switch]$AllowRunning        # for testing: skip the "game is running" check
)

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$sights = Join-Path $repo 'sights'
$utf8 = New-Object System.Text.UTF8Encoding($false)

if (-not $SavesRoot) {
    $SavesRoot = Join-Path ([Environment]::GetFolderPath('MyDocuments')) 'My Games\WarThunder\Saves'
}
if (-not $AllowRunning -and (Get-Process -Name aces -ErrorAction SilentlyContinue)) {
    throw 'War Thunder is running. Close the game first: it rewrites its settings file when it exits and would undo this.'
}
if (-not (Test-Path $SavesRoot)) {
    throw "War Thunder saves folder not found: $SavesRoot. Start the game once, then run this again."
}
$prods = @(Get-ChildItem $SavesRoot -Directory |
    ForEach-Object { Join-Path $_.FullName 'production' } |
    Where-Object { Test-Path (Join-Path $_ 'global.blk') })
if ($prods.Count -eq 0) {
    throw "No global.blk found under $SavesRoot. Start the game and log in once, then run this again."
}

# vehicle -> sight name
$manifest = @{}
if ($Mode -ne 'Uninstall') {
    Import-Csv (Join-Path $sights 'manifest.csv') | ForEach-Object { $manifest[$_.unit] = $_.sight }
    if ($manifest.Count -eq 0) { throw 'sights\manifest.csv is empty or missing.' }
}

# One vehicle's Sight settings entry, as lines relative to the entry's indent.
function New-Entry([string]$indent, [string]$unit, [string]$sight, [bool]$reload) {
    $l = New-Object System.Collections.Generic.List[string]
    $i = $indent + '  '
    $l.Add("$indent$unit{")
    $l.Add("$i" + 'crosshair:t="' + $sight + '"')
    $l.Add("$i" + 'crosshairColor:c=0, 0, 0, 255')
    $l.Add("$i" + 'crosshairLightColor:c=255, 64, 64, 255')
    $l.Add("$i" + 'shotDistScaleOffset:i=0')
    $l.Add('')
    if ($reload) {
        $l.Add("$i" + 'gunReady{')
        foreach ($x in 'visible:b=yes', 'color:c=0, 0, 0, 255', 'lightColor:c=0, 0, 0, 255',
            'nvColor:c=0, 0, 0, 255', 'thermalColor:c=0, 1, 75, 0', 'textSize:i=30', 'font:t=""',
            'position:p2=937.5, 376.5', 'gunReadyMarkVisible:b=yes',
            'gunReadyReloadCountdownVisible:b=yes') { $l.Add("$i  $x") }
        $l.Add("$i}")
        $l.Add('')
    }
    $l.Add("$i" + 'bulletType{')
    foreach ($x in 'visible:b=no', 'isShortName:b=no', 'textColor:c=0, 0, 0, 255',
        'bgColor:c=0, 0, 0, 0', 'nightVisionTextColor:c=0, 0, 0, 255',
        'nightVisionBgColor:c=0, 0, 0, 0', 'lightTextColor:c=0, 0, 0, 255',
        'lightBgColor:c=0, 0, 0, 0', 'thermalTextColor:c=0, 0, 0, 255',
        'thermalBgColor:c=0, 0, 0, 0', 'position:p2=700, 202', 'textSize:i=30', 'font:t=""') {
        $l.Add("$i  $x")
    }
    $l.Add("$i}")
    $l.Add("$indent}")
    return $l
}

# Index just past the closing brace of the block that opens on $lines[$start].
function Get-BlockEnd($lines, [int]$start) {
    $depth = 0
    for ($k = $start; $k -lt $lines.Count; $k++) {
        $t = $lines[$k].Trim()
        if ($t.EndsWith('{')) { $depth++ }
        elseif ($t -eq '}') { $depth--; if ($depth -eq 0) { return $k + 1 } }
    }
    throw 'Settings file has unbalanced braces; nothing was changed.'
}

function Find-Line($lines, [string]$trimmed) {
    for ($k = 0; $k -lt $lines.Count; $k++) { if ($lines[$k].Trim() -eq $trimmed) { return $k } }
    return -1
}

function Update-Settings([string]$path, [string]$sightDir) {
    $raw = [IO.File]::ReadAllText($path, $utf8)
    $nl = "`n"; if ($raw.Contains("`r`n")) { $nl = "`r`n" }
    $lines = New-Object System.Collections.Generic.List[string]
    $lines.AddRange([string[]]($raw -split "`r?`n"))

    $top = Find-Line $lines 'tankSightSettings{'
    if ($top -lt 0) {
        if ($Mode -eq 'Uninstall') { return 'nothing to remove' }
        # No vehicle has saved sight settings yet: create the block.
        $cs = Find-Line $lines 'customSettings{'
        if ($cs -lt 0) {
            throw "Could not find the sight settings section in $path. In the game, open Sight settings on any tank, press Save, close the game and run this again."
        }
        $csIndent = $lines[$cs].Substring(0, $lines[$cs].Length - $lines[$cs].TrimStart().Length)
        $csEnd = (Get-BlockEnd $lines $cs) - 1
        $lines.Insert($csEnd, "$csIndent  }")
        $lines.Insert($csEnd, "$csIndent  tankSightSettings{")
        $top = $csEnd
    }
    $end = Get-BlockEnd $lines $top
    $blockIndent = $lines[$top].Substring(0, $lines[$top].Length - $lines[$top].TrimStart().Length)
    $indent = $blockIndent + '  '

    # Existing entries, in file order: name -> lines, and the sight each selects.
    $have = [ordered]@{}
    $k = $top + 1
    while ($k -lt $end - 1) {
        $t = $lines[$k].Trim()
        if ($t -match '^([A-Za-z0-9_\-]+)\{$') {
            $e = Get-BlockEnd $lines $k
            $have[$Matches[1]] = $lines.GetRange($k, $e - $k)
            $k = $e
        }
        else { $k++ }
    }

    $out = New-Object System.Collections.Generic.List[string]
    $kept = 0; $set = 0; $removed = 0
    $names = New-Object System.Collections.Generic.List[string]
    foreach ($n in $have.Keys) { $names.Add($n) }
    foreach ($n in ($manifest.Keys | Sort-Object)) { if (-not $have.Contains($n)) { $names.Add($n) } }

    foreach ($unit in $names) {
        $cur = $null
        if ($have.Contains($unit)) {
            foreach ($x in $have[$unit]) {
                if ($x.Trim() -match '^crosshair:t="(.*)"$') { $cur = $Matches[1]; break }
            }
        }
        $ours = ($null -ne $cur) -and $cur.StartsWith('ranging_')
        if ($have.Contains($unit) -and -not $ours) {
            # The player's own choice of sight: leave the entry exactly as it is.
            $out.AddRange([string[]]$have[$unit]); $out.Add(''); $kept++
            continue
        }
        if ($Mode -eq 'Uninstall') { $removed++; continue }
        $sight = $manifest[$unit]
        # keep a ranging sight already picked, as long as its file still exists
        if ($ours -and (Test-Path (Join-Path $sightDir "$unit\$cur.blk"))) { $sight = $cur }
        if (-not $sight) { continue }
        $out.AddRange([string[]](New-Entry $indent $unit $sight ($Mode -eq 'Reload')))
        $out.Add(''); $set++
    }
    if ($out.Count -gt 0 -and $out[$out.Count - 1] -eq '') { $out.RemoveAt($out.Count - 1) }

    $new = New-Object System.Collections.Generic.List[string]
    $new.AddRange($lines.GetRange(0, $top + 1))
    $new.AddRange($out)
    $new.AddRange($lines.GetRange($end - 1, $lines.Count - ($end - 1)))

    $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
    Copy-Item $path "$path.bak-$stamp"
    [IO.File]::WriteAllText($path, ($new -join $nl), $utf8)
    if ($Mode -eq 'Uninstall') { return "removed $removed entries, left $kept of your own untouched (backup: global.blk.bak-$stamp)" }
    return "set $set vehicles, left $kept of your own untouched (backup: global.blk.bak-$stamp)"
}

foreach ($prod in $prods) {
    Write-Host ''
    Write-Host $prod
    $dest = Join-Path $prod 'UserSights'
    if ($Mode -eq 'Uninstall') {
        $n = 0
        if (Test-Path $dest) {
            Get-ChildItem $dest -Recurse -Filter 'ranging_*.blk' -File | ForEach-Object { Remove-Item $_.FullName; $n++ }
            Get-ChildItem $dest -Directory | Where-Object { -not (Get-ChildItem $_.FullName -Force) } |
                ForEach-Object { Remove-Item $_.FullName }
        }
        Write-Host "  sight files removed: $n"
    }
    else {
        New-Item -ItemType Directory -Force $dest | Out-Null
        & robocopy $sights $dest 'ranging_*.blk' /S /NFL /NDL /NJH /NJS /NP | Out-Null
        if ($LASTEXITCODE -ge 8) { throw "Copying sight files failed (robocopy code $LASTEXITCODE)." }
        $global:LASTEXITCODE = 0
        Write-Host "  sight files installed: $((Get-ChildItem $dest -Recurse -Filter 'ranging_*.blk' -File).Count)"
    }
    Write-Host "  settings: $(Update-Settings (Join-Path $prod 'global.blk') $dest)"
}
Write-Host ''
if ($Mode -eq 'Uninstall') { Write-Host 'Done. The ranging sights are removed.' }
else { Write-Host 'Done. Start War Thunder; every vehicle now uses its ranging sight.' }
