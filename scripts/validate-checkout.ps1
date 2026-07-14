param(
    [switch]$SkipCleanup,
    [string]$Region = "us-east-1"
)

$ErrorActionPreference = "Stop"
$FunctionName = "poketracker-prod-checkout-webhook"
$SecretId = "poketracker-prod-checkout-webhook-token"
$Ec2Tag = "poketracker-prod-target-checkout-browser"
$TestSku = "95042532"
$TestUrl = "https://www.target.com/p/spaghettios-original-pok-233-mon-shapes-canned-pasta-15-8oz/-/A-95042532"

function Write-Step($Text) { Write-Host "`n$Text" }
function Invoke-AwsJson($Arguments) {
    $raw = & aws @Arguments --output json
    if ($LASTEXITCODE -ne 0) { throw "aws $($Arguments -join ' ') failed" }
    $raw | ConvertFrom-Json
}

Write-Host "`nPokeTracker infra check"

Write-Step "[1/3] EC2 Chrome"
$instance = Invoke-AwsJson @(
    "ec2", "describe-instances",
    "--region", $Region,
    "--filters", "Name=tag:Name,Values=$Ec2Tag", "Name=instance-state-name,Values=running",
    "--query", "Reservations[0].Instances[0].{InstanceId:InstanceId,PrivateIp:PrivateIpAddress,LaunchTime:LaunchTime}"
)
if (-not $instance.InstanceId) { throw "EC2 Chrome instance not running" }
Write-Host "  OK $($instance.InstanceId) running at $($instance.PrivateIp)"

Write-Step "[2/3] Lambda verify_only checkout"
$token = & aws secretsmanager get-secret-value --region $Region --secret-id $SecretId --query SecretString --output text
if ($LASTEXITCODE -ne 0 -or -not $token) { throw "could not read webhook token" }

$body = @{
    item = @{
        id = "test-verify"
        name = "SpaghettiOs Pokemon Shapes"
        retailer = "target"
        sku = $TestSku
        url = $TestUrl
    }
    quantity = 1
    observed_price = 1.39
    msrp = 1.99
    verify_only = $true
} | ConvertTo-Json -Depth 8 -Compress

$event = @{
    headers = @{ authorization = "Bearer $token"; "content-type" = "application/json" }
    body = $body
    isBase64Encoded = $false
} | ConvertTo-Json -Depth 8 -Compress

$tmp = New-TemporaryFile
try {
    $started = Get-Date
    & aws lambda invoke `
        --region $Region `
        --function-name $FunctionName `
        --payload $event `
        --cli-binary-format raw-in-base64-out `
        --cli-read-timeout 320 `
        $tmp.FullName | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "lambda invoke failed" }

    $response = Get-Content -Raw $tmp.FullName | ConvertFrom-Json
    $payload = $response.body | ConvertFrom-Json
    $elapsed = [int]((Get-Date) - $started).TotalSeconds
    if ($payload.status -ne "ready_to_place_order") {
        throw "verify_only failed ($($response.statusCode)): $($payload.status) - $($payload.message)"
    }
    Write-Host "  OK $($payload.status) in ${elapsed}s"
}
finally {
    Remove-Item -LiteralPath $tmp.FullName -Force -ErrorAction SilentlyContinue
}

if ($SkipCleanup) {
    Write-Host "`nSkipped cart cleanup"
    exit 0
}

Write-Step "[3/3] Cart cleanup (SSM CDP)"
$clearPy = @'
import json,sys,time,urllib.request
try:
    import websocket
except ImportError:
    import subprocess; subprocess.run(["pip3","install","-q","websocket-client"],check=True); import websocket
pages=json.loads(urllib.request.urlopen("http://127.0.0.1:9223/json").read())
if not pages: print(0); sys.exit(0)
ws=websocket.create_connection(pages[0]["webSocketDebuggerUrl"],timeout=30)
_i=[1]
def c(m,p=None):
    d={"id":_i[0],"method":m,"params":p or {}}
    ws.send(json.dumps(d)); _i[0]+=1
    while True:
        r=json.loads(ws.recv())
        if r.get("id")==d["id"]: return r
c("Page.navigate",{"url":"https://www.target.com/cart"})
time.sleep(5)
js="""(function(){
  var s=["[data-test=\\"cartItem-remove\\"]","button[aria-label*=\\"Remove\\"]","button[aria-label*=\\"remove\\"]"];
  for(var i=0;i<s.length;i++){var b=Array.from(document.querySelectorAll(s[i]));if(b.length){b.forEach(x=>x.click());return b.length;}}
  var a=Array.from(document.querySelectorAll("button")).filter(b=>/^remove$/i.test((b.innerText||b.textContent||"").trim()));
  a.forEach(x=>x.click());return a.length;
})()"""
r=c("Runtime.evaluate",{"expression":js})
print(r.get("result",{}).get("result",{}).get("value",0))
time.sleep(3);ws.close()
'@
$encoded = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($clearPy))
$remoteCommand = "if ! command -v pip3 >/dev/null 2>&1; then apt-get update -qq >/dev/null 2>&1 && apt-get install -y -qq python3-pip >/dev/null 2>&1; fi`npython3 -c 'import websocket' >/dev/null 2>&1 || python3 -m pip install -q websocket-client >/dev/null 2>&1`necho '$encoded' | base64 -d | python3"
$parameters = @{ commands = @($remoteCommand) } | ConvertTo-Json -Compress

$commandId = & aws ssm send-command `
    --region $Region `
    --instance-ids $instance.InstanceId `
    --document-name AWS-RunShellScript `
    --parameters $parameters `
    --timeout-seconds 90 `
    --query "Command.CommandId" `
    --output text
if ($LASTEXITCODE -ne 0 -or -not $commandId) { throw "SSM send-command failed" }

$status = "InProgress"
$invocation = $null
for ($i = 0; $i -lt 18; $i++) {
    Start-Sleep -Seconds 5
    $raw = & aws ssm get-command-invocation --region $Region --command-id $commandId --instance-id $instance.InstanceId --output json 2>$null
    if ($LASTEXITCODE -ne 0 -or -not $raw) { continue }
    $invocation = $raw | ConvertFrom-Json
    $status = $invocation.Status
    if ($status -in @("Success", "Failed", "Cancelled", "TimedOut")) { break }
}
if ($status -ne "Success") { throw "cart cleanup failed with status $status" }
$removed = if ($invocation) { $invocation.StandardOutputContent.Trim() } else { "?" }
Write-Host "  OK removed $removed item(s)"
Write-Host "`nAll checks passed"
