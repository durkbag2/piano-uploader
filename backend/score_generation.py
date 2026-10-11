"""Bounded MIDI-to-grand-staff draft notation, with source attribution."""
import io
import math
from collections import defaultdict, deque
from xml.etree import ElementTree as ET

import mido
from fastapi import HTTPException

MAX_MIDI_BYTES = 2 * 1024 * 1024
MAX_DURATION = 15 * 60
MAX_MEASURES = 256
DIVISIONS = 24  # Supports sixteenth notes, dotted rhythms and eighth triplets.
ADAPTATION = 'Adapted: inferred hands and quantized rhythms.'
KEYS = {'Cb': -7, 'Gb': -6, 'Db': -5, 'Ab': -4, 'Eb': -3, 'Bb': -2, 'F': -1, 'C': 0, 'G': 1, 'D': 2, 'A': 3, 'E': 4, 'B': 5, 'F#': 6, 'C#': 7,
        'Abm': -7, 'Ebm': -6, 'Bbm': -5, 'Fm': -4, 'Cm': -3, 'Gm': -2, 'Dm': -1, 'Am': 0, 'Em': 1, 'Bm': 2, 'F#m': 3, 'C#m': 4, 'G#m': 5, 'D#m': 6, 'A#m': 7}
# duration, type, dots, triplet; smallest supported unit is a 64th-note triplet.
RHYTHMS = sorted([(144, 'whole', 1, False), (96, 'whole', 0, False), (84, 'half', 2, False), (72, 'half', 1, False), (48, 'half', 0, False), (42, 'quarter', 2, False), (36, 'quarter', 1, False), (32, 'half', 0, True), (24, 'quarter', 0, False), (21, 'eighth', 2, False), (18, 'eighth', 1, False), (16, 'quarter', 0, True), (12, 'eighth', 0, False), (9, '16th', 1, False), (8, 'eighth', 0, True), (6, '16th', 0, False), (4, '16th', 0, True), (3, '32nd', 0, False), (2, '32nd', 0, True), (1, '64th', 0, True)], reverse=True)


def add(parent, name, text=None, **attrs):
    child = ET.SubElement(parent, name, attrs)
    if text is not None:
        child.text = str(text)
    return child


