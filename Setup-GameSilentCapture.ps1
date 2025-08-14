#requires -Version 5.1
<#
Setup-GameSilentCapture.ps1 (ASCII-safe, 2025-08)
- Route game audio to VB-CABLE so speakers stay silent while OBS records.
- Patch current OBS profile: 48 kHz, disable Desktop Audio, set Mic/Aux = CABLE Output.
- Optionally reset everything back with -Reset.
Prereq: VB-Audio Virtual Cable installed (CABLE Input / CABLE Output visible in Sound control panel).
This script uses the current AudioDeviceCmdlets (Get-AudioDevice / Set-AudioDevice).
#>

param(
  [string]$GamePath = "",
  [string]$GameArgs = "",
  [switch]$KillOBS,
  [switch]$VerboseLog,
  [switch]$Reset,
  [string]$PreferredPlaybackName = ""   # only used in -Reset to pick your speaker by name
)

function Write-Log($msg) {
  if ($VerboseLog) { Write-Host "[*] $msg" -ForegroundColor Cyan } else { Write-Host "$msg" }
}

function Ensure-AudioDeviceModule {
  try {
    if (-not (Get-Module -ListAvailable -Name AudioDeviceCmdlets)) {
      Write-Log "Installing AudioDeviceCmdlets (CurrentUser)..."
      try { Install-PackageProvider -Name NuGet -MinimumVersion 2.8.5.201 -Force -Scope CurrentUser -ErrorAction SilentlyContinue } catch {}
      Install-Module -Name AudioDeviceCmdlets -Scope CurrentUser -Force -AllowClobber
    }
    Import-Module AudioDeviceCmdlets -ErrorAction Stop
  } catch {
    Write-Error "Failed to install/import AudioDeviceCmdlets. Please ensure internet connectivity and try again. $_"
    exit 1
  }
}

function Find-CableDevices {
  $all = Get-AudioDevice -List
  $cableIn  = $all | Where-Object { $_.Type -eq 'Playback'  -and $_.Name -like '*CABLE Input*' }
  $cableOut = $all | Where-Object { $_.Type -eq 'Recording' -and $_.Name -like '*CABLE Output*' }
  if (-not $cableIn -or -not $cableOut) {
    Write-Error "VB-CABLE not found. Install VB-Audio Virtual Cable first."
    exit 1
  }
  return @{ In = $cableIn; Out = $cableOut }
}

function Get-ObsPaths {
  $obsAppData = Join-Path $env:APPDATA 'obs-studio'
  $globalIni  = Join-Path $obsAppData 'global.ini'
  if (-not (Test-Path $globalIni)) { return $null }

  $profileMatch = Select-String -Path $globalIni -Pattern '^\s*Profile\s*=\s*(.+)$'
  $currentProfile = $null
  if ($profileMatch) { $currentProfile = $profileMatch.Matches.Groups[1].Value.Trim() }

  $profilesRoot = Join-Path $obsAppData 'basic\profiles'
  if (-not $currentProfile) {
    $first = Get-ChildItem -Path $profilesRoot -Directory -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($first) { $currentProfile = $first.Name }
  }
  if (-not $currentProfile) { return $null }

  $profileDir = Join-Path $profilesRoot $currentProfile
  $basicIni   = Join-Path $profileDir 'basic.ini'
  if (-not (Test-Path $basicIni)) { return $null }

  return @{
    GlobalIni   = $globalIni
    ProfileName = $currentProfile
    ProfileDir  = $profileDir
    BasicIni    = $basicIni
  }
}

