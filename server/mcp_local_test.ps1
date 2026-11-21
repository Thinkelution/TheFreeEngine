# Start a local test copy, run the official MCP SDK client against /mcp, then stop it.
$ErrorActionPreference = 'Stop'
$py = 'C:\Users\vivek\.kiro\crew\workspace\dan-demo\.venv\Scripts\python.exe'
$d = Join-Path ($env:KIROCREW_SCRATCH ?? $env:TEMP) 'tfe-mcp-local'
Remove-Item -Recurse -Force $d -ErrorAction SilentlyContinue; New-Item -ItemType Directory $d | Out-Null
$env:TFE_DATA = $d; $env:TFE_VOICES = (Resolve-Path "$PSScriptRoot\..\voices").Path; $env:TFE_MAIL_LOG = "$d\mail.log"
$env:TFE_COOKIE_SECURE = '0'; $env:TFE_BASE_URL = 'http://127.0.0.1:8713'
$p = Start-Process $py -ArgumentList '-m', 'uvicorn', 'app:app', '--host', '127.0.0.1', '--port', '8713' -WorkingDirectory $PSScriptRoot `
    -RedirectStandardError "$d\app.log" -RedirectStandardOutput "$d\out.log" -PassThru -WindowStyle Hidden
try {
    $U = 'http://127.0.0.1:8713'
    for ($i = 0; $i -lt 90; $i++) { try { Invoke-RestMethod "$U/v1/health" | Out-Null; break } catch { Start-Sleep 2 } }
    $H = @{ 'X-TFE' = '1' }
    Invoke-RestMethod "$U/auth/signup" -Method Post -Headers $H -ContentType 'application/json' -Body '{"email":"c@example.com","password":"password123"}' | Out-Null
    Start-Sleep 1
    $tok = [regex]::Match((Get-Content "$d\mail.log" -Raw), 'token=([A-Za-z0-9_-]+)').Groups[1].Value
    Invoke-RestMethod "$U/auth/verify" -Method Post -Headers $H -ContentType 'application/json' -Body "{`"token`":`"$tok`"}" -SessionVariable s | Out-Null
    $key = (Invoke-RestMethod "$U/api/keys" -Method Post -Headers $H -ContentType 'application/json' -Body '{"name":"sdk"}' -WebSession $s).key
    & $py "$PSScriptRoot\mcp_client_test.py" "$U/mcp" $key
} finally {
    Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue
    Select-String -Path "$d\app.log" -Pattern 'Traceback|ERROR' | Select-Object -First 3
}
