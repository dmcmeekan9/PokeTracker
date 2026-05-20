Guide the user through refreshing the Target session and uploading it to Secrets Manager.

The session refresh is fully automated — no browser or VNC needed. Run the session refresh Lambda, which signs in to EC2 Chrome using credentials from Secrets Manager and saves the updated session.

Steps:
1. Invoke the session refresh Lambda:
```bash
aws lambda invoke \
  --function-name poketracker-prod-target-session-refresh \
  --cli-binary-format raw-in-base64-out \
  --payload '{}' \
  --region us-east-1 \
  /tmp/refresh.json && cat /tmp/refresh.json
```
Expected response: `{"status":"refreshed","message":"Target CDP browser session refreshed"}`

2. Re-warm the EC2 Chrome tabs with the fresh session:
```bash
aws lambda invoke \
  --function-name poketracker-prod-tab-warmer \
  --cli-binary-format raw-in-base64-out \
  --payload '{}' \
  --region us-east-1 \
  /tmp/warmer.json && cat /tmp/warmer.json
```

3. Run `/check-infra` to confirm the session works end-to-end.

If the session refresh Lambda returns an error (e.g. `sign_in_required` or `identity_verification`): Target may have triggered a CAPTCHA or MFA challenge, which has not occurred in practice. Use `/vnc` as a last resort.