function Set-IniKeyInSection([string[]]$refLines, [string]$section, [hashtable]$kv) {
  $out = @()
  $inSection = $false
  $done = [System.Collections.Generic.HashSet[string]]::new()
  foreach ($line in $refLines) {
    if ($line -match '^\s*\[.*\]\s*$') {
      if ($inSection) {
        foreach ($k in $kv.Keys) { if (-not $done.Contains($k)) { $out += "$k=$($kv[$k])"; $done.Add($k) | Out-Null } }
        $inSection = $false
      }
      if ($line.Trim() -ieq "[$section]") { $inSection = $true }
      $out += $line
      continue
    }
    if ($inSection -and $line -match '^\s*([^=]+)\s*=\s*(.*)$') {
      $key = $Matches[1].Trim()
      if ($kv.ContainsKey($key)) {
        $out += "$key=$($kv[$key])"
        $done.Add($key) | Out-Null
        continue
      }
    }
    $out += $line
  }
  if ($inSection) {
    foreach ($k in $kv.Keys) { if (-not $done.Contains($k)) { $out += "$k=$($kv[$k])" } }
  }
  return ,$out
}

function Patch-ObsAudio($obs, $cableOutId) {
  $basicIni = $obs.BasicIni
  $backupPath = "$basicIni.bak_{0}" -f (Get-Date -Format 'yyyyMMdd_HHmmss')
  Copy-Item $basicIni $backupPath -Force
  Write-Log "Backed up OBS profile: $backupPath"

  $lines = Get-Content $basicIni -Encoding UTF8
  if (-not ($lines | Select-String '^\s*\[Audio\]\s*$')) {
    $lines += ''
    $lines += '[Audio]'
  }

  $audioMap = @{
    'SampleRate'           = '48000'
    'DesktopAudioDevice1'  = 'disabled'
    'DesktopAudioDevice2'  = 'disabled'
    'MicDevice1'           = $cableOutId
    'MicDevice2'           = 'disabled'
    'MicDevice3'           = 'disabled'
  }

  $lines = Set-IniKeyInSection -refLines $lines -section 'Audio' -kv $audioMap
  $lines | Set-Content $basicIni -Encoding UTF8
  Write-Log ("OBS patched (Profile='{0}'): 48 kHz, DesktopAudio disabled, Mic/Aux = CABLE Output." -f $obs.ProfileName)
}

function Get-CurrentPlaybackDefault {
  # AudioDeviceCmdlets (new): mark Default=$true on current default playback
  $def = Get-AudioDevice -Playback | Where-Object { $_.Default -eq $true } | Select-Object -First 1
  return $def
}

function Set-PlaybackDefault($deviceId) {
  # Set-AudioDevice without -Role sets the overall default (sufficient for our use-case)
  Set-AudioDevice -Id $deviceId | Out-Null
}

function Reset-ObsAudio($obs) {
  $basicIni = $obs.BasicIni
  $profileDir = $obs.ProfileDir
  $backups = Get-ChildItem -Path $profileDir -Filter 'basic.ini.bak_*' -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending
  if ($backups -and $backups.Count -gt 0) {
    $latest = $backups[0]
    Copy-Item $latest.FullName $basicIni -Force
    Write-Log ("OBS restored from backup: {0}" -f $latest.Name)
    return
  }

  $lines = Get-Content $basicIni -Encoding UTF8
  if (-not ($lines | Select-String '^\s*\[Audio\]\s*$')) {
    $lines += ''
    $lines += '[Audio]'
  }
  $audioReset = @{
    'DesktopAudioDevice1' = 'default'
    'DesktopAudioDevice2' = 'disabled'
    'MicDevice1'          = 'default'
    'MicDevice2'          = 'disabled'
    'MicDevice3'          = 'disabled'
  }
  $lines = Set-IniKeyInSection -refLines $lines -section 'Audio' -kv $audioReset
  $lines | Set-Content $basicIni -Encoding UTF8
  Write-Log "OBS audio devices set back to 'default' (no backup found)."
}

# ----- MAIN -----
Ensure-AudioDeviceModule

# Optional: close OBS for safe config write
$obsProc = Get-Process obs64 -ErrorAction SilentlyContinue
if ($obsProc) {
  if ($KillOBS) {
    Write-Log "Closing OBS..."
    $obsProc | Stop-Process -Force
    Start-Sleep -Seconds 1
  } else {
    Write-Host "OBS is running. Close it or re-run with -KillOBS for safe patching." -ForegroundColor Yellow
  }
}

