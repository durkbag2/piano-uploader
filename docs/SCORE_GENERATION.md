# Create piano sheet music from a song title

The **Create a score** workflow starts with a song title and searches Internet Archive for an existing MIDI transcription or arrangement. It is available from **Create piano score** on every search query and result card, regardless of the search provider. Those entrypoints carry the title into an automatic MIDI lookup. You select a source before the app converts its notes into a traditional piano score with treble and bass staves. It can also convert your own MIDI file.

A title is a search query, not a musical description. The app does not compose missing parts or transcribe audio. A missing arrangement produces an explicit empty result with a route to the existing sheet-music search. Search results may be unrelated or different editions; review the title, creator, source page, and license before selecting one.

## Update and run

From the repository root, stop both development servers, then run:

```bash
git pull --ff-only origin feat/piano-uploader-app
backend/.venv/bin/python -m pip install -r backend/requirements-lock.txt
npm --prefix frontend ci
```

Restart the backend and frontend using the README's two-terminal instructions. These dependency updates are required for MIDI parsing and notation/PDF rendering. Python 3.9 remains supported.

## Generate and export

1. Open **Create a score**, enter a song title, and choose **Find MIDI arrangements**, or use **Create piano score** from any search query or result card.
2. Review the candidates and select an arrangement. When the source also has a traditional PDF, you can use that original score instead of regenerating notation.
3. Confirm you have permission to adapt the MIDI and save the resulting score, then choose **Create piano score**.
4. Review the notation preview and conversion warnings. Download **PDF** for a printable score, or **MusicXML** to refine the notation in an editor.
5. Choose **Save PDF to Google Drive** or **Save PDF to iCloud Drive** to use the active library destination. macOS manages iCloud synchronization; Smart Pianist import remains a manual step on your iPad.

For a local file, choose **Or use your own original MIDI file**. Use a piano arrangement rather than an entire ensemble recording. Local uploads accept Standard MIDI format 0 or 1 files up to 2 MB. Conversion limits are 15 minutes, 5,000 notes, and 256 measures; unusually dense overlapping voices or too many notation symbols are rejected. Source download availability, licensing, and musical quality vary by arrangement.

## What the generated draft means

The conversion uses MIDI pitches and note timing to produce MusicXML, which is engraved in the browser and exported as a vector PDF. It infers a division between the two staves and quantizes rhythm; it does not infer a pianist's intended fingering or produce a reviewed professional arrangement. Performance MIDI may require substantial editing. Review chords, rests, ties, accidentals, hand placement, and any reported omissions or approximations.

Existing PDFs can retain a human engraver's intentional voicing and notation more faithfully than regenerating from performance MIDI. MusicXML is provided so you can correct the draft in a notation editor. Audio, arbitrary music URLs, paid-site downloads, and automatic Smart Pianist control are outside this workflow.

Generated files retain source credit and conversion information. Check your rights to adapt and distribute the arrangement; the confirmation checkbox does not supply a license. The live example used in development was [Moonlight Sonata (Piano), by Chordzy](https://archive.org/details/chordzy-sheet-music-ludwig-van-beethoven-moonlight-sonata-1st-movement-piano), whose source metadata lists [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). This is one verified example, not an assertion that every search result has that license.
