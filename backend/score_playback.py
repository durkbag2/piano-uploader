"""Playback events from final written MusicXML, including reductions and ties."""
import math
from bisect import bisect_right
from fractions import Fraction
from xml.etree import ElementTree as ET

from fastapi import HTTPException

MAX_XML_BYTES = 8 * 1024 * 1024
MAX_WRITTEN_NOTES = 20000
STEPS = {'C': 0, 'D': 2, 'E': 4, 'F': 5, 'G': 7, 'A': 9, 'B': 11}
BEAT_UNITS = {'whole': 4, 'half': 2, 'quarter': 1, 'eighth': 0.5, '16th': 0.25, '32nd': 0.125}


def quarter_tempo(direction):
    sound = direction.find('sound')
    if sound is not None and sound.get('tempo'):
        value = float(sound.get('tempo'))
    else:
        metronome = direction.find('direction-type/metronome')
        if metronome is None:
            return None
        unit = BEAT_UNITS.get(metronome.findtext('beat-unit'), 1)
        dots = len(metronome.findall('beat-unit-dot'))
        value = float(metronome.findtext('per-minute')) * unit * (2 - 2 ** -dots)
    if not math.isfinite(value) or value <= 0:
        raise ValueError('Tempo must be positive and finite.')
    return value


def from_musicxml(musicxml):
    if len(musicxml.encode('utf-8')) > MAX_XML_BYTES:
        raise HTTPException(413, 'The written score is too large for playback.')
    try:
        root = ET.fromstring(musicxml)
        count = 0
        events = []
        tempo_events = {Fraction(0): 120.0}
        score_end = Fraction(0)
        for part in root.findall('part'):
            divisions = 1
            measure_length = Fraction(4)
            measure_start = Fraction(0)
            tied = {}
            for measure in part.findall('measure'):
                cursor = Fraction(0)
                maximum = Fraction(0)
                last_attack = {}
                for element in measure:
                    if element.tag == 'attributes':
                        if element.find('divisions') is not None:
                            divisions = int(element.findtext('divisions'))
                            if divisions <= 0:
                                raise ValueError('Divisions must be positive.')
                        time = element.find('time')
                        if time is not None:
                            measure_length = Fraction(int(time.findtext('beats')) * 4, int(time.findtext('beat-type')))
                            if measure_length <= 0:
                                raise ValueError('Meter must be positive.')
                    elif element.tag == 'direction':
                        tempo = quarter_tempo(element)
                        if tempo is not None:
                            position = measure_start + cursor + Fraction(element.findtext('offset', '0')) / divisions
                            if position < 0:
                                raise ValueError('Tempo position cannot be negative.')
                            tempo_events[position] = tempo
                    elif element.tag in {'backup', 'forward'}:
                        duration = Fraction(element.findtext('duration')) / divisions
                        if duration < 0:
                            raise ValueError('Duration cannot be negative.')
                        cursor += duration * (-1 if element.tag == 'backup' else 1)
                        if cursor < 0:
                            raise ValueError('Voice timing cannot precede the measure.')
                        maximum = max(maximum, cursor)
                    elif element.tag == 'note':
                        count += 1
                        if count > MAX_WRITTEN_NOTES:
                            raise HTTPException(413, 'The written score has too many notes for playback.')
                        if element.find('grace') is not None:
                            continue
                        duration = Fraction(element.findtext('duration')) / divisions
                        if duration <= 0:
                            raise ValueError('Note duration must be positive.')
                        voice = (element.findtext('staff', '1'), element.findtext('voice', '1'))
                        if element.find('chord') is None:
                            start = measure_start + cursor
                            last_attack[voice] = start
                            cursor += duration
                        else:
                            if voice not in last_attack:
                                raise ValueError('Chord has no preceding note in its voice.')
                            start = last_attack[voice]
                        end = start + duration
                        maximum = max(maximum, cursor, end - measure_start)
                        pitch_xml = element.find('pitch')
                        if pitch_xml is None or element.find('rest') is not None:
                            continue
                        pitch = (int(pitch_xml.findtext('octave')) + 1) * 12 + STEPS[pitch_xml.findtext('step')] + int(pitch_xml.findtext('alter', '0'))
                        if not 0 <= pitch <= 127 or start < 0:
                            raise ValueError('Playback pitch or position is outside its supported range.')
                        key = (*voice, pitch)
                        ties = {tie.get('type') for tie in element.findall('tie')}
                        prior = tied.get(key)
                        if 'stop' in ties and prior is not None and prior['end'] == start:
                            prior['end'] = end
                            event = prior
                        else:
                            event = {'pitch': pitch, 'start': start, 'end': end}
                            events.append(event)
                        if 'start' in ties:
                            tied[key] = event
                        else:
                            tied.pop(key, None)
                measure_start += max(measure_length, maximum)
            score_end = max(score_end, measure_start)
        points = sorted(tempo_events)
        cumulative = [0.0]
        for index in range(1, len(points)):
            cumulative.append(cumulative[-1] + float(points[index] - points[index - 1]) * 60 / tempo_events[points[index - 1]])
        def seconds(position):
            index = max(0, bisect_right(points, position) - 1)
            return cumulative[index] + float(position - points[index]) * 60 / tempo_events[points[index]]
        notes = []
        for event in sorted(events, key=lambda value: (value['start'], value['pitch'], value['end'])):
            start = seconds(event['start'])
            duration = seconds(event['end']) - start
            if not math.isfinite(start) or not math.isfinite(duration) or start < 0 or duration <= 0:
                raise ValueError('Playback timing must be positive and finite.')
            notes.append({'pitch': event['pitch'], 'start_seconds': round(start, 9), 'duration_seconds': round(duration, 9), 'velocity': 0.72})
        total = seconds(score_end)
        if not math.isfinite(total) or total < 0:
            raise ValueError('Playback length must be nonnegative and finite.')
        return {'notes': notes, 'duration_seconds': round(total, 9)}
    except (ET.ParseError, ValueError, TypeError, KeyError, ZeroDivisionError, OverflowError):
        raise HTTPException(422, 'The final score contains unsupported or invalid playback timing.')
