# DTF demo recordings (assets/dtf_demo)

The seven real-screen clips of the DTF order form used by the DTF launch Shorts (one per fact in
daily_topic_lessons.json campaign.clips). Made 3-Oct-2026 on this Mac. Re-record when the DTF form changes.

Never upload the demo sheets to the live site: every upload there creates a live draft order. Record from a
LOCAL copy of dtf-app with the live price settings (stack.mjs sets price_per_metre_paise 15500, gst_bp 1800,
delivery courier/bike/drop) - check `curl -s https://dtf.bulkplaintshirt.com/api/meta` first and copy any
changed value into stack.mjs.

```
export REEL_DIR=/tmp/dtf-reel; mkdir -p $REEL_DIR/files
python3 tools/dtf_demo_recorder/make_files.py          # demo PNG sheets (PIL)
cd /Users/ankit/Projects/dtf-app
E2E_PORT=48731 E2E_RATES_PORT=48732 E2E_GEO_PORT=48733 E2E_BROKER_PORT=48734 E2E_DB_NAME=dtf_reel \
  E2E_DATA_DIR=$REEL_DIR/data node /Users/ankit/Projects/yt-shorts-bot/tools/dtf_demo_recorder/stack.mjs &
node /Users/ankit/Projects/yt-shorts-bot/tools/dtf_demo_recorder/rec.mjs transparent resolution canva pieces gang press launch
cp $REEL_DIR/clips/*.mp4 /Users/ankit/Projects/yt-shorts-bot/assets/dtf_demo/
kill %1    # stops the local stack this started (only that one)
```
rec.mjs uses Playwright from /Users/ankit/Projects/cc1/node_modules; ports 48731-48734 must be free (pick
others if not; never stop another process to free a port). Review every clip frame by frame before committing.
