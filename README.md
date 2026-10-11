# Piano Uploader

A local browser app to find sheet-music PDFs, generate piano notation drafts from existing MIDI arrangements, save authorized copies to Google Drive or an iCloud Drive folder on your Mac, and detect new files for handoff to your Yamaha Smart Pianist workflow.

Target devices: Yamaha CSP-255 and iPad (10th generation). Automatic import into Smart Pianist is **not implemented or verified**. Polling and **Import now** scan the selected storage folder and prepare a queue; opening a file does not confirm it was imported. See [device integration notes](docs/SMART_PIANIST.md).

## Local development

Requirements: Python 3.9+ and Node.js 22+. The backend is tested with Python 3.9 and 3.12; Python 3.9.6 on macOS has compatible dependency wheels.

```bash
cd piano-uploader
python3 -m venv backend/.venv
backend/.venv/bin/python -m pip install -r backend/requirements-lock.txt
cd frontend
npm ci
cd ..
bash scripts/dev.sh
```

Open the local frontend on port 5173 in your browser. The API listens on port 8000. Both bind to loopback for this single-user version. Keep the backend running for polling; timers cannot operate while it is stopped. Do not expose this version to the public internet: it has no multi-user account system.

On macOS, you can also use two terminals to start the services. From the project root in the first terminal:

```bash
backend/.venv/bin/python -m uvicorn backend.app:app --host 127.0.0.1 --port 8000 --no-access-log
```

In the second terminal, from the project root:

```bash
cd frontend
npm run dev
```

If an earlier checkout fails to install a pinned package, update the app branch with `git pull --ff-only origin feat/piano-uploader-app` and rerun the dependency installation. The lock file uses published PyPI releases and includes Python 3.9-specific dependencies. Do not manually change one version while retaining the obsolete lock file.

## Create piano sheet music

Use **Create piano score** on any search query or result card, or open **Create a score** and enter a song title. Select an existing MIDI transcription or arrangement, confirm permission to adapt it, and generate a traditional piano notation draft. Preview the treble and bass staves, download PDF or editable MusicXML, and save the PDF to your selected storage destination. You can also upload your own MIDI.

This converts existing notes; a song title alone cannot reconstruct the music. Review the draft's rhythm and inferred hand assignment. When available, an original PDF may preserve the arrangement more faithfully. See [score generation and dependency update instructions](docs/SCORE_GENERATION.md).

## Musicnotes and MuseScore website searches

Enter a query and choose **Search Musicnotes** or **Search MuseScore** to search in your own browser. The optional Chrome/Edge companion in `browser-extension` exports loaded score links; use **Import browser search capture** to bring them into the app. Install and use it following [website search instructions](docs/WEBSITE_SEARCH.md).

After downloading a purchased or authorized PDF through its source website, use **My library → Add a PDF from your device** to save it to the active Google Drive or iCloud Drive destination. This works independently of the companion and accepts PDFs up to 25 MB. Captured listings are source links; they do not provide restricted PDFs or PDF thumbnails.

## iCloud Drive on your Mac

iCloud storage uses the Mac's existing iCloud Drive folder. It does not require Google authorization, an Apple password in this app, or a new developer account. The backend must run on your Mac to use this option; a Linux cloud server cannot access your Mac's iCloud Drive.

1. Enable **iCloud Drive** in macOS **System Settings → Apple Account → iCloud → Drive**. On older macOS versions this appears under **Apple ID**. Check that iCloud Drive appears in Finder.
2. Enable iCloud Drive on your iPad using the same Apple Account. Confirm **iCloud Drive** is visible under **Browse → Locations** in the Files app.
3. In Piano Uploader's **Settings & connections**, choose **iCloud Drive** as the storage destination. Choose a folder name such as `Piano Uploader`, then save settings. This is a folder inside iCloud Drive, not an arbitrary filesystem path.
4. Select a score, preview it, confirm that you have permission to save it, and choose **Save to iCloud Drive**. The app writes the original PDF into the local folder. macOS manages synchronization separately; a successful save is not confirmation that the iPad has received it.
5. After syncing, find the score on your iPad under **Files → Browse → iCloud Drive → Piano Uploader** (or your configured folder). Use Smart Pianist's supported import/file-picker workflow for a compatible score. Import remains a manual device step.

