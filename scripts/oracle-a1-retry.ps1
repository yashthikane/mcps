# Keep trying to create an Oracle Cloud Always Free Ampere A1 instance until capacity frees up.
# Needs the OCI CLI signed in with an API key (~\.oci\config). Leave the window open (and the laptop awake).
#   scripts\oracle-a1-retry.ps1                       1 OCPU / 6 GB, try every 2 minutes
#   scripts\oracle-a1-retry.ps1 -Ocpus 2 -MemoryGb 12 -IntervalSeconds 300
#   scripts\oracle-a1-retry.ps1 -Ocpus 2 -MemoryGb 12 -FallbackAt 08:00 -FallbackOcpus 1 -FallbackMemoryGb 6
#     (try the bigger size until 08:00 local time, then switch to the smaller one)
param(
    [string]$Name = "donna",
    [int]$Ocpus = 1,
    [int]$MemoryGb = 6,
    [string]$FallbackAt = "",
    [int]$FallbackOcpus = 1,
    [int]$FallbackMemoryGb = 6,
    [int]$BootVolumeGb = 50,
    [string]$VcnName = "nexus-core-vcn",
    [string]$KeyPath = "$HOME\.ssh\donna",
    [int]$IntervalSeconds = 120,
    [string]$OciProfile = "DEFAULT"
)
$ErrorActionPreference = "Stop"
$Shape = "VM.Standard.A1.Flex"
if ($IntervalSeconds -lt 60) { throw "Use -IntervalSeconds 60 or more; faster retries get throttled (429)." }

# -FallbackAt HH:mm means the next time the clock reads HH:mm (today, or tomorrow if that has passed).
$fallbackTime = $null
if ($FallbackAt) {
    $fallbackTime = [datetime]::Today.Add([timespan]::Parse($FallbackAt))
    if ($fallbackTime -le (Get-Date)) { $fallbackTime = $fallbackTime.AddDays(1) }
}

function Invoke-Oci {
    # Runs the OCI CLI and returns @{Code; Json; Err}. Stderr is captured, not thrown (PowerShell 5.1).
    param([string[]]$OciArgs)
    $prev = $ErrorActionPreference; $ErrorActionPreference = "Continue"
    try { $out = & oci @OciArgs --profile $OciProfile --output json 2>&1; $code = $LASTEXITCODE }
    finally { $ErrorActionPreference = $prev }
    $std = ($out | Where-Object { $_ -isnot [System.Management.Automation.ErrorRecord] }) -join "`n"
    $err = ($out | Where-Object { $_ -is [System.Management.Automation.ErrorRecord] }) -join "`n"
    $json = $null
    if ($code -eq 0 -and $std.Trim()) { $json = $std | ConvertFrom-Json }
    return @{ Code = $code; Json = $json; Err = "$err`n$std" }
}

function Get-Oci([string[]]$OciArgs, [string]$What) {
    $r = Invoke-Oci $OciArgs
    if ($r.Code -ne 0) { throw "Could not $What.`n$($r.Err)" }
    return $r.Json
}

# --- Preflight ---------------------------------------------------------------------------------
if (-not (Get-Command oci -ErrorAction SilentlyContinue)) {
    throw "The OCI CLI isn't installed. Run: py -m pip install --user oci-cli  (then open a new window)."
}
$configPath = "$HOME\.oci\config"
if (-not (Test-Path $configPath)) { throw "No $configPath yet. Add an API key in the console first (see the setup steps)." }

# Always Free resources live in the root compartment, whose OCID is the tenancy OCID.
$section = $null; $tenancy = $null
foreach ($line in Get-Content $configPath) {
    if ($line -match '^\s*\[(.+)\]\s*$') { $section = $Matches[1]; continue }
    if ($section -eq $OciProfile -and $line -match '^\s*tenancy\s*=\s*(\S+)') { $tenancy = $Matches[1] }
}
if (-not $tenancy) { throw "No tenancy= line in profile [$OciProfile] of $configPath." }

if (-not (Test-Path "$KeyPath.pub")) {
    Write-Host "Creating SSH key $KeyPath ..."
    New-Item -ItemType Directory -Force (Split-Path $KeyPath) | Out-Null
    & ssh-keygen -t rsa -b 4096 -f $KeyPath -N '""' -C $Name | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "ssh-keygen failed." }
}

Write-Host "Looking up availability domain, image and subnet..."
$ad = (Get-Oci @("iam", "availability-domain", "list", "--compartment-id", $tenancy) "list availability domains").data[0].name

$images = (Get-Oci @("compute", "image", "list", "--compartment-id", $tenancy,
        "--operating-system", "Canonical Ubuntu", "--operating-system-version", "24.04",
        "--shape", $Shape, "--sort-by", "TIMECREATED", "--sort-order", "DESC", "--all") "list Ubuntu images").data
$image = $images | Where-Object { $_.'display-name' -notmatch 'Minimal' } | Select-Object -First 1
if (-not $image) { throw "No Ubuntu 24.04 image found for $Shape." }