def parse_midi(content):
    if len(content) > MAX_MIDI_BYTES:
        raise HTTPException(413, 'MIDI exceeds the 2 MB limit.')
    if not content.startswith(b'MThd'):
        raise HTTPException(422, 'The file is not a MIDI document.')
    try:
        midi = mido.MidiFile(file=io.BytesIO(content), clip=False)
    except (ValueError, EOFError, OSError, KeyError, IndexError):
        raise HTTPException(422, 'The MIDI document could not be parsed.')
    if midi.type not in (0, 1) or midi.ticks_per_beat <= 0:
        raise HTTPException(422, 'Use a format 0 or 1 MIDI with quarter-note timing; asynchronous and SMPTE MIDI are unsupported.')
    if len(midi.tracks) > 128 or sum(len(track) for track in midi.tracks) > 100000:
        raise HTTPException(413, 'This MIDI has too many tracks or events for draft notation.')
    active = defaultdict(deque)
    notes = []
    tempos = []
    meters = []
    keys = []
    tick = 0
    elapsed = 0
    tempo = 500000
    percussion = False
    sustain = False
    for message in mido.merge_tracks(midi.tracks):
        tick += message.time
        elapsed += mido.tick2second(message.time, midi.ticks_per_beat, tempo)
        if elapsed > MAX_DURATION:
            raise HTTPException(413, 'MIDI exceeds the 15-minute draft limit.')
        if message.type == 'set_tempo':
            tempo = message.tempo
            if tempo <= 0:
                raise HTTPException(422, 'The MIDI tempo must be positive.')
            tempos.append((tick, tempo))
        elif message.type == 'time_signature':
            meters.append((tick, message.numerator, message.denominator))
        elif message.type == 'key_signature':
            keys.append((tick, message.key))
        elif message.type == 'control_change' and message.control == 64:
            sustain = True
        elif message.type in ('note_on', 'note_off'):
            if message.channel == 9:
                percussion = True
                continue
            key = (message.channel, message.note)
            if message.type == 'note_on' and message.velocity > 0:
                active[key].append(tick)
            elif active[key]:
                start = active[key].popleft()
                if tick > start:
                    notes.append((start, tick, message.note))
    unfinished = False
    for (_, pitch), starts_for_key in active.items():
        for start in starts_for_key:
            if tick > start:
                notes.append((start, tick, pitch))
                unfinished = True
    if not notes:
        raise HTTPException(422, 'The MIDI contains no playable non-percussion notes.')
    quantize = lambda value: int(math.floor(value * DIVISIONS / midi.ticks_per_beat + 0.5))
    notes = sorted(set((quantize(start), max(quantize(start) + 1, quantize(end)), pitch) for start, end, pitch in notes))
    warnings = ['Draft notation: hands are inferred by pitch and rhythms are quantized; this is not a professional piano arrangement.']
    if percussion:
        warnings.append('Percussion-channel notes were omitted.')
    if sustain:
        warnings.append('Sustain-pedal controls were omitted; written note durations use MIDI note releases.')
    if unfinished:
        warnings.append('Notes without note-off events were closed at the MIDI end.')
    if not meters:
        warnings.append('No time signature was supplied; 4/4 is assumed.')
    if not tempos:
        warnings.append('No tempo was supplied; 120 quarter notes per minute is assumed.')
    meter = meters[0][1:] if meters and meters[0][0] == 0 else (4, 4)
    if any((n, d) != meter for _, n, d in meters):
        warnings.append('Time-signature changes are not supported in this draft; the opening meter is used throughout.')
    numerator, denominator = meter
    if numerator < 1 or numerator > 32 or denominator not in (1, 2, 4, 8, 16, 32):
        raise HTTPException(422, 'The opening time signature is unsupported for draft notation.')
    key = keys[0][1] if keys and keys[0][0] == 0 else 'C'
    if not keys or keys[0][0] != 0:
        warnings.append('No opening key signature was supplied; C major is displayed and note accidentals preserve pitch.')
    if any(k != key for _, k in keys):
        warnings.append('Key-signature changes are not supported in this draft; the opening key is retained.')
    if not tempos or tempos[0][0] > 0:
        tempos.insert(0, (0, 500000))
    tempo_positions = {}
    for tick, tempo in tempos:
        tempo_positions[quantize(tick)] = tempo
    return notes, sorted(tempo_positions.items()), meter, key, round(elapsed, 3), warnings


def allocate_voices(notes, staff):
    groups = defaultdict(list)
    for start, end, pitch in notes:
        if (pitch >= 60) == (staff == 1):
            groups[(start, end)].append(pitch)
    voices = []
    voice_ends = []
    for (start, end), pitches in sorted(groups.items()):
        index = next((i for i, last in enumerate(voice_ends) if last <= start), len(voices))
        if index == len(voices):
            voices.append([])
            voice_ends.append(0)
        if len(voices) > 12:
            return reduce_to_chords(notes, staff), True
        voices[index].append((start, end, sorted(set(pitches))))
        voice_ends[index] = end
    return voices or [[]], False


def reduce_to_chords(notes, staff):
    """Keep distinct pitch attacks while simplifying dense releases to one voice."""
    attacks = defaultdict(list)
    for start, end, pitch in notes:
        if (pitch >= 60) == (staff == 1):
            attacks[start].append((end, pitch))
    starts = sorted(attacks)
    voice = []
    for index, start in enumerate(starts):
        members = attacks[start]
        end = max(end for end, _ in members)
        if index + 1 < len(starts):
            end = min(end, starts[index + 1])
        voice.append((start, end, sorted({pitch for _, pitch in members})))
    return [voice]


