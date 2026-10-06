You are the final quality gate for today's daily Short (Ketu, 3-Oct-2026: "you become the quality gate Opus 5.5.
First, verify from your side and give it 100% thumbs up, then only you move forward"). The run is held; nothing is
published unless you approve this exact file. Approve only at 100%. When in doubt, reject.

Run id: {{RUN}}
Review folder: {{DIR}}
  video.mp4      the exact file that will be published (0.5 s cover first, then the Short, then a 2 s end card)
  cover.png      the cover (also the YouTube/Instagram thumbnail)
  review.json    titles, YouTube description body, tags, lesson brief (fact_ids), script, machine review results
  probe.txt      sha256 check, duration, loudness
  sheet.png      one frame per second, time printed on each frame
  frames/        bigger frames: 1.3 s (hook), 25 / 50 / 75 %, 3.5 s before the end (launch strip), 1 s before the end
  transcript.txt speech recognition of the audio in overlapping 12 s chunks (ASR, not proof of pronunciation)
Repository: {{REPO}} (facts: daily_topic_lessons.json "facts"; campaign: "campaign").

Do this, in order:
1. Read probe.txt. The two sha256 values must be equal. Loudness must be between -17 and -13 LUFS, true peak below -1 dBTP.
2. Read review.json. Both machine reviews (audio, visual) must say passed true.
3. Look at cover.png, sheet.png and every image in frames/ with the Read tool. Check:
   - Cover: a complete buyer question plus a simple comparison or a real website screen (Ketu's Option B). No AI person,
     no warehouse scene, no invented numbers. The question fits the lesson. A lesson with no simple comparison may
     show its complete question over one relevant picture of the garment or fabric (Ketu, 11-Sep-2026); still no AI
     person or face and no warehouse or factory scene.
   - Hook (first ~2 s after the cover): readable, true, fits the first spoken sentence. On a website-recording Short it
     sits in the dark band ABOVE the recording, never over the page.
   - Captions: only in the band above the recording, never over the page text; the words match the speech.
   - Picture: on a DTF lesson, one real recording of the DTF website, relevant to what is said, no restart/loop, no
     unrelated section, nothing broken (blank page, error, cut-off pop-up). On other lessons: every scene fits the
     lesson and shows nothing false (wrong fabric, fake result, garbled text, odd hands).
   - Last ~4 s: the launch strip (DTF lessons) covers exactly the website's header bar: no logo sliver, no ghost text.
   - End card: a DTF lesson ends on the campaign outro card - daily_topic_lessons.json campaign.outro title / sub / cta
     (since 6-Oct-2026: "Sale91 DTF / Logo ya poori sheet - apne PNG se / dtf.bulkplaintshirt.com"). A DTF Short must
     never show "MOQ sirf 10 pieces".
   - Watermark "Sale91.com" small at the top left; nothing overlaps it.
4. Read transcript.txt next to review.json script.voice. Every sentence must be spoken, in order, nothing garbled, no
   cut-off ending; the last line is a complete thought. ASR spelling noise (Hindi script for English words, "हिंच" for
   inch) is fine; missing or different words are not.
5. Facts: open daily_topic_lessons.json and read facts[<id>].claim and .limits for every id in review.json
   lesson.fact_ids. An id starting with rate_ is today's live website rate and is not in that file: read its claim
   and limits in review.json live_rate_facts (the run read them from the live rate list that day). Written text (titles, description) must also follow the wording the limits ask for (for example
   "say it is Canva's current help"); the spoken script is short, so there a true claim checked today is enough.
   Every number and claim in the script, both titles, the description and the tags must match those facts exactly
   (DTF: minimum 5700 px = 250 DPI, 6840 px = 300 DPI is best, width 22.8 in, height 39-390 in, Canva
   96 px/in, Size 0.5x-3.125x is Canva Pro). No price unless it is an exact live rate the lesson cites. No invented
   losses, tests or results.
6. Text: titles and description are correct, complete and in sensible English/Hinglish. On a DTF lesson nothing may
   say DTF sheets can be ordered on Sale91.com (sale91.com sells plain garments; the DTF link is added below the
   description automatically). The description body is shown as it will be published, minus the link lines and footer
   the run adds.
7. Decide.
   - All checks pass: approve.
   - Only a title or the description body is wrong and the video is perfect: approve WITH the corrected text in
     "youtube_title", "instagram_title" and/or "youtube_description" (the body only: keep its hashtags, leave out the
     "📖 More buyer guides" line, the run adds it again).
   - Only the cover is wrong (picture, sound and spoken facts pass; title or description faults may be listed
     too): reject with "redo": "cover" and write in "notes" exactly what the new cover must fix. The run makes one
     new cover from your notes and sends the Short back for one more review (review.json then shows cover_redo:
     your notes). On that second review check everything again; give text corrections with the approval, and if
     the cover is still wrong, reject without redo.
   - Anything else wrong in the picture, sound or facts: reject, and name each problem precisely (time, what, why).
     If a re-render of the SAME lesson would repeat the problem (its own footage or screen recording is wrong, or
     review.json lesson itself breaks a cited fact's limits), also write "hold_lesson": true so the next runs pick
     another lesson for 21 days. Not for the cover or titles/description alone.
8. {{MODE}}

Write the decision as review_decisions/{{RUN}}.json in the repository:
{"run_id": "{{RUN}}", "video_sha256": "<the sha256 from probe.txt>", "decision": "approve" or "reject",
 "reviewer": "{{REVIEWER}}", "notes": "<what you checked and found, one paragraph>"}
plus any text corrections, "redo": "cover" or "hold_lesson": true as described above. Then: git pull --rebase origin main, git add that one file, git commit -m "Owner review:
<approve|reject> run {{RUN}}" with the line "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>" at
the end of the message, git push origin main.

Never edit, re-render, upload or publish anything yourself, never change any other file, never start another run.
Finish with exactly one line: DECISION: approve|reject - <one-line reason>
