import io
from xml.etree import ElementTree as ET

import httpx
import mido
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from backend import app as service
from backend import score_generation as scores
from backend import score_sources as sources


def midi(events, meter=(4, 4), key='C', tempo=500000, midi_type=1):
    output = mido.MidiFile(type=midi_type, ticks_per_beat=480)
    track = mido.MidiTrack()
    output.tracks.append(track)
    track.append(mido.MetaMessage('set_tempo', tempo=tempo))
    track.append(mido.MetaMessage('time_signature', numerator=meter[0], denominator=meter[1]))
    track.append(mido.MetaMessage('key_signature', key=key))
    last = 0
    for tick, message in sorted(events, key=lambda event: event[0]):
        track.append(message.copy(time=tick - last))
        last = tick
    stream = io.BytesIO()
    output.save(file=stream)
    return stream.getvalue()


def on(pitch, channel=0):
    return mido.Message('note_on', note=pitch, velocity=80, channel=channel)


def off(pitch, channel=0):
    return mido.Message('note_off', note=pitch, velocity=0, channel=channel)


def assert_measure_timing(root):
    attributes = root.find('.//attributes')
    divisions = int(attributes.findtext('divisions'))
    beats = int(attributes.findtext('time/beats'))
    denominator = int(attributes.findtext('time/beat-type'))
    expected = beats * divisions * 4 // denominator
    for measure in root.findall('.//measure'):
        cursor = 0
        voice_lengths = {}
        for element in measure:
            if element.tag == 'backup':
                assert cursor == expected
                cursor -= int(element.findtext('duration'))
                assert cursor == 0
            elif element.tag == 'note' and element.find('chord') is None:
                value = int(element.findtext('duration'))
                cursor += value
                voice = element.findtext('voice')
                voice_lengths[voice] = voice_lengths.get(voice, 0) + value
        assert cursor == expected
        assert all(value == expected for value in voice_lengths.values())


def test_chords_grandstaff_rests_meter_tempo_key_and_escape():
    data = midi([(0, on(60)), (0, on(64)), (0, on(48)), (480, off(60)), (480, off(64)), (720, off(48))], meter=(3, 4), key='Bb', tempo=600000)
    result = scores.generate_score(data, 'Moonlight & <Piano>')
    root = ET.fromstring(result['musicxml'])
    assert root.findtext('work/work-title') == 'Moonlight & <Piano>'
    assert root.findtext('.//staves') == '2'
    assert root.findtext('.//key/fifths') == '-2'
    assert root.find('.//sound').attrib['tempo'] == '100.0'
    assert len(root.findall('.//chord')) == 1
    assert {note.findtext('staff') for note in root.findall('.//note')} == {'1', '2'}
    assert root.find('.//rest') is not None
    assert_measure_timing(root)


def test_polyphonic_sustained_notes_tie_across_bars_and_preserve_voicing():
    data = midi([(0, on(60)), (480, on(67)), (960, off(67)), (2400, off(60))])
    result = scores.generate_score(data, 'Sustained')
    assert result['reduction_applied'] is False
    root = ET.fromstring(result['musicxml'])
    assert len(root.findall('.//measure')) == 2
    assert root.find('.//tie[@type="start"]') is not None
    assert root.find('.//tie[@type="stop"]') is not None
    assert len({note.findtext('voice') for note in root.findall('.//note') if note.findtext('staff') == '1'}) == 2
    assert_measure_timing(root)


def test_triplets_preserve_exact_eighth_rhythm_without_distortion():
    data = midi([(0, on(60)), (160, off(60)), (160, on(64)), (320, off(64)), (320, on(67)), (480, off(67))], meter=(2, 2))
    root = ET.fromstring(scores.generate_score(data, 'Triplets')['musicxml'])
    pitched = [note for note in root.findall('.//note') if note.find('pitch') is not None]
    assert [int(note.findtext('duration')) for note in pitched] == [8, 8, 8]
    assert all(note.findtext('time-modification/actual-notes') == '3' for note in pitched)
    assert all(note.findtext('type') == 'eighth' for note in pitched)
    assert root.find('.//tuplet[@type="start"]') is not None
    assert root.find('.//tuplet[@type="stop"]') is not None
    assert_measure_timing(root)


def test_dotted_rhythm_and_source_attribution():
    data = midi([(0, on(60)), (720, off(60))])
    source = {'type': 'archive', 'creator': 'Chordzy', 'license': 'CC BY 4.0', 'license_url': 'https://creativecommons.org/licenses/by/4.0/', 'page_url': 'https://archive.org/details/source'}
    root = ET.fromstring(scores.generate_score(data, 'Draft', source)['musicxml'])
    note = next(note for note in root.findall('.//note') if note.find('pitch') is not None)
    assert note.findtext('type') == 'quarter'
    assert note.find('dot') is not None
    assert 'Chordzy' in root.findtext('identification/creator')
    assert source['license_url'] in root.findtext('identification/rights')
    assert 'Adapted' in root.findtext('identification/rights')
    assert root.findtext('identification/source') == source['page_url']