You can also copy authorized PDFs downloaded from another source into the same folder using Finder. Automatic checks and **Import now** scan the selected storage folder and prepare handoffs. Keep the Mac backend running for polling. If a file is stored only in iCloud and not downloaded to the Mac, download it through Finder before expecting a local preview.

The default local iCloud Drive root is `~/Library/Mobile Documents/com~apple~CloudDocs`. The app limits storage to a subfolder beneath that location. It detects local availability and access; it cannot inspect Apple's sync status, grant macOS permissions, or verify the iPad's Apple Account. If iCloud is unavailable, enable it on the Mac and check the account running the backend.

Google Drive remains available as a separate destination. Switching destinations retains the Google connection and folder settings; it does not copy files between the two services.

## Google Drive connection

1. Create a Google Cloud project, enable the **Google Drive API**, and configure the OAuth consent screen. If using a testing consent screen, add your Google account as a test user.
2. Create an OAuth **Web application** client. Add `http://localhost:8000/api/auth/callback` as an authorized redirect URI.
3. Set `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` in the backend process environment before startup. Supply credentials locally or through secure environment settings; never commit them or send them in chat. Use `localhost` consistently for OAuth redirects.
4. In the app's administration panel, connect Drive and consent to the requested Drive permissions. This version requests full Drive access so it can inspect an existing folder; the application itself lists PDFs and uploads only into the configured folder. Configure the folder ID from your Drive folder URL. The account must be able to list the folder and create files there. The app cannot grant access that your account does not have; use Drive's folder-sharing settings to manage those permissions.
5. Choose the polling interval, enable scanning, and use **Import now** to scan immediately. Open queued files on the iPad and follow Smart Pianist's documented device import procedure for a compatible format.

Settings, the handoff queue, and OAuth tokens are local to `backend/.state/`, which is ignored by Git. Treat this directory as sensitive. Disconnect Drive to remove locally stored tokens. A live Drive account is required for library listing, upload, and polling; disconnected states are displayed explicitly.

## Search and PDF handling

Search offers selectable sources: **Internet Archive, IMSLP, Musicnotes, and MuseScore**. Requests run independently so an unavailable site does not hide results from another. The source panel reports availability and offers a native website search link for your query. Website search links are separate from actual catalog matches.

Internet Archive results support in-app thumbnails, PDF previews, downloads, and Drive uploads. IMSLP uses its public catalog search API and links to matching work pages, where you can select an edition and check access/licensing. Musicnotes and MuseScore public catalog links can appear when their search pages are accessible and readable; when automated requests are blocked or their pages require a browser, the app provides a native search link instead. Purchases, account access, previews, and authorized downloads for these sites happen on their own websites. In-app direct PDF download/upload remains limited to Internet Archive; adding a search source does not grant access to paid files.

Check the source's license and your right to copy each score; the upload action requires an explicit confirmation. A source listing is not proof of permission. Catalog-only cards display **View at source**, with no PDF or Drive actions.

The PDF proxy accepts only HTTPS Internet Archive destinations, checks redirects, validates PDF content, and bounds downloads. Google credentials are used only for Google authentication and Drive operations. Provider failures are shown as errors rather than simulated successes.

## Validation

```bash
backend/.venv/bin/python -m pytest backend/tests -q
cd frontend
npm run build
```

Backend tests mock external providers so queue behavior, settings validation, OAuth checks, and PDF restrictions can be tested without credentials. Actual Drive authorization/upload and Yamaha device import require your account and device and must be tested separately.

## Cloud network requirements

Allow Internet Archive (`archive.org` and `*.archive.org`), IMSLP (`imslp.org`, `www.imslp.org`), Musicnotes (`musicnotes.com`, `www.musicnotes.com`), MuseScore (`musescore.com`, `www.musescore.com`), and Google (`accounts.google.com`, `oauth2.googleapis.com`, `www.googleapis.com`, `drive.google.com`). Yamaha and App Store documentation use `usa.yamaha.com`, `download.yamaha.com`, and `apps.apple.com`. These domains are saved in the environment draft, preserving earlier entries. Review/save the settings and publish to activate the reusable environment configuration; a draft save alone does not establish runtime access. Site-side access restrictions can still block automated searches after whitelisting.