# Load OBS paths (if installed/launched before)
$obs = Get-ObsPaths
if (-not $obs) {
  Write-Host "OBS config not found. Launch OBS once so it creates its config, then re-run this script if you need OBS patching." -ForegroundColor Yellow
}

if ($Reset) {
  # -------- RESET MODE --------
  Write-Log "Reset mode: restore system/OBS to pre-setup state."

  # 1) Restore Windows default playback to a non-CABLE device
  try {
    $playbacks = Get-AudioDevice -Playback
    $target = $null
    if ($PreferredPlaybackName) {
      $target = $playbacks | Where-Object { $_.Name -like "*$PreferredPlaybackName*" } | Select-Object -First 1
    }
    if (-not $target) {
      $target = $playbacks | Where-Object { $_.Name -notlike '*CABLE*' -and $_.Name -notlike '*VB-Audio*' -and $_.State -eq 'Active' } | Select-Object -First 1
    }
    if (-not $target) { $target = $playbacks | Select-Object -First 1 }
    if ($target) {
      Set-PlaybackDefault -deviceId $target.Id
      Write-Log ("Default playback set to: {0}" -f $target.Name)
    } else {
      Write-Host "Warning: cannot determine a playback device to set as default." -ForegroundColor Yellow
    }
  } catch { Write-Host "Warning: failed to reset Windows default playback. $_" -ForegroundColor Yellow }

  # 2) Restore OBS audio devices
  if ($obs) {
    Reset-ObsAudio -obs $obs
  }

  Write-Host "`nRESET DONE." -ForegroundColor Green
  Write-Host "If OBS was open, restart OBS to apply configuration." -ForegroundColor DarkGray
  exit 0
}

# -------- SETUP MODE --------
# 1) Ensure VB-CABLE exists
$cables = Find-CableDevices
Write-Log ("Found VB-CABLE: Playback='{0}'  Recording='{1}'." -f $cables.In.Name, $cables.Out.Name)

# 2) Patch OBS audio (if OBS config available)
if ($obs) {
  Patch-ObsAudio -obs $obs -cableOutId $cables.Out.Id
} else {
  Write-Log "Skipping OBS patch (no config found)."
}

# 3) Switch default playback to CABLE, launch game, then restore
$oldDefault = $null
try {
  $oldDefault = Get-CurrentPlaybackDefault
  if ($oldDefault) { Write-Log ("Current default playback: {0}" -f $oldDefault.Name) }
} catch { Write-Host "Warning: cannot read current default playback. $_" -ForegroundColor Yellow }

try {
  Set-PlaybackDefault -deviceId $cables.In.Id
  Write-Log ("Set default playback to: {0}" -f $cables.In.Name)
} catch {
  Write-Host "Warning: failed to set default playback to CABLE. $_" -ForegroundColor Yellow
}

if ($GamePath) {
  if (Test-Path $GamePath) {
    Write-Log ("Launching game: {0} {1}" -f $GamePath, $GameArgs)
    try { $proc = Start-Process -FilePath $GamePath -ArgumentList $GameArgs -PassThru } catch { Write-Host "Warning: failed to launch game. $_" -ForegroundColor Yellow }
    Start-Sleep -Seconds 5
  } else {
    Write-Host ("Warning: GamePath does not exist: {0}" -f $GamePath) -ForegroundColor Yellow
  }
}

if ($oldDefault) {
  try {
    Set-PlaybackDefault -deviceId $oldDefault.Id
    Write-Log ("Restored default playback: {0}" -f $oldDefault.Name)
  } catch {
    Write-Host "Warning: failed to restore previous default playback. $_" -ForegroundColor Yellow
  }
} else {
  Write-Log "Previous default playback not known; leaving CABLE as default."
}

Write-Host ""
Write-Host "SETUP DONE. OBS will record from 'CABLE Output'. Speakers remain silent." -ForegroundColor Green
Write-Host "Tip: Launch the game via this script so it binds to CABLE first." -ForegroundColor DarkGray
