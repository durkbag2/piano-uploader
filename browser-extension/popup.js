import { captureSearchResults } from './collect.js';
const button = document.querySelector('#capture');
const status = document.querySelector('#status');
const output = document.querySelector('#output');
const download = document.querySelector('#export');
let previousUrl;
button.addEventListener('click', async () => {
  button.disabled = true; output.hidden = true; download.hidden = true;
  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    const [execution] = await chrome.scripting.executeScript({ target: { tabId: tab.id }, func: captureSearchResults });
    if (execution.error || !execution.result) throw new Error(execution.error?.message || 'Could not capture this page. Open a supported search page and try again.');
    const data = execution.result;
    const json = JSON.stringify(data, null, 2);
    output.value = json; output.hidden = false;
    if (previousUrl) URL.revokeObjectURL(previousUrl);
    previousUrl = URL.createObjectURL(new Blob([json], { type: 'application/json' }));
    download.href = previousUrl; download.download = `piano-uploader-${data.provider}-results.json`; download.hidden = false;
    status.textContent = `Captured ${data.results.length} score links. Download the JSON and choose Import website results in Piano Uploader.`;
  } catch (error) { status.textContent = error.message || 'Capture failed. Try again from a supported search page.'; }
  finally { button.disabled = false; }
});
