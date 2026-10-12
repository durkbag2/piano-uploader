import io

import mido
import pytest
from fastapi import HTTPException

from backend.score_generation import generate_score
from backend.score_playback import from_musicxml


def document(measures, divisions=24, beats=4, denominator=4):
    attributes = f'<attributes><divisions>{divisions}</divisions><time><beats>{beats}</beats><beat-type>{denominator}</beat-type></time></attributes>'
    return '<score-partwise><part id="P1">' + ''.join(f'<measure>{attributes if index == 0 else ""}{content}</measure>' for index, content in enumerate(measures)) + '</part></score-partwise>'


def note(pitch=60, duration=24, voice=1, staff=1, chord=False, ties=()):
    names = [('C', 0), ('C', 1), ('D', 0), ('D', 1), ('E', 0), ('F', 0), ('F', 1), ('G', 0), ('G', 1), ('A', 0), ('A', 1), ('B', 0)]
    if pitch is None:
        content = '<rest/>'
    else:
        step, alter = names[pitch % 12]
        content = f'<pitch><step>{step}</step><alter>{alter}</alter><octave>{pitch // 12 - 1}</octave></pitch>'
    return '<note>' + ('<chord/>' if chord else '') + content + f'<duration>{duration}</duration>' + ''.join(f'<tie type="{tie}"/>' for tie in ties) + f'<voice>{voice}</voice><staff>{staff}</staff></note>'


def tempo(bpm, offset=0):
    return f'<direction><direction-type><metronome><beat-unit>quarter</beat-unit><per-minute>{bpm}</per-minute></metronome></direction-type><offset>{offset}</offset><sound tempo="{bpm}"/></direction>'


def test_voice_chord_rest_backup_timing_uses_final_written_score():
    upper = note(None, 24) + note(60, 24) + note(64, 24, chord=True) + note(None, 48)
    lower = note(48, 96, voice=2, staff=2)
    result = from_musicxml(document([tempo(120) + upper + '<backup><duration>96</duration></backup>' + lower]))
    assert [(event['pitch'], event['start_seconds'], event['duration_seconds']) for event in result['notes']] == [(48, 0, 2), (60, 0.5, 0.5), (64, 0.5, 0.5)]
    assert result['duration_seconds'] == 2
    assert all(0 < event['velocity'] <= 1 for event in result['notes'])


def test_ties_across_bars_and_internal_chunks_make_one_attack():
    first = note(60, 72, ties=['start']) + note(60, 24, ties=['stop', 'start'])
    second = note(60, 24, ties=['stop']) + note(None, 72)
    result = from_musicxml(document([tempo(120) + first, second]))
    assert len(result['notes']) == 1
    assert result['notes'][0]['duration_seconds'] == 2.5
    assert result['duration_seconds'] == 4


def test_triplet_and_dotted_values_use_musicxml_duration_not_type_guesses():
    # 8 units at divisions24 is one eighth triplet; 36 is a dotted quarter.
    contents = tempo(120) + note(60, 8) + note(64, 8) + note(67, 8) + note(72, 36) + note(None, 36)
    result = from_musicxml(document([contents]))
    assert [event['start_seconds'] for event in result['notes']] == pytest.approx([0, 1 / 6, 1 / 3, 0.5])
    assert [event['duration_seconds'] for event in result['notes']] == pytest.approx([1 / 6, 1 / 6, 1 / 6, 0.75])


def test_tempo_changes_between_and_inside_sustained_notes_are_integrated():
    first = tempo(120) + tempo(60, 48) + note(60, 96, ties=['start'])
    second = tempo(240) + note(60, 24, ties=['stop']) + note(67, 24) + note(None, 48)
    result = from_musicxml(document([first, second]))
    # First2quarter notes at120 take1s, next2 at60 take2s, tiedquarter240 .25s.
    assert result['notes'][0]['duration_seconds'] == 3.25
    assert result['notes'][1]['start_seconds'] == 3.25
    assert result['notes'][1]['duration_seconds'] == 0.25
    assert result['duration_seconds'] == 4


def test_generic_divisions_and_metronome_beat_unit_supported():
    direction = '<direction><direction-type><metronome><beat-unit>half</beat-unit><per-minute>60</per-minute></metronome></direction-type></direction>'
    result = from_musicxml(document([direction + note(60, 2) + note(None, 6)], divisions=2))
    assert result['notes'][0]['duration_seconds'] == 0.5
    assert result['duration_seconds'] == 2


def midi(tracks):
    file = mido.MidiFile(ticks_per_beat=480)
    for events in tracks:
        track = mido.MidiTrack()
        last = 0
        for position, event in sorted(events, key=lambda event: event[0]):
            track.append(event.copy(time=position - last))
            last = position
        file.tracks.append(track)
    output = io.BytesIO()
    file.save(file=output)
    return output.getvalue()


def test_selected_piano_playback_excludes_guitar():
    piano = [(0, mido.Message('program_change', channel=0, program=0)), (0, mido.Message('note_on', channel=0, note=60, velocity=90)), (480, mido.Message('note_off', channel=0, note=60))]
    guitar = [(0, mido.Message('program_change', channel=1, program=24))]
    for index in range(6):
        guitar.extend([(index * 120, mido.Message('note_on', channel=1, note=72, velocity=100)), ((index + 1) * 120, mido.Message('note_off', channel=1, note=72))])
    result = generate_score(midi([piano, guitar]), 'Piano only')
    assert result['selected_part']['id'] == '0:0'
    assert [event['pitch'] for event in result['playback']['notes']] == [60]
    assert result['playback']['notes'][0]['duration_seconds'] == 0.5


def test_dense_reduction_playback_matches_adjusted_lengths_not_source_midi():
    events = [(0, mido.Message('note_on', note=pitch, velocity=90)) for pitch in range(60, 73)]
    events += [(480 + index * 60, mido.Message('note_off', note=pitch)) for index, pitch in enumerate(range(60, 73))]
    events += [(240, mido.Message('note_on', note=75, velocity=90)), (1440, mido.Message('note_off', note=75))]
    result = generate_score(midi([events]), 'Reduced')
    assert result['reduction_applied'] is True
    first_chord = [event for event in result['playback']['notes'] if event['start_seconds'] == 0]
    assert len(first_chord) == 13
    assert all(event['duration_seconds'] == 0.25 for event in first_chord)
    later = next(event for event in result['playback']['notes'] if event['pitch'] == 75)
    assert later['start_seconds'] == 0.25
    assert later['duration_seconds'] == 1.25


@pytest.mark.parametrize('xml', ['not XML', '<score-partwise><part><measure><note><duration>-1</duration></note></measure></part></score-partwise>', document([tempo(0) + note(60)]), document(['<backup><duration>24</duration></backup>'])])
def test_invalid_timing_is_rejected_without_negative_events(xml):
    with pytest.raises(HTTPException) as error:
        from_musicxml(xml)
    assert error.value.status_code == 422


def test_direction_after_note_uses_current_cursor_plus_offset():
    contents = tempo(120) + note(60, 24) + tempo(60, 24) + note(64, 72)
    result = from_musicxml(document([contents]))
    assert result['notes'][0]['duration_seconds'] == 0.5
    assert result['notes'][1]['start_seconds'] == 0.5
    assert result['notes'][1]['duration_seconds'] == 2.5
    assert result['duration_seconds'] == 3
