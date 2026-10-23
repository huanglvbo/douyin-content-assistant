"""Bounded full-input timeline recovery; controlled recognizers, no network."""
import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from creator_notes.recognition import RecognitionError, transcribe_file


def segment(start, end, text='Public speech'):
    return SimpleNamespace(start=start, end=end, text=text)


class Passes:
    def __init__(self, outcomes, duration=30.07125):
        self.outcomes = outcomes
        self.duration = duration
        self.calls = []

    def transcribe(self, source, **options):
        self.calls.append((source, options))
        outcome = self.outcomes[len(self.calls) - 1]
        if isinstance(outcome, Exception):
            raise outcome
        return iter(outcome), SimpleNamespace(duration=self.duration, language='zh')


class RecognitionRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='recognition-recovery-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / 'saved.mp4'
        self.source.write_bytes(b'controlled-full-input')
        self.source_hash = hashlib.sha256(self.source.read_bytes()).hexdigest()

    def run_model(self, model, **kwargs):
        with patch('creator_notes.recognition._load_model', return_value=model) as loader:
            result = transcribe_file(self.source, self.root / 'models', **kwargs)
        loader.assert_called_once()
        return result

    def assert_same_complete_input(self, model):
        self.assertTrue(all(source == str(self.source.resolve()) for source, options in model.calls))
        self.assertTrue(all('clip_timestamps' not in options for source, options in model.calls))
        self.assertTrue(all(options['condition_on_previous_text'] is False for source, options in model.calls))
        self.assertEqual(hashlib.sha256(self.source.read_bytes()).hexdigest(), self.source_hash)

    def test_valid_first_pass_does_not_retry_or_change_output(self):
        model = Passes([[segment(0, 1, 'Opening'), segment(28.4, 28.88, 'OK')]])
        result = self.run_model(model)
        self.assertEqual(len(model.calls), 1)
        self.assertNotIn('chunk_length', model.calls[0][1])
        self.assertNotIn('timeline_recovery', result)
        self.assertEqual(result['segments'][1], {'start': 28.4, 'end': 28.88, 'text': 'OK'})
        self.assert_same_complete_input(model)

    def test_invalid_tail_replays_full_input_once_with_shorter_chunks(self):
        model = Passes([[segment(0, 1, 'Opening'), segment(30, 32, 'Unverified padded credit')],
                        [segment(0, 1.32, 'Opening'), segment(26.28, 27.28, 'OK'), segment(28.44, 29.44, 'OK')]])
        result = self.run_model(model)
        self.assertEqual(len(model.calls), 2)
        self.assertEqual(model.calls[1][1]['chunk_length'], 20)
        self.assertEqual(result['duration'], 30.07125)
        self.assertEqual(result['transcript'], 'Opening\nOK\nOK')
        self.assertNotIn('Unverified padded credit', result['transcript'])
        audit = result['timeline_recovery']
        self.assertEqual(audit['retry_count'], 1)
        self.assertTrue(audit['full_input_replayed'])
        self.assertFalse(audit['input_cropped'])
        self.assertEqual(audit['attempts'][0]['failure']['start'], 30)
        self.assertEqual(audit['attempts'][0]['failure']['end'], 32)
        self.assertEqual(audit['attempts'][0]['failure']['segment_index'], 1)
        self.assertEqual(audit['attempts'][1]['status'], 'passed')
        self.assertEqual(audit['attempts'][1]['segments'], 3)
        self.assertNotIn('Unverified padded credit', json.dumps(audit))
        self.assert_same_complete_input(model)

    def test_both_invalid_passes_fail_without_a_third_attempt(self):
        model = Passes([[segment(30, 32)], [segment(31, 32)]])
        with self.assertRaises(RecognitionError) as caught:
            self.run_model(model)
        self.assertEqual(caught.exception.code, 'invalid_timeline')
        self.assertEqual(len(model.calls), 2)
        attempts = caught.exception.timeline_recovery['attempts']
        self.assertEqual([attempt['status'] for attempt in attempts], ['invalid_timeline', 'invalid_timeline'])
        self.assertEqual([attempt['failure']['start'] for attempt in attempts], [30, 31])
        self.assert_same_complete_input(model)

    def test_unrelated_decoder_failure_is_not_retried_or_exposed(self):
        model = Passes([RuntimeError('secret-url?token=private')])
        with self.assertRaises(RecognitionError) as caught:
            self.run_model(model)
        self.assertEqual(caught.exception.code, 'transcription_failed')
        self.assertEqual(len(model.calls), 1)
        self.assertNotIn('token', str(caught.exception))
        self.assertFalse(hasattr(caught.exception, 'timeline_recovery'))

    def test_no_speech_and_invalid_transcript_are_not_retried(self):
        for outcome, code in (([], 'no_speech'), ([segment(0, 1, 'bad\x00text')], 'invalid_transcript')):
            with self.subTest(code=code):
                model = Passes([outcome])
                with self.assertRaises(RecognitionError) as caught:
                    self.run_model(model)
                self.assertEqual(caught.exception.code, code)
                self.assertEqual(len(model.calls), 1)

    def test_duration_limit_is_not_retried(self):
        model = Passes([[]], duration=0)
        with self.assertRaises(RecognitionError) as caught:
            self.run_model(model)
        self.assertEqual(caught.exception.code, 'duration_limit')
        self.assertEqual(len(model.calls), 1)

    def test_retry_decoder_failure_keeps_safe_first_timing_audit(self):
        model = Passes([[segment(30, 32)], RuntimeError('secret-url?token=private')])
        with self.assertRaises(RecognitionError) as caught:
            self.run_model(model)
        self.assertEqual(caught.exception.code, 'transcription_failed')
        audit = caught.exception.timeline_recovery
        self.assertEqual(len(audit['attempts']), 2)
        self.assertEqual(audit['attempts'][1]['status'], 'transcription_failed')
        self.assertEqual(audit['attempts'][0]['failure']['end'], 32)
        self.assertNotIn('token', json.dumps(audit))
        self.assertEqual(len(model.calls), 2)

    def test_cancel_before_retry_keeps_failure_and_does_not_replay(self):
        model = Passes([[segment(30, 32)]])
        def progress(stage, fraction):
            return not stage.startswith('Retrying the full audio')
        with self.assertRaises(RecognitionError) as caught:
            self.run_model(model, progress=progress)
        self.assertEqual(caught.exception.code, 'cancelled')
        self.assertEqual(len(model.calls), 1)
        self.assertEqual(caught.exception.timeline_recovery['retry_count'], 0)
        self.assertFalse(caught.exception.timeline_recovery['full_input_replayed'])

    def test_nonfinite_timing_audit_is_json_safe_and_bounded(self):
        model = Passes([[segment(float('nan'), 2)], [segment(float('inf'), 2)]])
        with self.assertRaises(RecognitionError) as caught:
            self.run_model(model)
        audit = caught.exception.timeline_recovery
        json.dumps(audit, allow_nan=False)
        self.assertIsNone(audit['attempts'][0]['failure']['start'])
        self.assertIsNone(audit['attempts'][1]['failure']['start'])
        self.assertEqual(len(model.calls), 2)


if __name__ == '__main__':
    unittest.main()
