# Approved personal Instagram release

The queue holds a real recorded video, with no generated speech. It does not use or change the reviewed-AI queue.

An active job is AES-GCM encrypted with `PERSONAL_INSTAGRAM_RELEASE_KEY` (32 random bytes encoded as hex). The public envelope binds ID, due time and SHA256 of the decoded job. `id` is the authenticated associated data. The complete job includes the exact user-approved video and cover SHA256, caption, release deadline and link dependencies. Do not commit its plaintext or private screenshot evidence.

Before create and immediately before publish, the runner checks the approved YouTube ID/channel, public/processed/embeddable state, India and age restrictions, anonymous exact-video oEmbed, current Instagram account and primary store URL, and the exact reviewed first-party landing bytes with a `full-story-video` link and store link. The native secondary link is evidenced by a reviewed screenshot bound into the encrypted job. Graph's `website` readback only covers the primary link; the runner does not pretend it can re-read the secondary native link on publication day.

The date is an eligibility window, not a native Instagram scheduled reservation. GitHub runs at 17:00 IST and may start late; release stops after the three-hour window. Failed checks open a repository issue without personal content. Held envelopes do not create any Instagram object. An unavailable original story must never be bypassed.

## Undo and recovery

Before the due window, replace this exact job envelope with status `held` or remove it, then push. Preserve S3 request state: never erase it to try again. A container is created only when the release is due and dependencies pass. An uncertain create or publish is retained as pending and cannot automatically repeat. Saved published IDs may only be rechecked. Removing the job does not retract a published Reel; after actual publication, use its exact saved ID/native UI with owner verification for an explicitly authorized removal.

The first-party page is a single S3 object. Back up an existing object before overwriting; when creating a new one, record its absent state and exact resulting ETag/hash. Undo deletes only that unchanged new key, or restores the exact backup. Native link undo removes only the added secondary link and preserves the original store link/order. No existing video, daily bot, warehouse queue or site-wide deployment is changed.
