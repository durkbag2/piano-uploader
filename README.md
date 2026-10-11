# Piano Uploader

A local browser app to find sheet-music PDFs, preview pages, save authorized copies to Google Drive, and detect new files for handoff to your Yamaha Smart Pianist workflow.

Target devices: Yamaha CSP-255 and iPad (10th generation). Automatic import into Smart Pianist is **not implemented or verified**. Polling and **Import now** scan Drive and prepare a queue; opening a file does not confirm it was imported. See [device integration notes](docs/SMART_PIANIST.md).

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
