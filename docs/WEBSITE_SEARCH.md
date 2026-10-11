# Website search without an API

Musicnotes and MuseScore can require JavaScript, sign-in, or browser verification. Backend HTML searches remain best-effort. The browser-assisted workflow uses your normal Chrome or Edge session to search and captures loaded product links when you request it. It does not require the sites to expose an API.

## Install the companion

1. Pull the `feat/piano-uploader-app` branch and restart the app's backend and frontend.
2. In Chrome, open `chrome://extensions`; in Edge, open `edge://extensions`.
3. Enable **Developer mode**, choose **Load unpacked**, and select the repository's `browser-extension` folder.
4. Pin **Piano Uploader Search Companion** from the browser's Extensions menu.

The extension requests only `activeTab` and `scripting`: it inspects the selected tab when invoked. It has no persistent site host permissions, background worker, account-token storage, or network-upload logic. It exports a file that you choose to import into the local app. Reload the extension on its Extensions page after pulling future extension updates.

## Search and capture

1. Enter a song or composer in Piano Uploader's search bar. Choose **Search Musicnotes** or **Search MuseScore** to open that website with your query.
2. Use the site's filters, sign in if needed, and complete any normal browser verification yourself. Wait for the results to load. Scroll to load additional results if the site uses lazy loading.
3. Click the companion icon, then **Capture results from this tab**.
4. Choose **Download results JSON**. Return to Piano Uploader and select that file under **Import browser search capture**.
5. Captured listings appear as source cards. Open a listing to continue on its source website.

Captures contain titles and score-page links, not PDFs or PDF thumbnails. They include up to 50 supported loaded links and omit hidden links, duplicates, and unsupported product paths. Current supported product formats are Musicnotes `/sheetmusic/mtd.asp?ppn=...` and MuseScore `/user/<id>/scores/<id>`. Other formats, including official-score paths, may be omitted. Website markup can change; zero captured links is reported rather than inventing results. Search captures stay in the current app session and a new regular search replaces the displayed results.

The app validates provider hosts and product paths again on import. A captured link does not verify the score's license, price, account access, or PDF download availability. Live authenticated captures on these websites still need testing in your browser; cloud requests were blocked during development.

## Save an authorized PDF

1. Open the source listing and use the site's supported purchase/download process, if a PDF download is available for that score and your account.
2. In Piano Uploader, open **My library → Add a PDF from your device**.
3. Choose the original downloaded PDF, confirm permission to save it, and choose **Save PDF to Google Drive** or **Save PDF to iCloud Drive**, according to your active destination.
4. The app accepts PDF files up to 25 MB. For iCloud, macOS handles synchronization; wait for the PDF to appear in Files on your iPad and import it manually in Smart Pianist.

This workflow does not automate purchases, sign-in, browser challenges, or Smart Pianist import. It does not reconstruct a restricted score from preview images. If the source does not offer a supported PDF download, the companion cannot supply one.
