#!/bin/bash
# Mac BACKUP for the day's Short (Ketu 5-Oct-2026: cloud first, the laptop only when the cloud has a problem).
# The cloud does the normal day: a Cloudflare cron starts daily_short.yml at 14:30 IST and the run itself dispatches
# ai_final_review.yml, where Claude Opus 5.5 reviews the held file. launchd (com.ketu.dailyshortreview) starts this
# Mon-Sat 15:15 IST: it starts the run only if none exists today, and reviews here (REVIEW.md) only if no cloud
# decision appears within CLOUD_GRACE_MIN minutes of the file being held.
# DRY=1 <run_id>: only prepare and review an already held run, print the decision, write nothing.
set -u
export PATH=/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:$HOME/.local/bin
export TZ=Asia/Kolkata
# The active gh account can be switched by other sessions; ankitgupta780 cannot start runs or notify.
GH_TOKEN=$(gh auth token --user thakyanamtumhara 2>/dev/null) && export GH_TOKEN
REPO=/Users/ankit/Projects/yt-shorts-bot
GHREPO=thakyanamtumhara/yt-shorts-bot
HERE=$REPO/tools/daily_review_agent
STATE=$HOME/.local/state/yt-daily-review
TODAY=$(date +%F)
mkdir -p "$STATE"
DRY=${DRY:-0}
[ "$DRY" = 1 ] || exec >>"$STATE/$TODAY.log" 2>&1
echo "=== $(date '+%F %T IST') start (dry=$DRY)"

notify() {
  [ "$DRY" = 1 ] && { echo "TELEGRAM (dry): $1 | $2"; return; }
  gh workflow run notify-telegram.yml -R thakyanamtumhara/Website-Order-Dashboard -f title="$1" -f message="$2" >/dev/null 2>&1 \
    || echo "telegram notify failed"
}
run_state() { gh run view "$1" -R $GHREPO --json status,conclusion -q '.status+" "+.conclusion' 2>/dev/null; }
held() { aws s3 ls "s3://bulkplaintshirt.com/p/review/$1/review.json" >/dev/null 2>&1; }
todays_run() {
  gh run list -R $GHREPO -w daily_short.yml -L 15 --json databaseId,createdAt,displayTitle \
    --jq "[.[] | select((.displayTitle | endswith(\"(test)\") | not) and ((.createdAt | fromdateiso8601) + 19800 | strftime(\"%Y-%m-%d\")) == \"$TODAY\")] | first | .databaseId // empty"
}

if [ "$DRY" = 1 ]; then
  RUN=${1:?DRY=1 needs a run id}
else
  [ "$(date +%u)" = 7 ] && { echo "Sunday: no daily Short"; exit 0; }
  [ -f "$STATE/$TODAY.done" ] && { echo "already done today"; exit 0; }
  mkdir "$STATE/lock" 2>/dev/null || { echo "another review is running"; exit 0; }
  trap 'rmdir "$STATE/lock"' EXIT
  caffeinate -dimsu -w $$ &
  RUN=$(todays_run)
  if [ -n "$RUN" ] && [ "$(run_state "$RUN" | cut -d' ' -f1)" = completed ]; then
    echo "today's run $RUN already finished; nothing to start"
    touch "$STATE/$TODAY.done"
    exit 0
  fi
  if [ -z "$RUN" ]; then
    gh workflow run daily_short.yml -R $GHREPO -f owner_review=true -f enable_subtitles=true \
      || { notify "Daily Short" "Today's Short could not be started (GitHub). Nothing was posted."; exit 1; }
    for i in $(seq 1 20); do sleep 6; RUN=$(todays_run); [ -n "$RUN" ] && break; done
    [ -n "$RUN" ] || { notify "Daily Short" "Today's Short run did not appear on GitHub. Nothing was posted."; exit 1; }
  fi
  echo "run $RUN"
  for i in $(seq 1 120); do
    held "$RUN" && break
    st=$(run_state "$RUN")
    if [ "${st%% *}" = completed ]; then
      notify "Daily Short" "Today's Short stopped before review ($st). Nothing was posted. Run: https://github.com/$GHREPO/actions/runs/$RUN"
      touch "$STATE/$TODAY.done"
      exit 0
    fi
    sleep 30
  done
  held "$RUN" || { notify "Daily Short" "Today's Short took over 60 min to render; not reviewed. Run: https://github.com/$GHREPO/actions/runs/$RUN"; exit 1; }
  for i in $(seq 1 $((${CLOUD_GRACE_MIN:-25} * 2))); do
    if gh api "repos/$GHREPO/contents/review_decisions/$RUN.json?ref=main" --silent 2>/dev/null; then
      echo "cloud reviewer decided run $RUN; nothing to do here"
      touch "$STATE/$TODAY.done"
      exit 0
    fi
    sleep 30
  done
  echo "no cloud decision for run $RUN after ${CLOUD_GRACE_MIN:-25} min; reviewing on the Mac"
