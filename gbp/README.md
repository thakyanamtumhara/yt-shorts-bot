# Google Business photos (gbp/)

Website photos and videos go to the Google Business Profile by themselves, through Google's official Business
Profile API. No browser is driven.

- Input: `https://www.bulkplaintshirt.com/phphotos.js` (`PH_PHOTOS`, `PH_VIDEOS`), the website's own gallery list.
- `state.json`: every item ever seen (`baseline` = was on the site on 19-Nov-2026, `uploaded`, `skipped: why`).
- **New items** (on the website after 19-Nov-2026) go up first, with a Telegram at once.
- **Older gallery** (`backlog.json`, since 20-Nov-2026): an ordered list of older items, each one looked at before it
  was listed (winter products and front views first). 3 photos + 1 video of it a day, and only while the item is
  still on the website and its product + colour are still in the live catalogue (the workflow's first step reads
  the catalogue with no keys in reach; if it cannot be read, the older items wait). One Telegram a week (Monday),
  one when the list is finished. Left out on purpose: closing-out lots, closed products, colours that are not
  coming back, anything already on the profile, a printed sample, clips over 30 s, clips with a readable vehicle
  number plate. To take an item out: delete its entry from `backlog.json`.
- A day's ceiling for everything together: 6 photos + 1 video (`GBP_MAX_PHOTOS`, `GBP_MAX_VIDEOS`, `GBP_OLD_PHOTOS`,
  `GBP_OLD_VIDEOS`); a second run on the same day never goes above it. Daily run booked for 11:20 IST; GitHub often starts
  it hours late (21 and 22-Nov-2026: about 19:10 IST), which is fine. The weekly note goes on Monday, or on the first
  run after it when Monday's run starts past midnight IST.
- Photos (600 px or more) go up as JPG copies, category ADDITIONAL like every photo already on the profile, with
  "Product, Colour" as the description; a video must fit Google's limits (30 s, 75 MB, 720p).
- Never uploaded: Varsity (closed) and any gallery with `sale` in its name.
- Telegram: new uploads, the weekly older-gallery note, a failure, a blind run (the photo list could not be read),
  and a still-running note on the 1st.
- Secrets: `GBP_CLIENT_ID`, `GBP_CLIENT_SECRET`, `GBP_REFRESH_TOKEN` (the business's own OAuth client, scope
  business.manage). This repo is public: no profile ids, keys or case numbers go into any file here.
- By hand: Actions -> "Google Business photos" -> Run workflow -> `dry` (only prints) / `run` / `whoami`.
- Google refuses the saved permission (Telegram says so): `mint_token.py` makes a new one; the owner presses Allow.
- Stop everything: disable the workflow in Actions. Stop only the older gallery: empty `items` in `backlog.json`.
  Remove an uploaded photo: Business Profile -> Photos (owner).