def test_percussion_omitted_and_non_piano_tracks_are_draft():
    data = midi([(0, on(35, 9)), (480, off(35, 9)), (0, on(60)), (480, off(60))])
    result = scores.generate_score(data, 'Mixed')
    assert result['note_count'] == 1
    assert any('Percussion' in warning for warning in result['warnings'])


@pytest.mark.parametrize('data', [b'not-midi', b'MThd\x00', b''])
def test_malformed_midi_rejected(data):
    with pytest.raises(HTTPException) as error:
        scores.generate_score(data, 'Bad')
    assert error.value.status_code == 422


def test_format2_and_empty_notes_rejected():
    for data in [midi([], midi_type=2), midi([]), midi([(0, on(35, 9)), (480, off(35, 9))])]:
        with pytest.raises(HTTPException) as error:
            scores.generate_score(data, 'Unsupported')
        assert error.value.status_code == 422


def test_duration_measure_and_file_limits(monkeypatch):
    data = midi([(0, on(60)), (480, off(60))])
    monkeypatch.setattr(scores, 'MAX_MIDI_BYTES', len(data) - 1)
    with pytest.raises(HTTPException) as error:
        scores.generate_score(data, 'Large')
    assert error.value.status_code == 413
    monkeypatch.setattr(scores, 'MAX_MIDI_BYTES', 2 * 1024 * 1024)
    with pytest.raises(HTTPException) as error:
        scores.generate_score(midi([(0, on(60)), (480 * 2000, off(60))]), 'Long')
    assert error.value.status_code == 413
    monkeypatch.setattr(scores, 'MAX_MEASURES', 1)
    with pytest.raises(HTTPException) as error:
        scores.generate_score(midi([(0, on(60)), (480 * 5, off(60))]), 'Many bars')
    assert error.value.status_code == 413


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(service, 'STATE_DIR', tmp_path / 'state')
    return TestClient(service.app)


def test_raw_midi_endpoint_rights_type_stream_limit_and_success(client, monkeypatch):
    data = midi([(0, on(60)), (480, off(60))])
    assert client.post('/api/scores/from-midi?title=Song', content=data, headers={'Content-Type': 'audio/midi'}).status_code == 400
    assert client.post('/api/scores/from-midi?title=Song&rights_confirmed=true', content=data, headers={'Content-Type': 'text/plain'}).status_code == 415
    response = client.post('/api/scores/from-midi?title=Song&rights_confirmed=true', content=data, headers={'Content-Type': 'audio/midi'})
    assert response.status_code == 200
    assert response.json()['source']['type'] == 'upload'
    assert response.json()['note_count'] == 1
    monkeypatch.setattr(scores, 'MAX_MIDI_BYTES', 5)
    assert client.post('/api/scores/from-midi?title=Song&rights_confirmed=true', content=iter([data]), headers={'Content-Type': 'application/octet-stream'}).status_code == 413


@pytest.mark.parametrize('url', ['http://archive.org/download/a/a.mid', 'https://evil.example/song.mid', 'https://archive.org.evil.example/download/a/a.mid', 'https://archive.org:bad/download/a/a.mid', 'https://user:pass@archive.org/download/a/a.mid'])
def test_source_ssrf_rejected(url):
    with pytest.raises(HTTPException) as error:
        sources.validate_url(url)
    assert error.value.status_code == 400


def mock_http(monkeypatch, handler):
    original = httpx.AsyncClient
    monkeypatch.setattr(sources.httpx, 'AsyncClient', lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs))


def test_remote_score_requires_rights_and_verifies_metadata_attribution(client, monkeypatch):
    data = midi([(0, on(60)), (480, off(60))])
    calls = []
    def respond(request):
        calls.append(request)
        assert 'authorization' not in request.headers
        if request.url.path.startswith('/metadata/'):
            return httpx.Response(200, json={'metadata': {'title': 'Original', 'creator': 'Chordzy', 'licenseurl': 'https://creativecommons.org/licenses/by/4.0/'}, 'files': [{'name': 'score.pdf'}]})
        return httpx.Response(200, content=data)
    mock_http(monkeypatch, respond)
    payload = {'title': 'Draft', 'midi_url': 'https://archive.org/download/id/score.midi'}
    assert client.post('/api/scores/from-source', json=payload).status_code == 400
    payload['rights_confirmed'] = True
    response = client.post('/api/scores/from-source', json=payload)
    assert response.status_code == 200
    source = response.json()['source']
    assert source['creator'] == 'Chordzy'
    assert source['metadata_verified'] is True
    assert source['pdf_url'] == 'https://archive.org/download/id/score.pdf'
    assert len(calls) == 2


