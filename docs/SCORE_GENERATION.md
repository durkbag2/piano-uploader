# Create piano sheet music from a song title

The **Create a score** workflow starts with a song title and searches **Internet Archive and BitMidi** for an existing MIDI transcription or arrangement. Both MIDI sources are selected by default, and you can filter them independently. It is available from **Create piano score** on every search query and result card, regardless of the search provider. Those entrypoints carry the title into an automatic MIDI lookup. You select a source before the app converts its notes into a traditional piano score with treble and bass staves. It can also convert your own MIDI file.

A title is a search query, not a musical description. The app does not compose missing parts or transcribe audio. A missing arrangement produces an explicit empty result with a route to the existing sheet-music search. Search results may be unrelated or different editions; review the title, creator, source page, and license before selecting one.

BitMidi lookup first reads the ordinary website search page, such as `https://bitmidi.com/search?q=Backstreet+Boys+Drowning`. If the top result's title contains every search keyword (ignoring case, accents and separators), it is selected automatically. A missing keyword leaves the result unselected for your review. Generation opens the selected source page and follows its explicitly labeled MIDI download link. The JSON interface remains a fallback when website HTML cannot be read. Its adapter follows the search/download markup and schema in the site's [open-source implementation](https://github.com/feross/bitmidi.com). Catalog metadata is not a music license: BitMidi files may have no author or licensing details, and its software repository's license does not grant rights to adapt every hosted MIDI. Check the source and your permissions before generating a score. MIDI downloads are restricted to the selected source's HTTPS hosts, bounded to 2 MB, and validated as MIDI.

Each source reports availability and offers a native website search link. A failure from BitMidi does not remove Internet Archive matches, or vice versa. Cloud access requires `bitmidi.com` and `www.bitmidi.com` in the environment's network allowlist; this cloud setting does not configure networking on your local Mac.

Selected BitMidi files are downloaded automatically during generation and cached under `backend/.state/midi-cache` (or the configured `PIANO_STATE_DIR`). The cache holds at most 20 downloads, each up to 2 MB, for reuse within 24 hours. Catalog metadata and the selected source page are checked before cached bytes are used. Expired or corrupted cached bytes are downloaded again. Cache files are private and ignored by Git; if the cache is unwritable, generation continues using the downloaded file. Delete the cache directory while the backend is stopped to clear it. The cache does not provide offline catalog search or bypass source access requirements.

## Update and run

From the repository root, stop both development servers, then run:

```bash
git pull --ff-only origin feat/piano-uploader-app
backend/.venv/bin/python -m pip install -r backend/requirements-lock.txt
npm --prefix frontend ci
```

Restart the backend and frontend using the README's two-terminal instructions. These dependency updates are required for MIDI parsing and notation/PDF rendering. Python 3.9 remains supported.

## Generate and export

1. Use **Create piano score for this search** from Discover, **Create piano score** from a result card, or open **Create a score** and enter a title. The lookup includes BitMidi first by default, alongside Internet Archive.
2. Review the candidates and select an arrangement. When the source also has a traditional PDF, you can use that original score instead of regenerating notation.
3. Leave **Part to include in the piano score** on **Automatic — prominent piano part**, confirm permission, then choose **Create piano score**. The backend downloads the selected MIDI directly and isolates one likely piano part. BitMidi downloads are cached locally for repeat generation; no manual file transfer is needed.
4. Review the notation preview and conversion warnings. Use **Play** to hear the generated score, with pause, stop, seeking and playback speed controls. Download **PDF** for a printable score, or **MusicXML** to refine the notation in an editor.
5. Choose **Save PDF to Google Drive** or **Save PDF to iCloud Drive** to use the active library destination. macOS manages iCloud synchronization; Smart Pianist import remains a manual step on your iPad.

For a local file, choose **Or use your own original MIDI file**. Use a piano arrangement rather than an entire ensemble recording. Local uploads accept Standard MIDI format 0 or 1 files up to 2 MB. There is no 5,000-note input cutoff. Conversion limits are 15 minutes and 256 measures; too many notation symbols are rejected. Dense overlapping voices are reduced automatically as described below. Source download availability, licensing, and musical quality vary by arrangement.

## Listen to the generated score

Playback uses the final generated MusicXML: it follows the selected part, quantized rhythm, rests, chords, ties and tempo changes, including any dense-voice reduction. It therefore lets you hear the notation you are reviewing. Tied notes sustain across written segments without repeated attacks. The playback timeline includes rests through the end of the written score; its length can differ from the original MIDI duration shown in the score summary.

The browser synthesizes a piano-style preview using Web Audio, with uniform dynamics. It does not reproduce the source MIDI's instruments, pedal or expressive velocities. Click **Play** to enable audio, then use **Pause**, **Stop**, the position slider and the speed selector to review a passage. Changing the source or selected part stops the old preview. No additional audio downloads or services are required.

## What the generated draft means

By default, generation isolates **one MIDI track/channel part**, rather than combining the entire band. It prefers parts identified by General MIDI piano-family programs or piano track names, then ranks those candidates by note activity weighted by velocity. This is a metadata heuristic, not an audio measurement of prominence. Percussion is excluded. If no piano is identified, the app uses an unlabeled/default-instrument candidate or a prominent non-percussion fallback and reports that uncertainty.

The generated score shows the chosen part and how many notes were used from the original pitched-note total. After the first generation, the **Part to include in the piano score** selector lists the file's available parts. Choose another to regenerate from the same original MIDI, or choose **All parts — combine the arrangement** to restore the complete pitched arrangement. Changing the source or uploaded file resets selection to automatic. Selected-part information is retained in MusicXML and PDF credits.

Part identity includes both track and channel, so two tracks sharing a channel are not combined during automatic/manual single-part generation. Global tempo, meter, and key metadata retain their original timing. If a selected track/channel changes instruments, the entire part is retained, including non-piano passages; the app warns about this. It does not separate instrument sections or infer a piano part from audio.

The conversion uses MIDI pitches and note timing to produce MusicXML, which is engraved in the browser and exported as a vector PDF. It infers a division between the two staves and quantizes rhythm; it does not infer a pianist's intended fingering or produce a reviewed professional arrangement. Performance MIDI may require substantial editing. Review chords, rests, ties, accidentals, hand placement, and any reported omissions or approximations.

If one staff would require more than 12 independent overlapping voices, the converter reduces that staff to one voice of chords grouped by note onset. Distinct pitches and attacks are retained. Notes within each chord receive a shared duration, clipped at the next attack, so sustain and individual release times change. Normal-density voices keep their independent timing and ties. The preview warning and exported PDF/MusicXML credits disclose any dense-voice reduction. Review the resulting chord sizes and note lengths before playing; a multi-instrument MIDI does not automatically become a physically playable piano arrangement.

Existing PDFs can retain a human engraver's intentional voicing and notation more faithfully than regenerating from performance MIDI. MusicXML is provided so you can correct the draft in a notation editor. Audio, arbitrary music URLs, paid-site downloads, and automatic Smart Pianist control are outside this workflow.

Generated files retain source credit and conversion information. Check your rights to adapt and distribute the arrangement; the confirmation checkbox does not supply a license. The live example used in development was [Moonlight Sonata (Piano), by Chordzy](https://archive.org/details/chordzy-sheet-music-ludwig-van-beethoven-moonlight-sonata-1st-movement-piano), whose source metadata lists [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). This is one verified example, not an assertion that every search result has that license.
