import io
from xml.etree import ElementTree as ET

import mido
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from backend import app as service
from backend import midi_parts
from backend import score_generation as scores


def track(events, name='', program=None, channel=0):
    output = mido.MidiTrack()
    if name:
        output.append(mido.MetaMessage('track_name', name=name))
    if program is not None:
        output.append(mido.Message('program_change', program=program, channel=channel))
    previous = 0
    for tick, message in sorted(events, key=lambda event: event[0]):
        output.append(message.copy(time=tick - previous))
        previous = tick
    return output


def notes(count, channel=0, pitch=60, velocity=80, start=0):
    events = []
    for index in range(count):
        onset = start + index * 240
        events.extend([(onset, mido.Message('note_on', channel=channel, note=pitch, velocity=velocity)), (onset + 120, mido.Message('note_off', channel=channel, note=pitch, velocity=0))])
    return events


def file_bytes(tracks, midi_type=1):
    midi = mido.MidiFile(type=midi_type, ticks_per_beat=480)
    midi.tracks.extend(tracks)
    stream = io.BytesIO()
    midi.save(file=stream)
    return stream.getvalue()


def test_auto_selects_piano_over_busier_guitar_and_drums():
    data = file_bytes([track(notes(3, 0), 'Main piano', program=0, channel=0), track(notes(12, 1), 'Guitar', program=25, channel=1), track(notes(20, 9, pitch=36), 'Drums', program=0, channel=9)])
    result = scores.generate_score(data, 'Band')
    assert result['selected_part']['id'] == '0:0'
    assert result['selected_part']['piano'] is True
    assert result['original_note_count'] == 15
    assert result['selected_note_count'] == result['note_count'] == 3
    assert len(result['available_parts']) == 2
    assert result['percussion_note_count'] == 20
    assert any('Percussion' in warning for warning in result['warnings'])
    assert 'perceived audibility' in result['selection_notice']
    assert result['selection_notice'] in ET.fromstring(result['musicxml']).findtext('identification/rights')


def test_two_piano_parts_rank_note_activity_and_velocity():
    data = file_bytes([track(notes(6, 0, velocity=20), 'Quiet piano', program=0, channel=0), track(notes(4, 1, velocity=100), 'Louder piano', program=1, channel=1)])
    result = scores.generate_score(data, 'Two keyboards')
    assert result['selected_part']['id'] == '1:1'
    assert result['note_count'] == 4
    assert result['selected_part']['avg_velocity'] == 100


def test_format0_multichannel_selects_channel_before_merging():
    events = [(0, mido.Message('program_change', channel=0, program=0)), (0, mido.Message('program_change', channel=1, program=48))]
    events += notes(2, 0, pitch=64) + notes(5, 1, pitch=72)
    data = file_bytes([track(events)], midi_type=0)
    result = scores.generate_score(data, 'Format zero')
    assert result['selected_part']['id'] == '0:0'
    assert result['note_count'] == 2
    manual = scores.generate_score(data, 'Strings override', part_id='0:1')
    assert manual['note_count'] == 5
    assert manual['selected_part']['piano'] is False
    assert any('not identified as piano' in warning for warning in manual['warnings'])


def test_shared_channel_different_tracks_never_pair_wrong_noteoffs():
    first = [(0, mido.Message('note_on', channel=0, note=60, velocity=90)), (960, mido.Message('note_off', channel=0, note=60))]
    second = [(240, mido.Message('note_on', channel=0, note=60, velocity=80)), (480, mido.Message('note_off', channel=0, note=60))]
    data = file_bytes([track(first), track(second)])
    selected, metadata, warnings = midi_parts.select_part(data, '0:0')
    assert scores.parse_midi(selected)[0] == [(0, 48, 60)]
    selected, _, _ = midi_parts.select_part(data, '1:0')
    assert scores.parse_midi(selected)[0] == [(12, 24, 60)]
    assert [part['id'] for part in metadata['available_parts']] == ['0:0', '1:0']


def test_conductor_tempo_meter_key_changes_preserve_absolute_offsets():
    conductor = track([(0, mido.MetaMessage('set_tempo', tempo=600000)), (0, mido.MetaMessage('time_signature', numerator=3, denominator=4)), (0, mido.MetaMessage('key_signature', key='D')), (960, mido.MetaMessage('set_tempo', tempo=750000))])
    data = file_bytes([conductor, track(notes(3, 0, start=480), 'Piano', program=0)])
    selected, _, _ = midi_parts.select_part(data)
    parsed_notes, tempos, meter, key, duration, warnings = scores.parse_midi(selected)
    assert parsed_notes[0][0] == 24
    assert meter == (3, 4) and key == 'D'
    assert tempos == [(0, 600000), (48, 750000)]
    root = ET.fromstring(scores.generate_score(data, 'Conductor')['musicxml'])
    assert root.findtext('.//time/beats') == '3'
    assert root.findtext('.//key/fifths') == '2'


