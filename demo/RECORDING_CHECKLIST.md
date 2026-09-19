# Recording checklist

Before **every** take:
- [ ] Shared stack healthy; CloudFront URL loads; token kept off screen.
- [ ] Target EC2 started (`scripts/ec2_start.sh`) and answering on port 80 (`python demo/curl_loop.py <ip>` is green).
- [ ] Target SG `deadman-target-shared` reset: exactly `tcp/80 0.0.0.0/0` open, no leftover change on it (no `SG_BUSY`).
- [ ] Demo TTL = smallest passing T from check 1a (`docs/verification_m1.md`): `______ s`; `MIN_TTL_SECONDS` allows it.
- [ ] Screen 1920x1080, browser zoom 125-150%, terminal font >= 20 pt, notifications off, clean desktop.
- [ ] Windows arranged: UI left, curl loop right; AWS console tabs open (Scheduler group, DynamoDB item explorer) with account id hidden.
- [ ] Microphone test; record voice-over separately if the room is noisy.

After each take:
- [ ] Target SG back to baseline; no stale changes; EC2 stopped if the session is over (`scripts/ec2_stop.sh`).
- [ ] Watch the take once at full speed: ring readable, token hidden, loop goes red then green.

Minimum: **2 full takes**; keep both and pick the better one.

| Take | Date/time | TTL used | Length | Problems | Keep? |
|---|---|---|---|---|---|
| 1 | | | | | |
| 2 | | | | | |