fi

DIR=$STATE/runs/$RUN
rm -rf "$DIR"
bash "$HERE/prep.sh" "$RUN" "$DIR" || { notify "Daily Short" "Review copy of run $RUN could not be prepared; it will time out and nothing is posted."; exit 1; }

if [ "$DRY" = 1 ]; then
  MODE="DRY RUN: do NOT write, commit or push any file. Only finish with the DECISION line."
else
  MODE="Write, commit and push the decision file as described below."
fi
PROMPT=$(python3 "$HERE/prompt.py" "$RUN" "$DIR" "$MODE" "$REPO" "Claude Opus 5.5 (Mac backup reviewer)")
cd "$REPO" && git pull -q --rebase origin main 2>/dev/null
perl -e 'alarm shift; exec @ARGV' 2700 claude -p "$PROMPT" --model claude-opus-5-5 --dangerously-skip-permissions \
  --add-dir "$DIR" > "$DIR/reviewer.txt" 2>&1
DECISION_LINE=$(grep -E "^DECISION:" "$DIR/reviewer.txt" | tail -1)
echo "reviewer: ${DECISION_LINE:-no decision line}"
[ "$DRY" = 1 ] && { cat "$DIR/reviewer.txt"; exit 0; }

git -C "$REPO" fetch -q origin
DEC=$(git -C "$REPO" show "origin/main:review_decisions/$RUN.json" 2>/dev/null | python3 -c "import json,sys; print(json.load(sys.stdin).get('decision',''))" 2>/dev/null)
if [ -z "$DEC" ]; then
  notify "Daily Short" "The AI reviewer gave no decision for run $RUN, so it is held and nothing is posted. ${DECISION_LINE}"
  exit 1
fi

for i in $(seq 1 240); do
  [ "$(run_state "$RUN" | cut -d' ' -f1)" = completed ] && break
  sleep 30
done
LOGTXT=$(gh run view "$RUN" -R $GHREPO --log 2>/dev/null)
TITLE=$(python3 -c "import json;print(json.load(open('$DIR/review.json'))['titles']['youtube'])" 2>/dev/null)
if [ "$DEC" = approve ]; then
  YT=$(grep -o "UPLOADED! https://youtube.com/shorts/[A-Za-z0-9_-]*" <<<"$LOGTXT" | head -1 | cut -d' ' -f2)
  WHEN=$(grep -o "Scheduled: [^│]*IST" <<<"$LOGTXT" | head -1)
  IG=$(grep -c "Instagram Reel published" <<<"$LOGTXT")
  FB=$(grep -c "FB Reel: PUBLISHED" <<<"$LOGTXT")
  TG=$(grep -c "Telegram: posted" <<<"$LOGTXT")
  notify "Daily Short approved" "$TITLE
YouTube: ${YT:-not uploaded} ${WHEN}
Instagram: $([ "$IG" -gt 0 ] && echo posted || echo NOT posted) · Facebook: $([ "$FB" -gt 0 ] && echo posted || echo NOT posted) · Telegram: $([ "$TG" -gt 0 ] && echo posted || echo NOT posted)
${DECISION_LINE#DECISION: }"
else
  notify "Daily Short rejected" "$TITLE
Nothing was posted. ${DECISION_LINE#DECISION: }"
fi
touch "$STATE/$TODAY.done"
echo "=== $(date '+%F %T IST') end"
