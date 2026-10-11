import { useEffect, useRef, useState, type FormEvent } from 'react';
import { OpenSheetMusicDisplay } from 'opensheetmusicdisplay';
import { jsPDF } from 'jspdf';
import 'svg2pdf.js';
import { ArrowUpRight, Check, CircleHelp, CloudUpload, Download, FileMusic, Loader2, Search } from 'lucide-react';
import './score-generator.css';

type Candidate = { id: string; title: string; creator?: string; source: string; page_url: string; midi_url: string; original_pdf_url?: string; pdf_url?: string; license?: string };
type Score = { musicxml: string; title: string; warnings: string[]; source: { type: 'upload' | 'archive'; url?: string; page_url?: string; creator?: string; license?: string; license_url?: string; pdf_url?: string }; note_count: number; duration_seconds: number };
type Props = { initialTitle?: string; storageName: string; storageReady: boolean; onSaved: () => Promise<void>; onFindExisting: (title: string) => void; onConfigureStorage: () => void };
async function request<T>(url: string, options?: RequestInit): Promise<T> {
  const response = await fetch(url, options);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : data.error || 'Could not complete this request.');
  return data;
}
function download(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob); const anchor = document.createElement('a');
  anchor.href = url; anchor.download = filename; anchor.click(); window.setTimeout(() => URL.revokeObjectURL(url), 10000);
}
function filename(title: string) { return title.replace(/[\\/:*?"<>|]/g, '').trim().slice(0, 100) || 'Piano score'; }
export default function ScoreGenerator({ initialTitle = '', storageName, storageReady, onSaved, onFindExisting, onConfigureStorage }: Props) {
  const [title, setTitle] = useState(initialTitle);
  const [searched, setSearched] = useState('');
  const [candidates, setCandidates] = useState<Candidate[]>([]);
  const [selected, setSelected] = useState<Candidate | null>(null);
  const [midi, setMidi] = useState<File | null>(null);
  const [rights, setRights] = useState(false);
  const [score, setScore] = useState<Score | null>(null);
  const [busy, setBusy] = useState('');
  const [rendering, setRendering] = useState(false);
  const [rendered, setRendered] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const display = useRef<HTMLDivElement>(null);
  const lookupController = useRef<AbortController | null>(null);
  const lookupVersion = useRef(0);
  async function action(name: string, run: () => Promise<void>) {
    setBusy(name); setError(''); setNotice('');
    try { await run(); } catch (e) { setError(e instanceof Error ? e.message : 'Please try again.'); } finally { setBusy(''); }
  }
  useEffect(() => {
    if (!score || !display.current) return;
    let cancelled = false; const container = display.current;
    container.replaceChildren(); setRendering(true); setRendered(false);
    const osmd = new OpenSheetMusicDisplay(container, { backend: 'svg', autoResize: false, drawingParameters: 'default', pageFormat: 'A4_P', drawTitle: true });
    osmd.load(score.musicxml).then(() => {
      if (cancelled) return; osmd.render(); setRendered(true);
    }).catch(e => { if (!cancelled) setError(`The score could not be rendered: ${e instanceof Error ? e.message : 'invalid MusicXML'}`); }).finally(() => { if (!cancelled) setRendering(false); });
    return () => { cancelled = true; };
  }, [score]);
  async function lookup(text: string) {
    if (!text.trim()) return;
    lookupController.current?.abort();
    const controller = new AbortController(); lookupController.current = controller;
    const version = ++lookupVersion.current;
    setBusy('search'); setError(''); setNotice(''); setScore(null); setSelected(null); setMidi(null); setRights(false); setCandidates([]); setSearched('');
    try {
      const result = await request<{ results: Candidate[]; error?: string }>(`/api/scores/search?q=${encodeURIComponent(text.trim())}`, { signal: controller.signal });
      if (version !== lookupVersion.current || controller.signal.aborted) return;
      setCandidates(result.results); setSearched(text.trim()); if (result.error) setError(result.error);
    } catch (e) {
      if (version === lookupVersion.current && !controller.signal.aborted) { setError(e instanceof Error ? e.message : 'Could not search for MIDI arrangements.'); setSearched(text.trim()); }
    } finally { if (version === lookupVersion.current) setBusy(''); }
  }
  useEffect(() => {
    if (initialTitle.trim()) { setTitle(initialTitle); void lookup(initialTitle); }
    return () => { lookupVersion.current++; lookupController.current?.abort(); };
  }, [initialTitle]);
  function find(e: FormEvent) { e.preventDefault(); void lookup(title); }
  async function generate() {
    if (!rights || (!selected && !midi)) return;
    await action('generate', async () => {
      let result: Score;
      if (midi) {
        if (midi.size > 2 * 1024 * 1024) throw new Error('Choose a MIDI file no larger than 2 MB.');
        result = await request<Score>(`/api/scores/from-midi?title=${encodeURIComponent(title.trim() || midi.name.replace(/\.midi?$/i, ''))}&rights_confirmed=true`, { method: 'POST', headers: { 'Content-Type': 'audio/midi' }, body: midi });
      } else {
        result = await request<Score>('/api/scores/from-source', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ midi_url: selected!.midi_url, title: selected!.title, rights_confirmed: true }) });
      }
      setScore(result);
    });
  }
  async function pdf(): Promise<Blob> {
    const pages = Array.from(display.current?.querySelectorAll<SVGSVGElement>('svg') || []).filter(svg => !svg.parentElement?.closest('svg'));
    if (!rendered || !pages.length) throw new Error('Wait for the notation preview to finish rendering before exporting.');
    const doc = new jsPDF({ unit: 'mm', format: 'a4', orientation: 'portrait' });
    for (let index = 0; index < pages.length; index++) {
      const svg = pages[index]; if (index) doc.addPage('a4', 'portrait');
      const box = svg.viewBox.baseVal; const rect = svg.getBoundingClientRect();
      const width = box.width || Number(svg.getAttribute('width')) || rect.width;
      const height = box.height || Number(svg.getAttribute('height')) || rect.height;
      if (!width || !height) throw new Error('The rendered page has no exportable size. Download MusicXML and open it in a notation editor.');
      const ratio = Math.min(190 / width, 255 / height);
      // Clone to avoid changing the live SVG during the asynchronous vector export.
      const copy = svg.cloneNode(true) as SVGSVGElement;
      // svg2pdf's default text state is Times; explicitly align jsPDF's font
      // and every SVG text node so browser-only font names cannot fall back.
      for (const text of copy.querySelectorAll<SVGElement>('text, tspan')) {
        text.setAttribute('font-family', 'times');
        text.style.fontFamily = 'times';
        // VexFlow emits the metronome label in pt, but svg2pdf supports px/em.
        const size = text.style.fontSize || text.getAttribute('font-size') || '';
        if (/^[0-9.]+pt$/.test(size)) {
          const pixels = `${parseFloat(size) * 4 / 3}px`;
          text.setAttribute('font-size', pixels); text.style.fontSize = pixels;
        }
      }
      doc.setFont('times', 'normal'); doc.setFontSize(16); doc.setTextColor(0);
      await doc.svg(copy, { x: (210 - width * ratio) / 2, y: 10, width: width * ratio, height: height * ratio });
      const source = score!.source;
      const credit = [source.creator ? `Source / arranger: ${source.creator}.` : source.type === 'upload' ? 'Source: user-provided MIDI.' : 'Source: Internet Archive MIDI.', source.license ? `License: ${source.license}.` : 'Review original source permissions.', source.license_url || '', source.page_url || source.url || '', 'Adaptation: inferred hands; quantized rhythms. Review before playing.'].filter(Boolean).join(' ');
      doc.setFont('helvetica', 'normal'); doc.setFontSize(6.5); doc.setTextColor(80);
      doc.text(doc.splitTextToSize(credit, 190), 10, 272, { lineHeightFactor: 1.15 });
      doc.setFontSize(6); doc.text(`Page ${index + 1} of ${pages.length}`, 200, 292, { align: 'right' });
    }
    return doc.output('blob');
  }
  async function exportPdf(save = false) {
    if (!score || !rights) return;
    await action(save ? 'save' : 'pdf', async () => {
      const blob = await pdf(); if (blob.size > 25 * 1024 * 1024) throw new Error('This generated PDF exceeds the 25 MB upload limit. Download MusicXML instead.');
      if (save) {
        const result = await request<{ message?: string }>(`/api/upload-local?title=${encodeURIComponent(filename(score.title) + '.pdf')}&rights_confirmed=true`, { method: 'POST', headers: { 'Content-Type': 'application/pdf' }, body: blob });
        await onSaved(); setNotice(result.message || `Generated PDF saved to ${storageName}. Import it on your device using Smart Pianist’s supported workflow.`);
      } else { download(blob, filename(score.title) + '.pdf'); setNotice('Your rendered score PDF is ready to download.'); }
    });
  }
  return <section className="score-generator">
    <div className="handoff-info"><CircleHelp size={20} /><div><strong>Turn an existing MIDI transcription into a piano score.</strong><p>A song title searches for an available MIDI arrangement. It does not compose or transcribe a recording. Generated scores are drafts: hand assignment is inferred and rhythms are quantized, with supported triplet timing retained. Review the notation before playing.</p></div></div>
    {error && <div className="alert error" role="alert"><CircleHelp size={17} /><span>{error}</span></div>}
    {notice && <div className="alert success" role="status"><Check size={17} /><span>{notice}</span></div>}
    <form className="search-form score-search" onSubmit={find}><Search size={22} /><input aria-label="Song title for piano score" placeholder="Enter a song title or composer…" value={title} onChange={e => { setTitle(e.target.value); setScore(null); }} /><button className="primary" disabled={!!busy || !title.trim()}>{busy === 'search' ? <Loader2 className="spin" size={17} /> : <Search size={17} />} Find MIDI arrangements</button></form>
    {searched && <div className="score-candidates"><h2>MIDI arrangements for “{searched}”</h2><p className="score-help">Choose a real source arrangement. Availability and quality vary by title.</p>{candidates.length ? candidates.map(candidate => <div className={`score-candidate ${selected?.id === candidate.id ? 'selected' : ''}`} key={candidate.id}><label><input type="radio" name="midi-source" checked={selected?.id === candidate.id && !midi} disabled={!!busy} onChange={() => { setSelected(candidate); setMidi(null); setRights(false); setScore(null); }} /><span><strong>{candidate.title}</strong><small>{candidate.creator || 'Creator not listed'} · {candidate.source} · {candidate.license || 'Check source permissions'}</small></span></label><div className="score-source-links"><a className="text-button" href={candidate.page_url} target="_blank" rel="noreferrer">Source <ArrowUpRight size={14} /></a>{(candidate.original_pdf_url || candidate.pdf_url) && <a className="text-button" href={`/api/pdf?url=${encodeURIComponent(candidate.original_pdf_url || candidate.pdf_url || '')}`} target="_blank" rel="noreferrer">Existing PDF <ArrowUpRight size={14} /></a>}</div></div>) : <div className="score-no-source"><FileMusic size={28} /><h3>No MIDI arrangements found.</h3><p>Try another title, upload your own MIDI, or find an existing PDF score.</p><button className="text-button" onClick={() => onFindExisting(searched)}>Find existing sheet music <ArrowUpRight size={15} /></button></div>}</div>}
    <div className="score-midi-upload"><label htmlFor="score-midi">Or use your own original MIDI file</label><input id="score-midi" type="file" accept=".mid,.midi,audio/midi,audio/x-midi" disabled={!!busy} onChange={e => { setMidi(e.target.files?.[0] || null); setSelected(null); setRights(false); setScore(null); }} /><p className="score-help">Up to 2 MB. Use a piano MIDI arrangement you have permission to adapt; complex ensemble tracks may need editing.</p></div>
    <div className="score-generate-actions"><label className="rights-checkbox"><input type="checkbox" checked={rights} disabled={!!busy} onChange={e => setRights(e.target.checked)} /> I have permission to adapt this MIDI and save the resulting score.</label><button className="primary" disabled={!!busy || !rights || (!selected && !midi)} onClick={() => void generate()}>{busy === 'generate' ? <Loader2 className="spin" size={17} /> : <FileMusic size={17} />} Create piano score</button></div>
    {score && <section className="generated-score"><div className="section-title"><div><h2>{score.title}</h2><p>Draft piano score · {score.note_count} notes · {Math.round(score.duration_seconds)} seconds · {score.source.type === 'upload' ? 'Your uploaded MIDI' : 'Internet Archive MIDI'}</p>{score.source.creator && <p>Source / arranger: {score.source.creator} · {score.source.license || 'Check source permissions'}</p>}</div>{score.source.url && <a className="text-button" href={score.source.url} target="_blank" rel="noreferrer">Original MIDI <ArrowUpRight size={14} /></a>}</div>{score.source.pdf_url && <p className="score-help">An existing source PDF is also available. <a className="text-button" href={`/api/pdf?url=${encodeURIComponent(score.source.pdf_url)}`} target="_blank" rel="noreferrer">View the original score <ArrowUpRight size={14} /></a> Its notation may be more polished than this generated draft.</p>}{score.warnings.length > 0 && <div className="score-warnings"><strong>Review this draft</strong><ul>{score.warnings.map((warning, index) => <li key={index}>{warning}</li>)}</ul></div>}{rendering && <div className="score-rendering" role="status"><Loader2 className="spin" size={20} /> Rendering piano notation…</div>}<div className="score-preview-scroll"><div ref={display} className="score-notation" aria-label="Generated piano sheet music preview" /></div><div className="score-export-actions"><button className="secondary" disabled={!!busy} onClick={() => download(new Blob([score.musicxml], { type: 'application/vnd.recordare.musicxml+xml' }), filename(score.title) + '.musicxml')}><Download size={16} /> Download MusicXML</button><button className="secondary" disabled={!!busy || !rendered || !rights} onClick={() => void exportPdf()}>{busy === 'pdf' ? <Loader2 className="spin" size={16} /> : <Download size={16} />} Download PDF</button><button className="primary" disabled={!!busy || !rendered || !rights || !storageReady} onClick={() => void exportPdf(true)}>{busy === 'save' ? <Loader2 className="spin" size={16} /> : <CloudUpload size={16} />} Save PDF to {storageName}</button>{!storageReady && <button className="text-button" onClick={onConfigureStorage}>Set up {storageName} <ArrowUpRight size={14} /></button>}</div><p className="score-help">PDF exports include all rendered notation pages. Keep MusicXML to refine fingering, voicing, rests, and layout in a notation editor. Saving a PDF does not import it into Smart Pianist.</p></section>}
  </section>;
}
