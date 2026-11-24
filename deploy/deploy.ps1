# Package the repo and install it on the server.  ./deploy/deploy.ps1 [-SetMailKey] [-Server user@host]
# The install runs as a systemd job on the server, so a dropped SSH link cannot cut it off.
# thefreeengine.site is behind Cloudflare (no SSH), so connect by the origin IP.
param([switch]$SetMailKey, [string]$Server = $(if ($env:TFE_SERVER) { $env:TFE_SERVER } else { 'root@170.23.67.208' }))
$ErrorActionPreference = 'Stop'
$Repo = Split-Path $PSScriptRoot -Parent
$tar = Join-Path $env:TEMP 'freeengine-release.tar.gz'
Push-Location $Repo
try {
    tar -czf $tar --exclude '__pycache__' --exclude 'music/samples' server voices web deploy music
    scp -o BatchMode=yes -q $tar "${Server}:/tmp/freeengine-release.tar.gz"
    scp -o BatchMode=yes -q deploy/install.sh "${Server}:/tmp/freeengine-install.sh"
    if ($SetMailKey) {
        . "$PSScriptRoot/keys.ps1"
        (Get-Key mailersend) | ssh -o BatchMode=yes $Server "umask 077; cat > /tmp/freeengine-mailkey"
    }
    $run = "sed -i 's/\r$//' /tmp/freeengine-install.sh; sudo -n systemctl reset-failed freeengine-deploy 2>/dev/null; " +
           "sudo -n systemd-run --unit=freeengine-deploy --collect /bin/bash -c " +
           "'if [ -f /tmp/freeengine-mailkey ]; then bash /tmp/freeengine-install.sh /tmp/freeengine-release.tar.gz < /tmp/freeengine-mailkey; else bash /tmp/freeengine-install.sh /tmp/freeengine-release.tar.gz < /dev/null; fi; rm -f /tmp/freeengine-mailkey /tmp/freeengine-release.tar.gz'"
    ssh -o BatchMode=yes $Server $run
    for ($i = 0; $i -lt 120; $i++) {
        Start-Sleep 10
        $st = ssh -o BatchMode=yes $Server "systemctl is-active freeengine-deploy 2>/dev/null"
        if ($st -ne 'active' -and $st -ne 'activating') { break }
    }
    ssh -o BatchMode=yes $Server "sudo -n journalctl -u freeengine-deploy --no-pager -n 300 -o cat | grep -vE 'warn\]|protocol options' | tail -n 12"
} finally { Pop-Location }
