# Reviewing a daily AI Short for MAIN

The daily workflow's `rendered-video-backup` artifact now lasts 21 days. The next runs save `review_manifest.json`, a retained cover and the original MP4 together. The manifest records the exact script/TTS input, video and cover hashes, BOT YouTube and Instagram IDs, workflow run, and rendering flags. Earlier artifacts still have their original expiry and may lack the manifest or cover.

Download the selected run's artifact while it remains available. Check every asset against its manifest hash, inspect the full clip and spoken words, and verify current Instagram/BOT metrics through the recorded IDs. An artifact from a failed or test run is not a published candidate. Missing cover/IDs/metrics are recorded as missing, not inferred. A manifest is an archive, not editorial or publication approval.

MAIN selection is occasional: roughly one strong candidate per 7–10 daily AI Shorts, with no forced selection when none meets the bar. Compare organic retention and buyer-relevant saves, shares and comments after similar exposure time. High reach alone does not validate a fabricated claim. Recheck the complete script, title, current product facts, spoken Hindi and render quality. Preserve the real-footage release schedule and check MAIN for an existing upload before any transfer.

AI video/music needs the platform's disclosure before release. This change creates no MAIN promotion job, uploads no additional social posts and changes no generation models. It makes a later evidence-based selection possible without losing the source export after three days.

Undo: revert the archive integration, helper and artifact configuration together. Previously created GitHub artifacts retain their own expiry; reverting the workflow does not delete them.
