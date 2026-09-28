# Production monitoring

Run `python3 scripts/monitor-production.py` from an operator checkout with the
`hetzner` SSH alias configured. It reads production health, containers, recent
job states, effective provider settings and OpenRouter balances. Credentials are
read only inside the production backend container and are never returned.
It does not submit jobs, change services, send email or purchase credits.

OpenRouter's key spending allowance is separate from account funds. Available
account funds are total credits minus total usage. Alert on the lower known
amount; unavailable values are unknown rather than zero.

VideoScale bandwidth is checked separately through its authenticated usage
page. Do not infer current bandwidth from video file sizes: cached downloads
may be billed differently.

The owner requested a Codex task heartbeat every 30 minutes. It checks for new
failed jobs, unavailable services, provider configuration drift, jobs queued
longer than 15 minutes, processing with no progress for 45 minutes, and jobs
running over three hours. Low-balance thresholds are 2 GB for VideoScale and
$5 for OpenRouter. Historical failures are baselined; unchanged observations
do not generate repeated alerts. Access failures and recoveries are reported.

This is an operator-workstation monitor. Keep Codex running and the workstation
awake for scheduled checks; a signed-out VideoScale browser session prevents
its balance check. This is not an independent always-on uptime service.
Purchases, deployments, refunds and customer communications require approval.
