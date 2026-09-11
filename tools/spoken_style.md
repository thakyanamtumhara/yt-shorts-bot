Prepare a new private dialogue script with the real-recording wording reference:

```sh
python tools/spoken_style.py --prompt
python tools/spoken_style.py --check /path/to/new-drafts.json
```

Use `--script-system roman` for the daily script writer. The checker accepts plain
text or JSON containing `script` / `script_voice` fields, including episode arrays.
It is read-only, makes no provider calls and never rewrites text. A failure means
rewrite the sentence before generating its audio; preserve the intended meaning.

The reference contains eight normalized fragments from three real recordings.
Their source IDs, original-raw time ranges and excerpt hashes are stored in
`spoken_style_reference.json`. Do not replace them with generated dialogue,
daily BOT scripts, video descriptions or owner sales replies. These examples
ground a prompt; they do not train a model or prove pronunciation.

Only the specifically rejected formal words and close spellings trigger the
vocabulary gate. Useful technical words remain allowed and should be explained
simply. Passing vocabulary does not approve facts, audio delivery or publication.
Existing selected videos retain their exact scripts, audio and media hashes.

Speech ingestion checks MAIN channel membership and native synthetic disclosure
before captions or transcription. The explicit exclusion ledger always wins,
including cached corpus files. An omitted native field is unknown: only an
original recording explicitly reviewed in the reference may pass without it.
New unknown sources stay out until reviewed. Metadata errors stop ingestion;
these safeguards do not detect every undeclared synthetic upload.
