# Validation performed

## Dense overlapping voice reduction

All **135 backend tests passed on Python 3.9.25 and Python 3.12**, and the production build passed. A regression exercises a MIDI requiring more than 12 independent voices in both staves: the actual upload endpoint generates a two-voice chord reduction instead of the previous overlap error. It verifies exact preservation of distinct pitch/onset pairs, correct per-voice measure timing, and disclosure of adjusted note lengths in warnings and MusicXML credits. The existing normal-density polyphony/tie test confirms those scores are not reduced.

Chromium uploaded that dense MIDI to the running backend, displayed the reduction warning, rendered the grand staff, and downloaded a PDF without JavaScript errors. The exported PDF was checked for the reduction/note-length disclosure. This is a disclosed notation reduction, not a verified performance-faithful or musician-reviewed piano arrangement.

## Removal of the 5,000-note input cutoff

All **134 backend tests passed on Python 3.9.25 and Python 3.12** after removing the MIDI note-start count rejection. A regression uploads a 6,004-note MIDI through the actual score-creation endpoint, verifies success and all 6,004 pitched notes in the generated MusicXML, and checks each voice's measure timing. No replacement input note-count cutoff was added. Existing file-size, event-count, duration, measure, voice-density and rendered-symbol safeguards remain.

## BitMidi website-flow correction

All **133 backend tests passed on Python 3.9.25 and Python 3.12**, and the production build passed. New tests exercise the exact requested flow with representative server-rendered HTML: search `Backstreet Boys Drowning`, recognize the top result only when all keywords match, open its source page, follow the labeled download link, generate MusicXML, and reuse cached MIDI bytes. Tests reject ambiguous/external/unlabeled download links and prevent selecting a top title with a missing keyword. They also verify HTTP 520 is reported as source unavailability rather than an empty successful search. Existing JSON fallback tests remain active.

Chromium checks with controlled source responses confirmed the query button automatically selects the matching top title, keeps permission gating, passes the source page to generation, and leaves an incomplete match unselected. Live website search, source-page and JSON requests returned **HTTP 520** from BitMidi at validation time, so the real Drowning download could not be verified here. The adapter now follows the website flow; that implementation result is separate from live source availability.

## BitMidi source and download cache

All **130 backend tests passed on Python 3.9.25 and Python 3.12**, including BitMidi's public JSON envelope, query encoding, supported slug formats, bounded/validated downloads, provider selection and isolated failures, source-page/download identity, malicious links/redirects, generation attribution, and cache reuse/expiry/corruption/privacy/eviction. Cache integration tests run the actual generation endpoint with mocked BitMidi responses: repeat generation downloads the MIDI once, while expired or modified bytes are downloaded again. Metadata/source validation still runs for cache hits. The production build passed.

The adapter schema was checked against the official `feross/bitmidi.com` source at commit `f8aa4d6094474626153948c553e1a3d9c8168b7b`; its tests use that documented schema and synthetic MIDI, not live BitMidi captures. Direct live BitMidi requests were blocked by the cloud proxy. A live combined Moonlight query retained four Internet Archive candidates while reporting BitMidi unavailable, proving source failure isolation in the running backend.

Chromium checks confirmed the universal query button includes BitMidi first, both sources default selected, unavailable-source website links, working Archive candidates, and (with mocked BitMidi responses) candidate order, source-page handoff, rights gating, notation/PDF export, empty-provider gating, mobile width and no JavaScript errors. The exported fixture PDF retained BitMidi credit, its source URL and unknown-license text, without mislabeling the file as Archive. Successful live BitMidi search/download still requires validation after network access is available.

The cloud configuration draft adds `bitmidi.com` and `www.bitmidi.com` while preserving existing network destinations, and updates reusable startup guidance. The tool reported that review/publication is required; saving the draft did not apply it to this running environment. This cloud restriction does not establish that BitMidi is blocked on the user's Mac.

## Piano score generation

All **105 backend tests passed on Python 3.9.25 and Python 3.12**. New tests cover grand-staff pitch placement, chords, independent voice timing with rests, sustained ties across measures, eighth-note triplets, dotted rhythms, opening meter/key/tempo, source attribution and XML escaping, malformed MIDI, percussion omission, file/duration/measure/density limits, streamed upload limits, rights checks, source URL/redirect validation, metadata preservation, and bounded source discovery. The original search/storage tests continue to pass.

The updated Python lock installed in the workspace and `pip check` passed. `npm ci` and the TypeScript/production build passed. The notation tool is loaded separately from the initial app; its large engraving/PDF bundle produces a Vite size warning but the build completes.

Live Internet Archive lookup for **Moonlight Sonata** returned four MIDI candidates. The selected Chordzy arrangement downloaded and converted **1,157 notes over 324.706 seconds into 69 measures**. Its metadata supplied the creator, CC BY 4.0 license URL, item page, and matching original PDF. Chromium rendered 12 notation pages, downloaded a matching MusicXML file and an approximately 8.4 MB vector PDF, and saved the generated PDF through the actual upload endpoint to a temporary simulated Mac iCloud folder. PDF page count, credit/license/adaptation text, title, visible tempo, and first-page notation were checked with PyMuPDF. Export fixes normalize supported font families and convert SVG point font sizes to pixels.

Browser checks also verified creation from a universal search-query action, creation from a catalog result card, title handoff and automatic source lookup, explicit source/rights gating, local MIDI input, an empty-source fallback, no JavaScript errors, and no horizontal overflow at 390 pixels. The empty-source and catalog-card cases use controlled fixtures; the Moonlight source search/download/conversion is live. This validates a generated notation draft, not a musician-reviewed arrangement or Smart Pianist analysis. Apple synchronization and on-device import remain untested.

