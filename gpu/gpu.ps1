# Control the on-demand L40S GPU worker through AWS (no SSH, no open ports).
#   ./gpu/gpu.ps1 start | stop | status
#   ./gpu/gpu.ps1 run <repo-relative script> [timeoutSeconds]
#   ./gpu/gpu.ps1 pull <repo-relative folder>
param(
    [Parameter(Mandatory)][ValidateSet('start', 'stop', 'status', 'run', 'pull')][string]$Cmd,
    [string]$Arg,
    [int]$Timeout = 14400
)
$ErrorActionPreference = 'Stop'
$Id = 'i-04f4aa002f1c858b2'
$Region = 'us-east-1'
$Bucket = 'gpu-worker-132442190413'
$Prefix = "s3://$Bucket/thefreeengine"
$Repo = Split-Path $PSScriptRoot -Parent

function State { aws ec2 describe-instances --region $Region --instance-ids $Id --query 'Reservations[0].Instances[0].State.Name' --output text }

switch ($Cmd) {
    'status' { State }
    'start' {
        for ($i = 1; $i -le 20; $i++) {
            aws ec2 start-instances --region $Region --instance-ids $Id --output text 2>&1 | Out-Null
            if ($LASTEXITCODE -eq 0) { break }
            Write-Host "No L40S capacity yet (try $i/20), waiting 60s..."
            Start-Sleep 60
        }
        if ($LASTEXITCODE -ne 0) { throw 'Could not start the GPU worker: no capacity.' }
        aws ec2 wait instance-running --region $Region --instance-ids $Id
        Write-Host 'Running. Waiting for SSM agent...'
        for ($i = 0; $i -lt 40; $i++) {
            $p = aws ssm describe-instance-information --region $Region --filters "Key=InstanceIds,Values=$Id" --query 'InstanceInformationList[0].PingStatus' --output text
            if ($p -eq 'Online') { Write-Host 'Ready.'; return }
            Start-Sleep 10
        }
        throw 'SSM agent did not come online.'
    }
    'stop' {
        aws ec2 stop-instances --region $Region --instance-ids $Id --query 'StoppingInstances[0].CurrentState.Name' --output text
    }
    'run' {
        if (-not $Arg) { throw 'Give a repo-relative script, e.g. gpu/setup.sh' }
        aws s3 sync $Repo "$Prefix/repo" --region $Region --delete --only-show-errors `
            --exclude '.git/*' --exclude '*/.venv/*' --exclude 'site/samples/*' --exclude 'voices/*.safetensors'
        $script = $Arg -replace '\\', '/'
        $cmds = @(
            'set -e',
            'mkdir -p /opt/tfe && cd /opt/tfe',
            "aws s3 sync $Prefix/repo /opt/tfe --only-show-errors --delete --exclude 'out/*'",
            "bash /opt/tfe/$script 2>&1 | tee /opt/tfe-last.log",
            'exit ${PIPESTATUS[0]}'
        )
        $params = @{ commands = $cmds; executionTimeout = @("$Timeout") } | ConvertTo-Json -Compress
        $f = New-TemporaryFile
        Set-Content $f $params -Encoding utf8
        $cid = aws ssm send-command --region $Region --instance-ids $Id --document-name AWS-RunShellScript `
            --parameters "file://$f" --timeout-seconds 600 `
            --output-s3-bucket-name $Bucket --output-s3-key-prefix thefreeengine/ssm-logs `
            --query 'Command.CommandId' --output text
        Remove-Item $f
        Write-Host "Command $cid sent. Waiting..."
        do {
            Start-Sleep 20
            $st = aws ssm get-command-invocation --region $Region --command-id $cid --instance-id $Id --query Status --output text 2>$null
        } while ($st -in @('Pending', 'InProgress', 'Delayed', ''))
        $log = "$env:TEMP\tfe-$cid.log"
        aws s3 cp "s3://$Bucket/thefreeengine/ssm-logs/$cid/$Id/awsrunShellScript/0.awsrunShellScript/stdout" $log --region $Region --only-show-errors 2>$null
        if (Test-Path $log) { Get-Content $log -Tail 60 }
        aws ssm get-command-invocation --region $Region --command-id $cid --instance-id $Id --query StandardErrorContent --output text
        Write-Host "Status: $st"
        if ($st -ne 'Success') { exit 1 }
    }
    'pull' {
        if (-not $Arg) { throw 'Give a folder, e.g. voices' }
        aws s3 sync "$Prefix/out/$Arg" (Join-Path $Repo $Arg) --region $Region --only-show-errors
        Write-Host "Pulled $Prefix/out/$Arg"
    }
}
