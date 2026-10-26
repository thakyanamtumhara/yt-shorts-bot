# quora-trigger: start the daily Quora answer on time

**Why (26-Oct-2026):** GitHub's own cron in `.github/workflows/quora_answers.yml` started the 08:17 IST run about 5 h
late every day (19-24 Oct: 13:06-13:36 IST; the 10:47 catch-up 15:05-16:20), after the Post Time alarm (11-12 IST).
The page shows only the newest day, so the person posting always got the day before's answer. Ketu wants "first to
respond".

**How:**
- Cloudflare Worker `quora-trigger` (this folder), crons `47 2 * * MON-SAT` (08:17 IST -> workflow_dispatch
  action `send`) and `17 5 * * MON-SAT` (10:47 IST -> action `catchup`: writes it only if 08:17 did not, and tells Ketu).
- GitHub's own two schedules stay as late backups, both in catchup mode: a day already written is skipped
  ("today's answer is already written": no page rebuild, no Telegram); a day the Worker's runs missed is written and
  Ketu is told it was late. That is the "did not run" alert.
- GitHub refuses the start (3 tries 20 s apart; 401/403/404/422 are not retried) or a secret is missing -> Telegram to
  Ketu's private chat (WWbun's bot + chat, the same pair payerr-watch uses). A run that starts and then fails sends the
  workflow's own "Today's Quora answer did not go out".
- It stores nothing. Cloudflare cron day names are used (MON-SAT) so the weekday numbering cannot be misread.

**Deploy:**
```sh
cd quora_trigger_worker && node --test worker.test.mjs
wrangler deploy --config wrangler.jsonc
python3 secret-install.py        # QA_GH_TOKEN + TG_TOKEN + TG_CHAT, values never printed
wrangler tail quora-trigger --format json    # a tick logs {"quora_trigger":{...},"cron":"..."}
```
Run `python3 secret-install.py` again after `gh auth login` is redone for thakyanamtumhara, or after the Telegram bot
token is rotated (BotFather /revoke, new token in WWbun's Railway variables).

**Test:** a cron that is not one of the two slots starts a normal `send` (used once for the live test, then removed).

**Undo:**
```sh
wrangler delete quora-trigger    # GitHub's schedules still write the answer, ~5 h late, as before
```
To also give the GitHub schedules back their old send/catchup split, revert the commit that added this folder.
