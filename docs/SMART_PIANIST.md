# Smart Pianist integration boundaries

The target device setup is a **Yamaha CSP-255 with an iPad (10th generation)**. This first version runs in a local browser on the computer hosting the server, with Google Drive providing the handoff to the iPad. It can discover sheet music, preview PDFs, copy supported downloads into a configured Google Drive folder, and poll that folder for newly added files. Its current implementation prepares a device handoff; it does not perform or verify an unattended import into Smart Pianist.

## What the import workflow means

- **Polling** checks the configured Drive folder and records new files in a handoff queue. The interval controls the scan frequency while the server is running.
- **Import now** performs that scan immediately. Finding a file or preparing a handoff is not confirmation that Yamaha imported it.
- Open or download a queued file on the iPhone or iPad. Use the device's Files/share interface and Smart Pianist's supported import procedure for that file format and piano model.
- The application's handoff status records that a file was **opened**, not that Smart Pianist imported, analyzed, or played it. Check the result inside Smart Pianist separately.

## File formats and device compatibility

A sheet-music PDF contains a visual score rather than MIDI performance data. Smart Pianist **does support PDF import and score features**, according to Yamaha's official App Store release history:

- **3.1.0 (April 12, 2023):** “PDF files can be imported into User Song.”
- **3.4.0 (April 23, 2024):** added “Score Reader” and the Guide function on PDF scores for **CSP Series, CVP Series, and P-S500**.
- **3.9.0:** added stream-light/guide-lamp chord guidance for imported lead-sheet scores on those same instrument families, and a difficulty display for PDF scores.
- **3.10.0:** documents changes to the tone used when importing PDF piano/vocal scores and improved guidance for PDF lead sheets on CSP Series, CVP Series, and P-S500.

This is direct official evidence for PDF import and **CSP-family** PDF score guidance. It is not a completed CSP-255/iPad device test or a verified list of accepted PDF layouts. The exact CSP-255 feature matrix, instrument firmware requirements, accepted score types, and import steps still need confirmation from the matching manual and the installed app. Do not describe all PDFs as unsupported, or promise that every downloaded PDF can be analyzed or played.

This project stores and hands off the original PDF. It does not convert PDF notation to MIDI or reproduce Yamaha's Score Reader feature. Use Smart Pianist's supported PDF import/Score Reader workflow on the iPad and check its analysis/playback result there. Scanned images, complex notation, or incompatible score layouts may require separate handling; any specific restrictions must come from the current manual or an actual device test.

Smart Pianist's available features and supported song/import formats depend on its version, the connected Yamaha piano model, and the instrument's firmware. Compatible MIDI or audio may be useful for a future handoff integration, but each format must be checked against the relevant Yamaha documentation and tested on the actual device before the application claims support.

No public Smart Pianist automation API, supported URL scheme, direct Google Drive synchronization, or iOS shortcut for unattended import has been verified for this project. The application must not invent an endpoint, launch URL, or automatic import success state. Browser/server polling cannot control another iOS application's private storage.

## Required follow-up for device integration

1. The model and device are known: **CSP-255** and **iPad (10th generation)**. Record instrument firmware, installed Smart Pianist version, and iPadOS version next.
2. Consult the matching Smart Pianist manual and Yamaha compatibility list, specifically checking **PDF Score** availability and import/analysis requirements for the CSP-255.
3. Test a permitted sample PDF on the actual iPad using the documented import route, and confirm whether it is merely displayed or can also be analyzed and played.
4. Add an automated adapter only if Yamaha provides a supported interface; otherwise keep the explicit device handoff.

## Official references and verification status

- [Smart Pianist — Yamaha USA](https://usa.yamaha.com/products/musical_instruments/pianos/apps/smart_pianist/index.html)
- [Smart Pianist online manual](https://manual.yamaha.com/mi/app/smartpianist/en/)
- [Smart Pianist downloads and specification links — Yamaha USA](https://usa.yamaha.com/products/musical_instruments/pianos/apps/smart_pianist/downloads.html)
- [Smart Pianist US — Apple App Store](https://apps.apple.com/us/app/smart-pianist-us/id1261382457)
- [Yamaha downloads and manuals](https://download.yamaha.com/)

After a network configuration change during implementation, the official App Store page and Yamaha USA's Smart Pianist product, features, and downloads pages were successfully retrieved with HTTP 200. The PDF import and CSP-family feature statements above were checked against the **App Store release history**, not customer reviews.

The linked `manual.yamaha.com` manual remains blocked by the environment's proxy with HTTP 403. The specification PDF linked on Yamaha's downloads page (`SmartPianist_Specification_202606.pdf`) could not be retrieved from either its USA-hosted asset path or the `download.yamaha.com` link; those requests also returned proxy 403. Attempted CSP-200/CSP-255 product URLs redirected to the generic piano overview and therefore do not establish exact model support. No browsing/search tool was available. The remaining unverified items are the current CSP-255 feature matrix, exact import procedure/score restrictions, and a supported external automation interface.
