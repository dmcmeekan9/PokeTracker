#!/usr/bin/env bash
# PokeTracker checkout infrastructure validation
#
# Runs a verify-only purchase through the full AWS pipeline (~90s, no tokens).
# Confirms EC2 Chrome is live, Target session is valid, and the checkout
# Lambda can reach the Place Order button without clicking it. Clears the
# test cart item from the persistent browser afterwards via SSM CDP.
#
# Usage:
#   bash scripts/validate-checkout.sh              # full check
#   bash scripts/validate-checkout.sh --skip-cleanup   # skip SSM cart clear

set -euo pipefail

REGION="${AWS_REGION:-us-east-1}"
FUNCTION="poketracker-prod-checkout-webhook"
SECRET_ID="poketracker-prod-checkout-webhook-token"
EC2_TAG="poketracker-prod-target-checkout-browser"

# Test item: SpaghettiOs Pokemon Shapes 15.8oz — $1.39, sold by Target, reliably in stock.
# Verified 2026-05-14. If this SKU ever 404s, replace with another cheap Target-sold item.
TEST_SKU="95042532"
TEST_URL="https://www.target.com/p/spaghettios-original-pok-233-mon-shapes-canned-pasta-15-8oz/-/A-95042532"
TEST_PRICE="1.39"
TEST_MSRP="1.99"

SKIP_CLEANUP="${1:-}"

# ── helpers ──────────────────────────────────────────────────────────────────────
ok()   { printf "  ✓ %s\n" "$*"; }
fail() { printf "\n  ✗ %s\n\n" "$*" >&2; exit 1; }
step() { printf "\n[%s/3] %s\n" "$1" "$2"; }
py()   { command -v python3 &>/dev/null && python3 "$@" || python "$@"; }

T0=$(date +%s)
echo ""
echo "━━━ PokeTracker infra check ━━━"

# ── 1. EC2 Chrome ────────────────────────────────────────────────────────────────
step 1 "EC2 Chrome"
INSTANCE_ID=$(aws ec2 describe-instances \
  --region "$REGION" \
  --filters "Name=tag:Name,Values=$EC2_TAG" "Name=instance-state-name,Values=running" \
  --query "Reservations[0].Instances[0].InstanceId" \
  --output text 2>/dev/null || echo "")
[[ "$INSTANCE_ID" == "None" || -z "$INSTANCE_ID" ]] && fail "EC2 Chrome instance not running — check AWS console or redeploy"
ok "$INSTANCE_ID running"

# ── 2. Checkout verify-only ──────────────────────────────────────────────────────
step 2 "Lambda verify_only checkout"
TOKEN=$(aws secretsmanager get-secret-value \
  --region "$REGION" \
  --secret-id "$SECRET_ID" \
  --query SecretString --output text)

BODY=$(printf \
  '{"item":{"id":"test-verify","name":"SpaghettiOs Pokemon Shapes","retailer":"target","sku":"%s","url":"%s"},"quantity":1,"observed_price":%s,"msrp":%s,"verify_only":true}' \
  "$TEST_SKU" "$TEST_URL" "$TEST_PRICE" "$TEST_MSRP")
ENCODED=$(py -c "import json,sys; print(json.dumps(sys.stdin.read()))" <<< "$BODY")
PAYLOAD=$(printf \
  '{"headers":{"authorization":"Bearer %s","content-type":"application/json"},"body":%s,"isBase64Encoded":false}' \
  "$TOKEN" "$ENCODED")

RESP=$(mktemp)
T1=$(date +%s)
aws lambda invoke \
  --region "$REGION" \
  --function-name "$FUNCTION" \
  --payload "$PAYLOAD" \
  --cli-binary-format raw-in-base64-out \
  --cli-read-timeout 320 \
  "$RESP" > /dev/null

# Parse status — exits non-zero and prints the error if anything other than ready_to_place_order
PARSE_RESULT=$(py -c "
import json, sys
with open(sys.argv[1]) as f:
    r = json.load(f)
b = json.loads(r['body'])
if b['status'] != 'ready_to_place_order':
    print(f\"FAIL ({r['statusCode']}): {b['status']} — {b['message']}\", file=sys.stderr)
    sys.exit(1)
print(b['status'])
" "$RESP" 2>&1) || { rm -f "$RESP"; fail "$PARSE_RESULT"; }
rm -f "$RESP"

ok "$PARSE_RESULT ($(($(date +%s) - T1))s)"

# ── 3. Cart cleanup (SSM CDP) ─────────────────────────────────────────────────────
if [[ "$SKIP_CLEANUP" != "--skip-cleanup" ]]; then
  step 3 "Cart cleanup (SSM CDP)"

  # Python script that runs on the EC2 instance via SSM.
  # Connects to Chrome (127.0.0.1:9223), navigates to the Target cart,
  # and clicks all Remove buttons.
  CLEAR_PY='import json,sys,time,urllib.request
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
n=r.get("result",{}).get("result",{}).get("value",0)
print(n)
time.sleep(3);ws.close()'

  B64=$(printf '%s' "$CLEAR_PY" | base64 | tr -d '\n')
  SSM_CMD="{\"commands\":[\"apt-get install -y -qq python3-pip 2>/dev/null; pip3 install -q websocket-client 2>/dev/null; echo '$B64' | base64 -d | python3\"]}"

  CMD_ID=$(aws ssm send-command \
    --region "$REGION" \
    --instance-ids "$INSTANCE_ID" \
    --document-name "AWS-RunShellScript" \
    --parameters "$SSM_CMD" \
    --timeout-seconds 90 \
    --output text \
    --query "Command.CommandId")

  SSM_STATUS="InProgress"
  INV="{}"
  for _ in $(seq 10); do
    sleep 5
    INV=$(aws ssm get-command-invocation \
      --region "$REGION" \
      --command-id "$CMD_ID" \
      --instance-id "$INSTANCE_ID" \
      --output json \
      --query "{s:Status,o:StandardOutputContent}" 2>/dev/null || echo "{}")
    SSM_STATUS=$(py -c "import json,sys; r=json.loads(sys.argv[1]); print(r.get('s','InProgress'))" "$INV" 2>/dev/null || echo "InProgress")
    [[ "$SSM_STATUS" == "Success" || "$SSM_STATUS" == "Failed" ]] && break
  done

  N=$(py -c "import json,sys; r=json.loads(sys.argv[1]); print(r.get('o','0').strip())" "$INV" 2>/dev/null || echo "?")
  if [[ "$SSM_STATUS" == "Success" ]]; then
    ok "${N:-0} item(s) removed"
  else
    printf "  ⚠ cart cleanup returned %s — may need manual clear at target.com/cart\n" "$SSM_STATUS"
  fi
fi

echo ""
echo "━━━ All checks passed ($(($(date +%s) - T0))s) ━━━"
echo ""
