# Google Business photos (gbp/)

New website photos and videos go to the Google Business Profile by themselves, through Google's official Business
Profile API. No browser is driven.

- Input: `https://www.bulkplaintshirt.com/phphotos.js` (`PH_PHOTOS`, `PH_VIDEOS`), the website's own gallery list.
- `state.json`: every item ever seen. Items on the site on 19-Nov-2026 are `baseline` and are never uploaded.
- A run uploads at most 6 photos and 1 video (`GBP_MAX_PHOTOS`, `GBP_MAX_VIDEOS`), once a day (11:20 IST). More
  than that waits for the next day. Photos go up as JPG copies (category ADDITIONAL, like every photo already on the profile) with "Product, Colour" as the
  description; videos must fit Google's limits (30 s, 75 MB, 720p) or are skipped with a Telegram.
- Never uploaded: Varsity (closed) and any gallery with `sale` in its name.
- Telegram: what went up, a failure, a blind run (the list could not be read), and a still-running note on the 1st.
- Secrets: `GBP_CLIENT_ID`, `GBP_CLIENT_SECRET`, `GBP_REFRESH_TOKEN` (the business's own OAuth client, scope
  business.manage). This repo is public: no profile ids, keys or case numbers go into any file here.
- By hand: Actions -> "Google Business photos" -> Run workflow -> `dry` (only prints) / `run` / `whoami`.
- Google refuses the saved permission (Telegram says so): `mint_token.py` makes a new one; the owner presses Allow.
- Stop it: disable the workflow in Actions. Remove an uploaded photo: Business Profile -> Photos (owner).