## Browser-assisted website search and local PDF import

All **85 backend tests passed on Python 3.9.25 and Python 3.12**. New cases verify capture host/path validation, deduplication and canonical links, limits and unsupported providers, local PDF rights/MIME/signature/size checks, bounded streaming without Content-Length, and saves to both a simulated local iCloud folder and mocked Google Drive. Existing search and storage tests continue to pass. The production build passed.

Chromium checks exercised the companion collector on rendered Musicnotes/MuseScore fixtures, including hidden links, duplicates, title extraction, canonical URLs and unsupported pages. App browser checks imported those captures through the actual backend, opened source-only cards, verified website search query encoding, and uploaded a generated PDF through the actual local upload endpoint to a temporary simulated Mac folder. The served PDF matched its original bytes; rights gating, invalid-PDF rejection, cleared upload inputs, mobile width and JavaScript-error checks passed. This is a local integration test, not a live Apple synchronization test.

The companion popup's capture and JSON-download controls were checked with mocked Chrome APIs. Native unpacked-extension loading could not be validated with this environment's Chromium runtime. Live Musicnotes requests remained blocked by the cloud proxy; MuseScore returned a browser verification page. Successful capture against the user's authenticated Chrome/Edge pages and changing live website markup remains unverified. The supported product URL patterns and installation steps are documented in [website search instructions](WEBSITE_SEARCH.md).

## iCloud Drive addition

All **70 backend tests passed on Python 3.9.25 and Python 3.12** after adding iCloud Drive. The 21 additional cases cover legacy Google settings/queue migration, folder validation and symlink boundaries, unavailable/non-macOS storage, local PDF saves and collision handling, current-folder previews, cloud-only placeholders, polling without Google credentials, nested-folder handoffs, and separate provider queues. Mac behavior is simulated against temporary local folders; these tests do not establish native Apple synchronization.

The TypeScript/production build passed. Chromium checks against the running Linux backend confirmed unavailable iCloud storage is gated. Browser checks with mocked Mac responses verified destination selection/save, PDF rights gating, iCloud upload and library refresh, cloud-only Download Now guidance, nested-folder handoff opening, retention of the Google folder when switching, no JavaScript errors, and no horizontal overflow at a 390-pixel viewport. No actual Apple account, macOS FileProvider, iPad sync, or Smart Pianist import was tested.

## Earlier Google Drive and search checks

The local development launcher (`bash scripts/dev.sh`) started both services successfully. Dependency installation was verified with the Python lock file and `npm ci`; `pip check` and the TypeScript/production build passed.

- **49 backend tests passed**: persistent settings and interval validation; OAuth state expiration/replay and successful mocked token exchange; Drive pagination; authorized multipart PDF upload; queue deduplication and folder isolation; polling enable/interval behavior; PDF/thumbnail destination, redirect, signature, and size restrictions; origin/host protections; multi-source query encoding/selection; failure isolation; provider redirect/response limits; actual IMSLP API schema without page IDs; malformed responses; and representative Musicnotes/MuseScore product-link parsing. External Google calls and commercial-source product parser fixtures in these tests are mocked.
- **Live Internet Archive check passed**: a “moonlight” search returned 12 actual sheet-music records. All 12 cover thumbnails loaded through the image proxy. A selected score's 1,897,918-byte PDF was fetched and its PDF signature verified; browser download completed successfully.
- **Chromium browser checks passed**: settings save/reload through the actual backend; disconnected OAuth/upload gating; search/gallery; PDF preview/download; Escape closes the modal; no JavaScript errors; no horizontal overflow at a 390-pixel viewport.
- **Mocked connected-account browser checks passed**: upload requires rights confirmation, upload refreshes the Drive library, Import now creates a handoff, and opening a handoff records opened status. This does not establish live Drive account access or successful Yamaha import.
- **Live multi-source browser checks passed**: “moonlight” returned 12 Internet Archive and 12 IMSLP catalog matches. Source filters selected all four providers by default, blocked empty selection, and successfully searched IMSLP alone. All three added websites had native search links carrying the correct query. IMSLP catalog dialogs omitted PDF/Drive actions; Archive PDF controls remained available. Mobile width and JavaScript error checks passed.

Musicnotes requests were denied by the cloud proxy, so live product retrieval remains blocked. MuseScore requests varied between a browser verification/403 response and readable HTML with no supported public product records; the application accurately reports unavailable or external-search status and provides the native search link. Musicnotes and MuseScore HTML parser tests use representative fixtures, not verified live result captures. Adding their hosts to the saved allowlist does not remove source access requirements or establish a live automatic search integration.

Google OAuth credentials were absent, so actual consent, Drive library permissions, live uploads, and live Drive polling were not tested. Smart Pianist on the CSP-255/iPad was not available for a device test. Official App Store release history confirms PDF import and CSP-family Score Reader features; no unattended import adapter has been verified or implemented.

The dependency lock was corrected after a macOS installation reported an unavailable `annotated-types==0.8.0`. The replacement pins use published PyPI releases. Clean installs and `pip check` passed with Python 3.9.25 and 3.12; all 49 backend tests passed in both environments without the earlier test-client deprecation warning. Binary dependency resolution for Python 3.9.6 succeeded for both Intel macOS and Apple Silicon, including the Python 3.9-specific `exceptiongroup` and `tomli` dependencies. These are Mac wheel checks, not native execution on the user's Mac.

Python 3.9 support required replacing a Python 3.10-only union annotation. The local launcher also no longer uses `wait -n`, which is unavailable in macOS's default Bash; a launcher check confirmed that a failed backend returns its exit code and stops the frontend.
