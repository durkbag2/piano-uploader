"""Select independent MIDI track/channel parts before note-event merging."""
import io
import re
from collections import defaultdict

import mido
from fastapi import HTTPException

PIANO_PROGRAMS = ['Acoustic Grand Piano', 'Bright Acoustic Piano', 'Electric Grand Piano', 'Honky-tonk Piano', 'Electric Piano 1', 'Electric Piano 2', 'Harpsichord', 'Clavinet']


def load(content, max_bytes):
    if len(content) > max_bytes:
        raise HTTPException(413, 'MIDI exceeds the 2 MB limit.')
    if not content.startswith(b'MThd'):
        raise HTTPException(422, 'The file is not a MIDI document.')
    try:
        midi = mido.MidiFile(file=io.BytesIO(content), clip=False)
    except (ValueError, EOFError, OSError, KeyError, IndexError):
        raise HTTPException(422, 'The MIDI document could not be parsed.')
    if midi.type not in (0, 1) or midi.ticks_per_beat <= 0:
        raise HTTPException(422, 'Use format 0 or 1 MIDI with quarter-note timing.')
    if len(midi.tracks) > 128 or sum(len(track) for track in midi.tracks) > 100000:
        raise HTTPException(413, 'This MIDI has too many tracks or events for draft notation.')
    return midi


def enumerate_parts(midi):
    names = {}
    events = []
    for track_index, track in enumerate(midi.tracks):
        tick = 0
        labels = []
        for event_index, message in enumerate(track):
            tick += message.time
            if message.type in {'track_name', 'instrument_name'} and message.name.strip():
                printable = ''.join(char if ord(char) >= 32 and ord(char) != 127 else ' ' for char in message.name)
                labels.append(' '.join(printable.split())[:120])
            if not message.is_meta:
                events.append((tick, track_index, event_index, message))
        names[track_index] = ' / '.join(dict.fromkeys(labels))[:160]
    global_programs = {}
    local_programs = {}
    parts = {}
    percussion_notes = 0
    for _, track_index, _, message in sorted(events, key=lambda event: event[:3]):
        channel = getattr(message, 'channel', None)
        if channel is None:
            continue
        key = (track_index, channel)
        if message.type == 'program_change':
            global_programs[channel] = message.program
            local_programs[key] = message.program
        elif message.type == 'note_on' and message.velocity > 0:
            if channel == 9:
                percussion_notes += 1
                continue
            part = parts.setdefault(key, {'note_count': 0, 'velocity_sum': 0, 'programs': set(), 'unknown_notes': 0})
            program = local_programs.get(key, global_programs.get(channel))
            if program is None:
                part['unknown_notes'] += 1
            else:
                part['programs'].add(program)
            part['note_count'] += 1
            part['velocity_sum'] += message.velocity
    result = []
    for (track_index, channel), data in parts.items():
        programs = sorted(data['programs'])
        named_piano = 'piano' in names[track_index].casefold()
        piano = named_piano or any(0 <= program <= 7 for program in programs)
        average = data['velocity_sum'] / data['note_count']
        default_name = PIANO_PROGRAMS[programs[0]] if len(programs) == 1 and programs[0] <= 7 else (f'GM program {programs[0] + 1}' if len(programs) == 1 else 'Instrument part' if programs else 'Unlabeled MIDI part')
        label = names[track_index] or default_name
        result.append({'id': f'{track_index}:{channel}', 'label': f'{label} — track {track_index + 1}, channel {channel + 1}', 'track_index': track_index, 'channel': channel, 'programs': programs, 'program_names': [PIANO_PROGRAMS[program] if program <= 7 else f'GM program {program + 1}' for program in programs], 'note_count': data['note_count'], 'piano': piano, 'assumed_piano': not programs and not named_piano, 'avg_velocity': round(average, 2), 'activity_score': round(data['velocity_sum'] / 127, 3)})
    return sorted(result, key=lambda part: (-part['activity_score'], -part['avg_velocity'], -part['note_count'], part['track_index'], part['channel'])), percussion_notes


def filter_part(midi, selected):
    filtered = mido.MidiFile(type=midi.type, ticks_per_beat=midi.ticks_per_beat)
    for track_index, track in enumerate(midi.tracks):
        output = mido.MidiTrack()
        tick = 0
        last_kept = 0
        for message in track:
            tick += message.time
            # All conductor metadata keeps its absolute timing. Channel messages
            # from other tracks cannot pair with this part's note-off events.
            keep = message.is_meta or (track_index == selected['track_index'] and getattr(message, 'channel', None) == selected['channel'])
            if keep:
                output.append(message.copy(time=tick - last_kept))
                last_kept = tick
        filtered.tracks.append(output)
    stream = io.BytesIO()
    filtered.save(file=stream)
    return stream.getvalue()


def select_part(content, part_id='auto', max_bytes=2 * 1024 * 1024):
    if not isinstance(part_id, str) or (part_id not in {'auto', 'all'} and not re.fullmatch(r'[0-9]+:[0-9]+', part_id)):
        raise HTTPException(422, 'Choose auto, all, or a listed track:channel part ID.')
    midi = load(content, max_bytes)
    available, percussion = enumerate_parts(midi)
    if not available:
        raise HTTPException(422, 'The MIDI contains no playable non-percussion part.')
    warnings = []
    if part_id == 'all':
        selected = None
        notice = 'All non-percussion MIDI parts were included by explicit choice.'
        filtered = content
    else:
        if part_id == 'auto':
            candidates = [part for part in available if part['piano']]
            if not candidates:
                candidates = [part for part in available if part['assumed_piano']]
                if candidates:
                    warnings.append('No piano program or piano track name was supplied. An unlabeled part is treated as a possible default GM piano; verify the selected part.')
                else:
                    candidates = available
                    warnings.append('No piano part was identified. The most active non-percussion part was selected as a fallback; choose a different part if needed.')
            selected = candidates[0]
            notice = 'Selected ' + selected['label'] + ' using MIDI note activity and velocity. This estimates prominence, not perceived audibility; other parts were omitted.'
        else:
            selected = next((part for part in available if part['id'] == part_id), None)
            if not selected:
                raise HTTPException(422, 'The selected MIDI part is not present in this file.')
            notice = 'Selected ' + selected['label'] + ' by manual choice; other MIDI parts were omitted.'
        if selected['programs'] and any(program > 7 for program in selected['programs']) and not selected['piano']:
            warnings.append('The selected part is not identified as piano by its MIDI program or name; it is not automatically rearranged for piano.')
        if len(selected['programs']) > 1 and any(program > 7 for program in selected['programs']):
            warnings.append('The selected track/channel changes instruments. All its notes are included, including non-piano passages; instrument sections were not filtered.')
        filtered = filter_part(midi, selected)
    if percussion:
        warnings.append('Percussion-channel notes were omitted.')
    return filtered, {'available_parts': available, 'selected_part': selected, 'original_note_count': sum(part['note_count'] for part in available), 'percussion_note_count': percussion, 'selection_notice': notice}, warnings
