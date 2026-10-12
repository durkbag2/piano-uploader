import { useEffect, useRef, useState } from 'react';
import { Pause, Play, Square, Volume2 } from 'lucide-react';
import './score-playback.css';

export type PlaybackNote = { pitch: number; start_seconds: number; duration_seconds: number; velocity?: number };
export type Playback = { notes: PlaybackNote[]; duration_seconds: number };
type Voice = { oscillators: OscillatorNode[]; gain: GainNode };
const timestamp = (value: number) => `${Math.floor(value / 60)}:${String(Math.floor(value % 60)).padStart(2, '0')}`;
export default function ScorePlayback({ playback, title }: { playback: Playback; title: string }) {
  const [playing, setPlaying] = useState(false);
  const [position, setPosition] = useState(0);
  const [speed, setSpeed] = useState(1);
  const [error, setError] = useState('');
  const context = useRef<AudioContext | null>(null);
  const output = useRef<AudioNode | null>(null);
  const voices = useRef<Set<Voice>>(new Set());
  const timer = useRef<number | undefined>(undefined);
  const active = useRef(false);
  const offset = useRef(0);
  const anchor = useRef(0);
  const rate = useRef(1);
  const nextNote = useRef(0);
  const generation = useRef(0);
  const notes = useRef<PlaybackNote[]>([]);
  const duration = useRef(0);
  function currentPosition() {
    return Math.min(duration.current, Math.max(0, offset.current + (active.current && context.current ? (context.current.currentTime - anchor.current) * rate.current : 0)));
  }
  function silence() {
    window.clearInterval(timer.current); timer.current = undefined;
    for (const voice of voices.current) {
      for (const oscillator of voice.oscillators) { try { oscillator.stop(); } catch { /* Already stopped. */ } oscillator.disconnect(); }
      voice.gain.disconnect();
    }
    voices.current.clear();
  }
  function pause() {
    generation.current++; offset.current = currentPosition(); active.current = false;
    silence(); setPlaying(false); setPosition(offset.current);
  }
  function stop() { pause(); offset.current = 0; setPosition(0); }
  function sound(note: PlaybackNote, startAt: number, remaining: number, elapsed = 0) {
    const audio = context.current; if (!audio || !output.current || remaining <= 0) return;
    const start = Math.max(audio.currentTime, startAt);
    const end = start + remaining / rate.current;
    const gain = audio.createGain();
    const velocity = Math.max(0, Math.min(1, (note.velocity ?? 0.72) > 1 ? (note.velocity ?? 0.72) / 127 : note.velocity ?? 0.72));
    const amplitude = Math.max(0.0002, 0.09 * velocity * Math.exp(-Math.min(elapsed, 5) * 0.6));
    gain.gain.setValueAtTime(0.0001, start);
    gain.gain.linearRampToValueAtTime(amplitude, start + Math.min(0.008, (end - start) / 3));
    gain.gain.exponentialRampToValueAtTime(Math.max(0.0001, amplitude * Math.exp(-Math.min(remaining / rate.current, 6) * 0.6)), end);
    gain.gain.exponentialRampToValueAtTime(0.0001, end + 0.09);
    gain.connect(output.current);
    const frequency = 440 * 2 ** ((note.pitch - 69) / 12);
    const fundamental = audio.createOscillator(); fundamental.type = 'triangle'; fundamental.frequency.value = frequency;
    const harmonic = audio.createOscillator(); harmonic.type = 'sine'; harmonic.frequency.value = frequency * 2;
    const mix = audio.createGain(); mix.gain.value = 0.16; harmonic.connect(mix); mix.connect(gain); fundamental.connect(gain);
    const voice = { oscillators: [fundamental, harmonic], gain }; voices.current.add(voice);
    let ended = 0;
    const onEnded = () => { if (++ended === 2) { voices.current.delete(voice); fundamental.disconnect(); harmonic.disconnect(); mix.disconnect(); gain.disconnect(); } };
    fundamental.onended = onEnded; harmonic.onended = onEnded;
    fundamental.start(start); harmonic.start(start); fundamental.stop(end + 0.1); harmonic.stop(end + 0.1);
  }
  function tick() {
    const audio = context.current; if (!audio || !active.current) return;
    const now = currentPosition();
    const horizon = Math.min(duration.current, now + 0.15 * rate.current);
    while (nextNote.current < notes.current.length && notes.current[nextNote.current].start_seconds <= horizon) {
      const note = notes.current[nextNote.current++];
      const elapsed = Math.max(0, now - note.start_seconds);
      const remaining = note.duration_seconds - elapsed;
      if (remaining > 0) sound(note, audio.currentTime + Math.max(0, note.start_seconds - now) / rate.current, remaining, elapsed);
    }
    setPosition(now);
    if (now >= duration.current) { pause(); offset.current = duration.current; setPosition(duration.current); }
  }
  async function play() {
    const attempt = ++generation.current;
    try {
      if (!context.current) {
        const Constructor = window.AudioContext || (window as Window & { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
        if (!Constructor) throw new Error('Audio preview is not supported in this browser.');
        context.current = new Constructor();
        const master = context.current.createGain(); master.gain.value = 0.7;
        const compressor = context.current.createDynamicsCompressor();
        compressor.threshold.value = -18; compressor.ratio.value = 6;
        master.connect(compressor); compressor.connect(context.current.destination); output.current = master;
      }
      await context.current.resume();
      if (attempt !== generation.current) return;
      if (offset.current >= duration.current) offset.current = 0;
      const audio = context.current;
      anchor.current = audio.currentTime; active.current = true; setPlaying(true); setError('');
      nextNote.current = notes.current.findIndex(note => note.start_seconds >= offset.current);
      if (nextNote.current < 0) nextNote.current = notes.current.length;
      // Resuming or seeking into a held note keeps its remaining duration.
      for (let index = 0; index < nextNote.current; index++) {
        const note = notes.current[index];
        if (note.start_seconds + note.duration_seconds > offset.current) sound(note, audio.currentTime, note.start_seconds + note.duration_seconds - offset.current, offset.current - note.start_seconds);
      }
      tick(); timer.current = window.setInterval(tick, 50);
    } catch (e) { if (attempt === generation.current) { offset.current = currentPosition(); active.current = false; silence(); setPlaying(false); setPosition(offset.current); setError(e instanceof Error ? e.message : 'Could not start the audio preview.'); } }
  }
  function seek(value: number) {
    const resume = active.current; pause(); offset.current = Math.max(0, Math.min(duration.current, value)); setPosition(offset.current);
    if (resume) void play();
  }
  function changeSpeed(value: number) {
    const resume = active.current; pause(); rate.current = value; setSpeed(value); if (resume) void play();
  }
  useEffect(() => {
    stop(); setError(''); notes.current = playback.notes.filter(note => Number.isFinite(note.pitch) && note.pitch >= 0 && note.pitch <= 127 && Number.isFinite(note.start_seconds) && note.start_seconds >= 0 && Number.isFinite(note.duration_seconds) && note.duration_seconds > 0).sort((a, b) => a.start_seconds - b.start_seconds);
    duration.current = Math.max(0, Number.isFinite(playback.duration_seconds) ? playback.duration_seconds : 0);
    return () => {
      generation.current++; active.current = false; silence(); output.current?.disconnect(); output.current = null;
      const audio = context.current; context.current = null; if (audio) void audio.close().catch(() => {});
    };
  }, [playback]);
  return <section className="score-playback" aria-label={`Playback of ${title}`}><div className="playback-heading"><Volume2 size={18} /><div><strong>Listen to this score</strong><p>Synthesized piano preview of the final notation, including the selected part and written rhythms.</p></div></div><div className="playback-controls"><button className="primary" disabled={!(playback.duration_seconds > 0) || !playback.notes.length} onClick={() => playing ? pause() : void play()} aria-label={playing ? 'Pause score playback' : 'Play score playback'}>{playing ? <Pause size={17} /> : <Play size={17} />}{playing ? 'Pause' : 'Play'}</button><button className="secondary" onClick={stop} aria-label="Stop score playback"><Square size={14} /> Stop</button><span className="playback-time" aria-label="Playback position">{timestamp(position)} / {timestamp(playback.duration_seconds)}</span><label className="playback-speed">Speed<select aria-label="Playback speed" value={speed} onChange={e => changeSpeed(Number(e.target.value))}>{[0.5, 0.75, 1, 1.25, 1.5, 2].map(value => <option key={value} value={value}>{value}×</option>)}</select></label></div><label className="playback-seek"><span className="sr-only">Seek within score playback</span><input type="range" min={0} max={Math.max(0, playback.duration_seconds)} step="0.01" value={position} onChange={e => seek(Number(e.target.value))} aria-label="Seek within score playback" aria-valuetext={`${timestamp(position)} of ${timestamp(playback.duration_seconds)}`} /></label>{error && <p className="playback-error" role="alert">{error}</p>}</section>;
}
