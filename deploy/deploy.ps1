# Package the repo and install it on the 208 server.  ./deploy/deploy.ps1 [-SetMailKey]
param([switch]$SetMailKey)
$ErrorActionPreference = 'Stop'
$Repo = Split-Path $PSScriptRoot -Parent
$Server = 'administrator@208.110.72.147'
$tar = Join-Path $env:TEMP 'freeengine-release.tar.gz'
Push-Location $Repo
try {
    tar -czf $tar --exclude '__pycache__' server voices web deploy
    scp -o BatchMode=yes -q $tar "${Server}:/tmp/freeengine-release.tar.gz"
    scp -o BatchMode=yes -q deploy/install.sh "${Server}:/tmp/freeengine-install.sh"
    $cmd = "sed -i 's/\r$//' /tmp/freeengine-install.sh && sudo -n bash /tmp/freeengine-install.sh /tmp/freeengine-release.tar.gz; rm -f /tmp/freeengine-release.tar.gz /tmp/freeengine-install.sh"
    if ($SetMailKey) {
        . "$PSScriptRoot/keys.ps1"
        (Get-Key mailersend) | ssh -o BatchMode=yes $Server $cmd
    } else {
        ssh -o BatchMode=yes $Server "$cmd < /dev/null"
    }
} finally { Pop-Location }
