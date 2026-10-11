// Self-contained because Chrome serializes this function into the selected tab.
export function captureSearchResults() {
  const host = location.hostname.toLowerCase();
  const provider = ['musicnotes.com', 'www.musicnotes.com'].includes(host) ? 'musicnotes' : ['musescore.com', 'www.musescore.com'].includes(host) ? 'musescore' : null;
  const searchPath = provider === 'musicnotes' ? /^\/search\/go\/?$/i : /^\/sheetmusic\/?$/;
  if (!provider || location.protocol !== 'https:' || !searchPath.test(location.pathname)) {
    throw new Error('Open a Musicnotes or MuseScore search page before capturing results.');
  }
  const records = []; const seen = new Set();
  for (const anchor of document.querySelectorAll('a[href]')) {
    if (!anchor.getClientRects().length || getComputedStyle(anchor).visibility === 'hidden') continue;
    let url; try { url = new URL(anchor.href); } catch { continue; }
    if (url.protocol !== 'https:' || url.username || url.password || (url.port && url.port !== '443')) continue;
    const allowed = provider === 'musicnotes' ? ['musicnotes.com', 'www.musicnotes.com'] : ['musescore.com', 'www.musescore.com'];
    if (!allowed.includes(url.hostname.toLowerCase())) continue;
    let pageUrl;
    if (provider === 'musicnotes') {
      if (url.pathname.toLowerCase() !== '/sheetmusic/mtd.asp') continue;
      const ppn = [...url.searchParams].find(([key]) => key.toLowerCase() === 'ppn')?.[1];
      if (!ppn || !/^[a-zA-Z0-9]+$/.test(ppn)) continue;
      pageUrl = 'https://www.musicnotes.com/sheetmusic/mtd.asp?ppn=' + encodeURIComponent(ppn);
    } else {
      const match = url.pathname.match(/^\/user\/(\d+)\/scores\/(\d+)\/?$/);
      if (!match) continue;
      pageUrl = `https://musescore.com/user/${match[1]}/scores/${match[2]}`;
    }
    const label = anchor.getAttribute('aria-label') || anchor.getAttribute('title') || anchor.innerText || anchor.querySelector('img[alt]')?.alt || '';
    const title = label.replace(/\s+/g, ' ').trim().slice(0, 250);
    if (!title || /^(view|details|download|view score|sheet music)$/i.test(title) || seen.has(pageUrl)) continue;
    seen.add(pageUrl); records.push({ title, page_url: pageUrl });
    if (records.length === 50) break;
  }
  if (!records.length) throw new Error('No supported score links found. Complete any normal sign-in or verification, wait for results to load, and try again. Official-score or other product URL formats may be unsupported.');
  return { provider, page_url: location.href.split('#')[0], results: records };
}
