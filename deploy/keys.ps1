# Read one value from F:/work/keys.txt without printing it.  . ./deploy/keys.ps1; $t = Get-Key cloudfflare
function Get-Key([string]$Name, [string]$File = 'F:/work/keys.txt') {
    foreach ($l in Get-Content $File) {
        if ($l -match '^\s*([^=:]+?)\s*[=:]\s*(.*?)\s*$' -and $Matches[1] -eq $Name) {
            $v = $Matches[2].Trim('"', "'")
            if ($v -match '(\S{20,})') { return $Matches[1] }  # first long token on the line
            return $v
        }
    }
    throw "Key '$Name' not found"
}