def test_remote_redirect_cannot_reach_foreign_host(client, monkeypatch):
    calls = []
    def respond(request):
        calls.append(request)
        return httpx.Response(302, headers={'location': 'https://127.0.0.1/private'})
    mock_http(monkeypatch, respond)
    response = client.post('/api/scores/from-source', json={'title': 'Draft', 'midi_url': 'https://archive.org/download/id/score.mid', 'rights_confirmed': True})
    assert response.status_code == 400
    assert len(calls) == 1


def test_midi_search_uses_actual_files_bounds_and_encodes_query(client, monkeypatch):
    def respond(request):
        if request.url.path == '/advancedsearch.php':
            assert 'Für Elise & Piano' in request.url.params['q']
            return httpx.Response(200, json={'response': {'docs': [{'identifier': 'good', 'title': 'Title'}, {'identifier': 'bad', 'title': 'Unavailable'}]}})
        if request.url.path == '/metadata/bad':
            return httpx.Response(503)
        return httpx.Response(200, json={'metadata': {'creator': 'Creator'}, 'files': [{'name': 'score.mid', 'source': 'original', 'size': '100'}, {'name': 'score.pdf'}, {'name': 'private.mid', 'private': 'true'}, {'name': 'large.mid', 'size': str(scores.MAX_MIDI_BYTES + 1)}]})
    mock_http(monkeypatch, respond)
    response = client.get('/api/scores/search', params={'q': 'Für Elise & Piano'})
    assert response.status_code == 200
    data = response.json()
    assert len(data['results']) == 1
    assert data['results'][0]['midi_url'] == 'https://archive.org/download/good/score.mid'
    assert data['results'][0]['pdf_url'] == 'https://archive.org/download/good/score.pdf'
    assert 'error' in data


def test_single_title_and_inactive_voices_hidden_without_changing_timing():
    # Two overlapping treble voices in bar one; only the sustained voice remains
    # in bar two. The inactive voice retains invisible timing rests.
    data = midi([(0, on(60)), (480, on(67)), (960, off(67)), (2400, off(60))])
    root = ET.fromstring(scores.generate_score(data, 'One title')['musicxml'])
    assert len([element for element in root.iter() if element.text == 'One title']) == 1
    assert root.find('movement-title') is None
    second = root.findall('.//measure')[1]
    hidden = [note for note in second.findall('note') if note.attrib.get('print-object') == 'no']
    assert hidden and all(note.find('rest') is not None for note in hidden)
    # Empty bass staff still has one visible rest voice.
    bass = [note for note in second.findall('note') if note.findtext('staff') == '2']
    assert bass and all(note.attrib.get('print-object') != 'no' for note in bass)
    assert_measure_timing(root)


def test_uploaded_midi_over_five_thousand_notes_generates_complete_score(client):
    events = []
    for step in range(1501):
        start = step * 120
        for pitch in (48, 55, 60, 64):
            events.extend([(start, on(pitch)), (start + 60, off(pitch))])
    data = midi(events)
    response = client.post('/api/scores/from-midi?title=Large%20piano%20score&rights_confirmed=true', content=data, headers={'Content-Type': 'audio/midi'})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result['note_count'] == 6004
    root = ET.fromstring(result['musicxml'])
    assert len(root.findall('.//note/pitch')) == 6004
    assert_measure_timing(root)


def test_dense_overlap_reduces_both_hands_preserving_distinct_pitch_attacks(client):
    events = [(0, on(pitch)) for pitch in list(range(36, 49)) + list(range(60, 73))]
    events += [(480 + index * 60, off(pitch)) for index, pitch in enumerate(list(range(36, 49)) + list(range(60, 73)))]
    events += [(240, on(75)), (1440, off(75))]
    data = midi(events)
    response = client.post('/api/scores/from-midi?title=Dense%20MIDI&rights_confirmed=true', content=data, headers={'Content-Type': 'audio/midi'})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result['reduction_applied'] is True
    assert result['note_count'] == 27
    assert any('note lengths' in warning for warning in result['warnings'])
    root = ET.fromstring(result['musicxml'])
    assert 'note lengths adjusted' in root.findtext('identification/rights')
    assert len({note.findtext('voice') for note in root.findall('.//note')}) == 2
    assert_measure_timing(root)
    expected_notes = scores.parse_midi(data)[0]
    expected = {(start, pitch) for start, end, pitch in expected_notes}
    actual = set()
    for index, measure in enumerate(root.findall('.//measure')):
        cursor = 0
        last_attack = 0
        for element in measure:
            if element.tag == 'backup':
                cursor -= int(element.findtext('duration'))
            elif element.tag == 'note':
                if element.find('chord') is None:
                    last_attack = cursor
                    cursor += int(element.findtext('duration'))
                pitch = element.find('pitch')
                if pitch is not None and element.find('tie[@type="stop"]') is None:
                    number = (int(pitch.findtext('octave')) + 1) * 12 + {'C': 0, 'D': 2, 'E': 4, 'F': 5, 'G': 7, 'A': 9, 'B': 11}[pitch.findtext('step')] + int(pitch.findtext('alter', '0'))
                    actual.add((index * 96 + last_attack, number))
    assert actual == expected
