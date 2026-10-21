"""Recognition contract tests with a fake local model; no downloads occur."""
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from creator_notes.recognition import RecognitionError, transcribe_file


class FakeModel:
    def __init__(self, segments, duration=10):
        self.segments = segments
        self.duration = duration
        self.calls = []

    def transcribe(self, source, **options):
        self.calls.append((source, options))
        return iter(self.segments), SimpleNamespace(duration=self.duration, language='zh')


def segment(start, end, text):
    return SimpleNamespace(start=start, end=end, text=text)


class RecognitionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.source = self.directory / '\u793a\u4f8b \u58f0\u97f3.mp4'
        self.source.write_bytes(b'fake-media')
        self.addCleanup(self.temp.cleanup)

    def run_model(self, model, **options):
        with patch('creator_notes.recognition._load_model', return_value=model):
            return transcribe_file(self.source, self.directory / '\u6a21\u578b cache', **options)

    def test_full_segments_gaps_unicode_and_cpu_transcription_options(self):
        model = FakeModel([segment(0, 2, ' \u4f60\u597d '), segment(7, 9, '\u5199\u4ee3\u7801\u3002')])
        progress = []
        result = self.run_model(model, progress=lambda stage, fraction: progress.append((stage, fraction)))
        self.assertEqual(result['transcript'], '\u4f60\u597d\n\u5199\u4ee3\u7801\u3002')
        self.assertEqual(len(result['segments']), 2)
        self.assertEqual(result['segments'][1], {'start': 7.0, 'end': 9.0, 'text': '\u5199\u4ee3\u7801\u3002'})
        self.assertEqual(result['duration'], 10.0)
        self.assertEqual(result['model'], 'small')
        self.assertEqual(result['language'], 'zh')
        self.assertEqual(model.calls[0][1]['beam_size'], 5)
        self.assertEqual(model.calls[0][1]['vad_filter'], True)
        self.assertEqual(progress[-1][1], 1.0)

    def test_no_speech_does_not_fabricate_transcript(self):
        with self.assertRaises(RecognitionError) as caught:
            self.run_model(FakeModel([segment(0, 1, '  ')]))
        self.assertEqual(caught.exception.code, 'no_speech')

    def test_invalid_duration_or_segment_is_rejected(self):
        for model in (FakeModel([], float('nan')), FakeModel([], float('inf')),
                      FakeModel([], 0), FakeModel([], 14401),
                      FakeModel([segment(-1, 1, '\u5b57')]),
                      FakeModel([segment(1, 3, '\u5b57'), segment(0, 4, '\u5b57')]),
                      FakeModel([segment(2, 1, '\u5b57')]),
                      FakeModel([segment(2, 11, '\u5b57')]),
                      FakeModel([segment(True, 2, '\u5b57')])):
            with self.subTest(model=model), self.assertRaises(RecognitionError):
                self.run_model(model)

    def test_last_segment_small_rounding_overshoot_is_bounded(self):
        result = self.run_model(FakeModel([segment(8, 10.1, '\u7ed3\u675f')]))
        self.assertEqual(result['segments'][0]['end'], 10.0)

    def test_cancel_between_segments(self):
        model = FakeModel([segment(0, 2, '\u7b2c\u4e00\u6bb5'), segment(2, 4, '\u7b2c\u4e8c\u6bb5')])
        def progress(stage, fraction):
            if fraction is not None and fraction > 0:
                return False
        with self.assertRaises(RecognitionError) as caught:
            self.run_model(model, progress=progress)
        self.assertEqual(caught.exception.code, 'cancelled')

    def test_invalid_source_and_model_rejected_before_model_loading(self):
        with patch('creator_notes.recognition._load_model') as loader:
            with self.assertRaises(RecognitionError):
                transcribe_file(self.directory / 'missing.mp4', self.directory)
            with self.assertRaises(RecognitionError):
                transcribe_file(self.source, self.directory, model='https://unexpected.example/model')
            self.assertFalse(loader.called)

    def test_decoder_error_is_safe(self):
        class Broken:
            def transcribe(self, *args, **kwargs):
                raise RuntimeError('secret-file-or-url?token=do-not-expose')
        with self.assertRaises(RecognitionError) as caught:
            self.run_model(Broken())
        self.assertEqual(caught.exception.code, 'transcription_failed')
        self.assertNotIn('token', str(caught.exception))


if __name__ == '__main__':
    unittest.main()