def test_manual_and_all_override_preserve_available_parts():
    data = file_bytes([track(notes(2), 'Piano left', program=0), track(notes(3, 1, pitch=72), 'Piano right', program=0, channel=1)])
    auto = scores.generate_score(data, 'Auto')
    assert auto['selected_part']['id'] == '1:1'
    manual = scores.generate_score(data, 'Manual', part_id='0:0')
    assert manual['note_count'] == 2
    assert 'manual choice' in manual['selection_notice']
    combined = scores.generate_score(data, 'All', part_id='all')
    assert combined['selected_part'] is None
    assert combined['original_note_count'] == combined['note_count'] == 5
    assert len(combined['available_parts']) == 2


def test_unlabeled_default_and_non_piano_fallback_are_explicit():
    unknown = file_bytes([track(notes(2)), track(notes(4, 1))])
    result = scores.generate_score(unknown, 'Unlabeled')
    assert result['selected_part']['id'] == '1:1'
    assert result['selected_part']['assumed_piano'] is True
    assert any('possible default GM piano' in warning for warning in result['warnings'])
    guitar = file_bytes([track(notes(3), 'Guitar', program=25)])
    result = scores.generate_score(guitar, 'Fallback')
    assert result['selected_part']['piano'] is False
    assert any('No piano part' in warning for warning in result['warnings'])


def test_piano_track_name_identifies_missing_program():
    data = file_bytes([track(notes(2), 'Piano RH'), track(notes(8, 1), 'Unknown', channel=1)])
    result = scores.generate_score(data, 'Names')
    assert result['selected_part']['id'] == '0:0'
    assert result['selected_part']['piano'] is True


@pytest.mark.parametrize('part_id', ['bad', '-1:0', '0:16', '4:0', '0:9', '../track', '', None])
def test_invalid_or_missing_part_id_rejected(part_id):
    with pytest.raises(HTTPException) as error:
        midi_parts.select_part(file_bytes([track(notes(1))]), part_id)
    assert error.value.status_code == 422


def test_track_label_control_characters_do_not_break_musicxml():
    result = scores.generate_score(file_bytes([track(notes(1), 'Piano\x00 lead')]), 'Controls')
    root = ET.fromstring(result['musicxml'])
    assert 'Piano lead' in result['selected_part']['label']
    assert '\x00' not in root.findtext('identification/rights')


def test_http_raw_part_selection_and_invalid_id(tmp_path, monkeypatch):
    monkeypatch.setattr(service, 'STATE_DIR', tmp_path / 'state')
    client = TestClient(service.app)
    data = file_bytes([track(notes(2), 'Piano A'), track(notes(4, 1), 'Piano B', channel=1)])
    response = client.post('/api/scores/from-midi', params={'title': 'Band', 'rights_confirmed': True, 'part_id': '0:0'}, content=data, headers={'Content-Type': 'audio/midi'})
    assert response.status_code == 200
    assert response.json()['note_count'] == 2
    assert response.json()['selected_part']['id'] == '0:0'
    invalid = client.post('/api/scores/from-midi', params={'title': 'Band', 'rights_confirmed': True, 'part_id': 'bad'}, content=data, headers={'Content-Type': 'audio/midi'})
    assert invalid.status_code == 422


def test_http_remote_part_selection_keeps_original_cached_source_bytes(tmp_path, monkeypatch):
    monkeypatch.setattr(service, 'STATE_DIR', tmp_path / 'state')
    from backend import score_sources
    data = file_bytes([track(notes(2), 'Piano A'), track(notes(4, 1), 'Piano B', channel=1)])
    calls = []
    async def download(url, title, source_page_url=None, cache_dir=None):
        calls.append((url, cache_dir))
        return data, {'type': 'bitmidi', 'creator': 'Unknown', 'license': 'Check source', 'metadata_verified': True}
    monkeypatch.setattr(score_sources, 'download_source', download)
    response = TestClient(service.app).post('/api/scores/from-source', json={'title': 'Source', 'midi_url': 'https://bitmidi.com/uploads/1.mid', 'rights_confirmed': True, 'part_id': '0:0'})
    assert response.status_code == 200
    assert response.json()['note_count'] == 2
    assert response.json()['original_note_count'] == 6
    assert calls[0][1] == service.STATE_DIR / 'midi-cache'


def test_mixed_piano_non_piano_programs_warn_without_removing_sections():
    events = notes(1, pitch=60)
    events += [(240, mido.Message('program_change', channel=0, program=24))]
    events += notes(1, pitch=67, start=240)
    result = scores.generate_score(file_bytes([track(events, program=0)]), 'Mixed programs')
    assert result['selected_part']['programs'] == [0, 24]
    assert result['selected_part']['piano'] is True
    assert result['note_count'] == 2
    assert any('including non-piano passages' in warning for warning in result['warnings'])
    generic = scores.generate_score(file_bytes([track(notes(1), program=24)]), 'Generic label')
    assert generic['selected_part']['label'].startswith('GM program 25')