def chunks(duration):
    while duration:
        rhythm = next(value for value in RHYTHMS if value[0] <= duration)
        yield rhythm
        duration -= rhythm[0]


def write_piece(measure, pitches, duration, staff, voice, start, original_start, original_end, fifths):
    for amount, note_type, dots, triplet in chunks(duration):
        for index, pitch in enumerate(pitches or [None]):
            note = add(measure, 'note')
            if index:
                add(note, 'chord')
            if pitch is None:
                add(note, 'rest')
            else:
                names = [('C', 0), ('D', -1), ('D', 0), ('E', -1), ('E', 0), ('F', 0), ('G', -1), ('G', 0), ('A', -1), ('A', 0), ('B', -1), ('B', 0)] if fifths < 0 else [('C', 0), ('C', 1), ('D', 0), ('D', 1), ('E', 0), ('F', 0), ('F', 1), ('G', 0), ('G', 1), ('A', 0), ('A', 1), ('B', 0)]
                step, alter = names[pitch % 12]
                value = add(note, 'pitch')
                add(value, 'step', step)
                if alter:
                    add(value, 'alter', alter)
                add(value, 'octave', pitch // 12 - 1)
            add(note, 'duration', amount)
            stops = pitch is not None and start > original_start
            begins = pitch is not None and start + amount < original_end
            if stops:
                add(note, 'tie', type='stop')
            if begins:
                add(note, 'tie', type='start')
            add(note, 'voice', voice)
            add(note, 'type', note_type)
            for _ in range(dots):
                add(note, 'dot')
            if triplet:
                modulation = add(note, 'time-modification')
                add(modulation, 'actual-notes', 3)
                add(modulation, 'normal-notes', 2)
                add(modulation, 'normal-type', note_type)
            add(note, 'staff', staff)
            group = amount * 3 if triplet else None
            tuplet_start = triplet and start % group == 0
            tuplet_stop = triplet and (start + amount) % group == 0
            if stops or begins or (index == 0 and (tuplet_start or tuplet_stop)):
                notations = add(note, 'notations')
                if stops:
                    add(notations, 'tied', type='stop')
                if begins:
                    add(notations, 'tied', type='start')
                if index == 0 and tuplet_start:
                    add(notations, 'tuplet', type='start', number='1')
                if index == 0 and tuplet_stop:
                    add(notations, 'tuplet', type='stop', number='1')
        start += amount


def generate_score(content, title, source=None):
    notes, tempos, meter, key, duration, warnings = parse_midi(content)
    (upper, upper_reduced), (lower, lower_reduced) = allocate_voices(notes, 1), allocate_voices(notes, 2)
    reduced = upper_reduced or lower_reduced
    adaptation = ADAPTATION
    if reduced:
        staves = ' and '.join(name for name, changed in [('Treble', upper_reduced), ('bass', lower_reduced)] if changed)
        warnings.append(staves + ' voices were reduced to onset-aligned chords because the MIDI has dense overlaps. Distinct pitches and attacks are retained; note lengths are harmonized within each chord and clipped at the next attack. Review sustain and voicing in MusicXML.')
        adaptation += ' Dense overlapping voices reduced to onset chords; note lengths adjusted.'
    source = source or {'type': 'upload', 'title': title, 'creator': 'User-provided MIDI', 'license': 'User-confirmed permissions'}
    measure_length = meter[0] * DIVISIONS * 4 // meter[1]
    end = max(end for _, end, _ in notes)
    measure_count = math.ceil(end / measure_length)
    if measure_count > MAX_MEASURES:
        raise HTTPException(413, 'This MIDI exceeds the 256-measure draft limit. Use a shorter excerpt.')
    root = ET.Element('score-partwise', {'version': '4.0'})
    add(add(root, 'work'), 'work-title', title)
    identity = add(root, 'identification')
    add(identity, 'creator', 'MIDI source: ' + source.get('creator', 'User-provided MIDI'), type='arranger')
    license_text = source.get('license', 'Check source')
    if source.get('license_url') and source['license_url'] not in license_text:
        license_text += ' (' + source['license_url'] + ')'
    add(identity, 'rights', 'Source license: ' + license_text + '. ' + adaptation)
    add(add(identity, 'encoding'), 'software', 'Piano Uploader MIDI draft')
    if source.get('page_url') or source.get('url'):
        add(identity, 'source', source.get('page_url') or source['url'])
    listing = add(root, 'part-list')
    part_info = add(listing, 'score-part', id='P1')
    add(part_info, 'part-name', 'Piano')
    part = add(root, 'part', id='P1')
    fifths = KEYS.get(key, 0)
    voices = [(1, i + 1, voice) for i, voice in enumerate(upper)] + [(2, len(upper) + i + 1, voice) for i, voice in enumerate(lower)]
    for number in range(measure_count):
        left, right = number * measure_length, (number + 1) * measure_length
        measure = add(part, 'measure', number=str(number + 1))
        if number == 0:
            attributes = add(measure, 'attributes')
            add(attributes, 'divisions', DIVISIONS)
            key_xml = add(attributes, 'key')
            add(key_xml, 'fifths', fifths)
            add(key_xml, 'mode', 'minor' if key.endswith('m') else 'major')
            time_xml = add(attributes, 'time')
            add(time_xml, 'beats', meter[0])
            add(time_xml, 'beat-type', meter[1])
            add(attributes, 'staves', 2)
            for staff, sign, line in [(1, 'G', 2), (2, 'F', 4)]:
                clef = add(attributes, 'clef', number=str(staff))
                add(clef, 'sign', sign)
                add(clef, 'line', line)
        for position, tempo in tempos:
            if left <= position < right:
                direction = add(measure, 'direction', placement='above')
                metronome = add(add(direction, 'direction-type'), 'metronome')
                add(metronome, 'beat-unit', 'quarter')
                add(metronome, 'per-minute', round(mido.tempo2bpm(tempo), 3))
                add(direction, 'offset', position - left)
                add(direction, 'staff', 1)
                add(direction, 'sound', tempo=str(round(mido.tempo2bpm(tempo), 3)))
        active_voices = {voice_id for _, voice_id, intervals in voices if any(end > left and start < right for start, end, _ in intervals)}
        visible_voice = {}
        for staff, voice_id, _ in voices:
            if staff not in visible_voice or (visible_voice[staff] not in active_voices and voice_id in active_voices):
                visible_voice[staff] = voice_id
        for voice_index, (staff, voice_id, intervals) in enumerate(voices):
            if voice_index:
                add(add(measure, 'backup'), 'duration', measure_length)
            note_offset = len(measure)
            cursor = left
            for start, end, pitches in intervals:
                if end <= left or start >= right:
                    continue
                clipped_start, clipped_end = max(start, left), min(end, right)
                if clipped_start > cursor:
                    write_piece(measure, [], clipped_start - cursor, staff, voice_id, cursor, cursor, clipped_start, fifths)
                write_piece(measure, pitches, clipped_end - clipped_start, staff, voice_id, clipped_start, start, end, fifths)
                cursor = clipped_end
            if cursor < right:
                write_piece(measure, [], right - cursor, staff, voice_id, cursor, cursor, right, fifths)
            if voice_id not in active_voices and voice_id != visible_voice[staff]:
                for element in list(measure)[note_offset:]:
                    if element.tag == 'note':
                        element.set('print-object', 'no')
    if len(root.findall('.//note')) > 20000:
        raise HTTPException(413, 'This MIDI produces too many notation symbols. Use a shorter, simpler excerpt.')
    musicxml = ET.tostring(root, encoding='utf-8', xml_declaration=True).decode('utf-8')
    return {'musicxml': musicxml, 'title': title, 'warnings': warnings, 'source': source, 'note_count': len(notes), 'duration_seconds': duration, 'reduction_applied': reduced}