$vcn = (Get-Oci @("network", "vcn", "list", "--compartment-id", $tenancy, "--display-name", $VcnName) "list VCNs").data |
    Select-Object -First 1
if (-not $vcn) { throw "VCN '$VcnName' not found. Pass -VcnName with the right name." }
$subnet = (Get-Oci @("network", "subnet", "list", "--compartment-id", $tenancy, "--vcn-id", $vcn.id) "list subnets").data |
    Where-Object { -not $_.'prohibit-public-ip-on-vnic' } | Select-Object -First 1
if (-not $subnet) { throw "VCN '$VcnName' has no public subnet." }

# PowerShell 5.1 mangles quotes in JSON arguments to native programs, so pass it as a file.
$shapeFile = Join-Path $env:TEMP "oci-a1-shape.json"
Set-Content -Path $shapeFile -Encoding ascii -Value ('{"ocpus": ' + $Ocpus + ', "memoryInGBs": ' + $MemoryGb + '}')

Write-Host ""
Write-Host "  Name     $Name"
Write-Host "  Shape    $Shape  $Ocpus OCPU / $MemoryGb GB, boot $BootVolumeGb GB"
Write-Host "  Image    $($image.'display-name')"
Write-Host "  Subnet   $($subnet.'display-name') in $VcnName"
Write-Host "  AD       $ad"
Write-Host "  SSH key  $KeyPath"
Write-Host "  Retry    every $IntervalSeconds s (Ctrl+C to stop)"
if ($fallbackTime) { Write-Host "  Then     $FallbackOcpus OCPU / $FallbackMemoryGb GB from $($fallbackTime.ToString('yyyy-MM-dd HH:mm'))" }
Write-Host ""

# --- Retry loop --------------------------------------------------------------------------------
$attempt = 0
while ($true) {
    # Never create a duplicate, e.g. if a previous launch succeeded but its reply was lost.
    $existing = (Get-Oci @("compute", "instance", "list", "--compartment-id", $tenancy, "--display-name", $Name) "list instances").data |
        Where-Object { $_.'lifecycle-state' -notin @("TERMINATED", "TERMINATING") } | Select-Object -First 1
    if ($existing) { $instanceId = $existing.id; Write-Host "Instance '$Name' already exists ($($existing.'lifecycle-state'))."; break }

    if ($fallbackTime -and (Get-Date) -ge $fallbackTime) {
        $Ocpus = $FallbackOcpus; $MemoryGb = $FallbackMemoryGb; $fallbackTime = $null
        Set-Content -Path $shapeFile -Encoding ascii -Value ('{"ocpus": ' + $Ocpus + ', "memoryInGBs": ' + $MemoryGb + '}')
        Write-Host "Switching to $Ocpus OCPU / $MemoryGb GB."
    }

    $attempt++
    $stamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $r = Invoke-Oci @("compute", "instance", "launch",
        "--compartment-id", $tenancy, "--availability-domain", $ad, "--display-name", $Name,
        "--shape", $Shape, "--shape-config", "file://$shapeFile",
        "--image-id", $image.id, "--subnet-id", $subnet.id, "--assign-public-ip", "true",
        "--boot-volume-size-in-gbs", "$BootVolumeGb", "--ssh-authorized-keys-file", "$KeyPath.pub")

    if ($r.Code -eq 0) { $instanceId = $r.Json.data.id; Write-Host "[$stamp] #$attempt created with $Ocpus OCPU / $MemoryGb GB!"; break }

    if ($r.Err -match 'capacity|TooManyRequests|"status": 429|"status": 5\d\d|timed out|Connection') {
        $why = if ($r.Err -match 'capacity') { "out of capacity" } elseif ($r.Err -match '429|TooManyRequests') { "throttled" } else { "temporary error" }
        Write-Host "[$stamp] #$attempt $why, retrying in $IntervalSeconds s"
        Start-Sleep -Seconds $IntervalSeconds
        continue
    }
    # Anything else (LimitExceeded, NotAuthorized, bad parameter) won't fix itself by retrying.
    Write-Host "[$stamp] #$attempt failed with an error that retrying won't fix:" -ForegroundColor Red
    Write-Host $r.Err
    exit 1
}

# --- Wait for RUNNING and show how to connect --------------------------------------------------
Write-Host "Waiting for the instance to start..."
do {
    Start-Sleep -Seconds 15
    $state = (Get-Oci @("compute", "instance", "get", "--instance-id", $instanceId) "read the instance").data.'lifecycle-state'
    Write-Host "  $state"
} while ($state -in @("PROVISIONING", "STARTING"))

$ip = (Get-Oci @("compute", "instance", "list-vnics", "--instance-id", $instanceId) "read the public IP").data[0].'public-ip'
[console]::beep(880, 300); [console]::beep(1175, 400)
Write-Host ""
Write-Host "Done: '$Name' is $state at $ip" -ForegroundColor Green
Write-Host "Connect with:  ssh -i $KeyPath ubuntu@$ip"
