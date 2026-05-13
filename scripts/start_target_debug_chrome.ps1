$chrome = "${env:ProgramFiles}\Google\Chrome\Application\chrome.exe"
if (-not (Test-Path -LiteralPath $chrome)) {
    $chrome = "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe"
}
if (-not (Test-Path -LiteralPath $chrome)) {
    throw "Chrome executable was not found."
}

$profile = Join-Path (Get-Location) ".tmp\target-debug-chrome-profile"
New-Item -ItemType Directory -Path $profile -Force | Out-Null

Start-Process -FilePath $chrome -ArgumentList @(
    "--remote-debugging-port=9222",
    "--user-data-dir=$profile",
    "--no-first-run",
    "--new-window",
    "https://www.target.com/account"
)
