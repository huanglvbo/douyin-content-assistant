"""Recognition contract tests with a fake local model; no downloads occur."""
from pathlib import Path
import builtins
import os
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from creator_notes.recognition import RecognitionError, transcribe_file, model_inventory, select_model, ensure_model_cache


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

    def cached_model(self, directory):
        directory.mkdir(parents=True, exist_ok=True)
        for name in ('model.bin', 'config.json', 'tokenizer.json', 'vocabulary.json'):
            (directory / name).write_bytes(b'local-fixture')
        return directory

    def test_missing_requested_model_explicitly_selects_complete_cached_alternative(self):
        cache = self.directory / 'models'
        small = self.cached_model(cache / 'small')
        selection = select_model('base', cache, prefer_cached=True)
        self.assertEqual(selection, {'requested_model': 'base', 'selected_model': 'small',
            'cache_path': str(small.resolve()), 'reason': 'cached_alternative', 'download_required': False})
        model = FakeModel([segment(0, 2, 'recognized')])
        with patch('creator_notes.recognition._load_model', return_value=model) as loader:
            result = transcribe_file(self.source, cache, model='base', prefer_cached=True)
        loader.assert_called_once_with(str(small.resolve()), cache.resolve())
        self.assertEqual(result['model'], 'small')
        self.assertEqual(result['requested_model'], 'base')
        self.assertEqual(result['model_selection']['reason'], 'cached_alternative')

    def test_requested_cache_wins_and_fallback_is_never_implicit(self):
        cache = self.directory / 'models'
        self.cached_model(cache / 'small')
        miss = select_model('base', cache, prefer_cached=False)
        self.assertEqual(miss['selected_model'], 'base')
        self.assertEqual(miss['reason'], 'download_required')
        self.assertIsNone(miss['cache_path'])
        self.cached_model(cache / 'base')
        hit = select_model('base', cache)
        self.assertEqual(hit['selected_model'], 'base')
        self.assertEqual(hit['reason'], 'requested_cached')

    def test_cached_alternatives_never_reduce_balanced_or_accurate_capacity(self):
        cache = self.directory / 'models'
        for name in ('tiny', 'base'):
            self.cached_model(cache / name)
        balanced = select_model('small', cache)
        self.assertEqual(balanced['selected_model'], 'small')
        self.assertEqual(balanced['reason'], 'download_required')
        self.cached_model(cache / 'small')
        accurate = select_model('medium', cache)
        self.assertEqual(accurate['selected_model'], 'medium')
        self.assertEqual(accurate['reason'], 'download_required')
        self.cached_model(cache / 'large-v3')
        accurate = select_model('medium', cache)
        self.assertEqual(accurate['selected_model'], 'large-v3')
        self.assertEqual(accurate['reason'], 'cached_alternative')

    def test_turbo_is_not_an_unproven_capacity_substitution(self):
        cache = self.directory / 'models'
        self.cached_model(cache / 'large-v3-turbo')
        self.assertEqual(select_model('medium', cache)['reason'], 'download_required')
        (cache / 'large-v3-turbo' / 'model.bin').unlink()
        self.cached_model(cache / 'large-v3')
        self.assertEqual(select_model('large-v3-turbo', cache)['reason'], 'download_required')

    def test_huggingface_complete_snapshot_and_main_ref_are_discovered_offline(self):
        cache = self.directory / 'models'
        repository = cache / 'models--Systran--faster-whisper-small'
        chosen = self.cached_model(repository / 'snapshots' / ('a' * 40))
        self.cached_model(repository / 'snapshots' / ('f' * 40))
        (repository / 'refs').mkdir()
        (repository / 'refs' / 'main').write_text('a' * 40, encoding='utf-8')
        with patch('creator_notes.recognition._load_model') as loader:
            inventory = model_inventory(cache)
        loader.assert_not_called()
        self.assertEqual(inventory['small']['path'], str(chosen.resolve()))
        self.assertEqual(inventory['small']['layout'], 'huggingface_snapshot')

    def test_partial_and_english_only_models_are_not_cached_alternatives(self):
        cache = self.directory / 'models'
        self.cached_model(cache / 'small.en')
        partial = self.cached_model(cache / 'small')
        (partial / 'tokenizer.json').unlink()
        self.cached_model(cache / 'base')
        (cache / 'base' / 'model.bin').write_bytes(b'')
        self.assertEqual(model_inventory(cache), {})
        selection = select_model('base', cache)
        self.assertEqual(selection['reason'], 'download_required')
        self.assertTrue(selection['download_required'])

    def test_direct_named_model_root_is_supported(self):
        model = self.cached_model(self.directory / 'faster-whisper-small')
        self.assertEqual(model_inventory(model)['small']['path'], str(model.resolve()))

    def test_local_cache_model_loader_forbids_network_lookup(self):
        from creator_notes.recognition import _load_model
        model = self.cached_model(self.directory / 'small')
        with patch('faster_whisper.WhisperModel') as constructor:
            _load_model(str(model), self.directory)
        self.assertTrue(constructor.call_args.kwargs['local_files_only'])
        self.assertEqual(constructor.call_args.args[0], str(model))

    def test_canonical_blob_links_are_allowed_but_external_links_are_not(self):
        cache = self.directory / 'models'
        model = self.cached_model(cache / 'small')
        blobs = cache / 'blobs'
        blobs.mkdir()
        blob = blobs / 'weights'
        blob.write_bytes(b'weights')
        (model / 'model.bin').unlink()
        try:
            (model / 'model.bin').symlink_to(blob)
        except OSError:
            self.skipTest('Creating symbolic links requires privileges on this Windows account')
        self.assertIn('small', model_inventory(cache))
        (model / 'model.bin').unlink()
        outside = self.directory / 'outside-model.bin'
        outside.write_bytes(b'outside')
        (model / 'model.bin').symlink_to(outside)
        self.assertNotIn('small', model_inventory(cache))

    def test_preflight_uses_cached_alternative_without_network_or_model_loading(self):
        cache = self.directory / 'models'
        self.cached_model(cache / 'small')
        with patch('faster_whisper.utils.download_model') as download, \
             patch('creator_notes.recognition._load_model') as load:
            result = ensure_model_cache(cache, 'base')
        download.assert_not_called()
        load.assert_not_called()
        self.assertEqual(result['reason'], 'cached_alternative')
        self.assertEqual(result['selected_model'], 'small')
        self.assertFalse(result['downloaded_now'])

    def test_explicit_model_preparation_can_require_the_exact_requested_model(self):
        cache = self.directory / 'models'
        self.cached_model(cache / 'small')
        def complete(name, **options):
            self.assertEqual(name, 'base')
            return self.cached_model(cache / 'base')
        with patch('faster_whisper.utils.download_model', side_effect=complete) as download:
            result = ensure_model_cache(cache, 'base', prefer_cached=False)
        download.assert_called_once()
        self.assertEqual(result['selected_model'], 'base')
        self.assertTrue(result['downloaded_now'])

    def test_preflight_downloads_missing_public_model_then_verifies_complete_files(self):
        cache = self.directory / 'models'
        progress = []
        def download_fixture(name, **options):
            self.assertEqual(name, 'small')
            self.assertEqual(options['cache_dir'], str(cache.resolve()))
            self.assertFalse(options['use_auth_token'])
            return self.cached_model(cache / 'faster-whisper-small')
        with patch('faster_whisper.utils.download_model', side_effect=download_fixture) as download:
            result = ensure_model_cache(cache, 'small', lambda stage, fraction: progress.append(stage))
        download.assert_called_once()
        self.assertTrue(result['downloaded_now'])
        self.assertFalse(result['download_required'])
        self.assertEqual(result['reason'], 'requested_cached')
        self.assertIn('before collection', progress[0])

    def test_preflight_download_failure_and_partial_cache_report_safe_model_error(self):
        cache = self.directory / 'models'
        for effect in (RuntimeError('https://private.invalid/?token=secret'), None):
            with self.subTest(effect=effect), patch('faster_whisper.utils.download_model', side_effect=effect), \
                 patch('creator_notes.recognition._repair_model_cache', return_value=None):
                with self.assertRaises(RecognitionError) as caught:
                    ensure_model_cache(cache, 'base')
                self.assertEqual(caught.exception.code, 'model_unavailable')
                self.assertNotIn('token', str(caught.exception))

    def test_preflight_can_cancel_before_download(self):
        with patch('faster_whisper.utils.download_model') as download:
            with self.assertRaises(RecognitionError) as caught:
                ensure_model_cache(self.directory / 'models', 'base', lambda *args: False)
        self.assertEqual(caught.exception.code, 'cancelled')
        download.assert_not_called()

    def test_xet_is_disabled_before_public_download_import(self):
        cache = self.directory / 'models'
        real_import = builtins.__import__
        observed = []
        def observe_import(name, *args, **kwargs):
            if name == 'faster_whisper.utils':
                observed.append(os.environ.get('HF_HUB_DISABLE_XET'))
            return real_import(name, *args, **kwargs)
        def complete(*args, **kwargs):
            return self.cached_model(cache / 'small')
        with patch('faster_whisper.utils.download_model', side_effect=complete), \
             patch.dict(os.environ, {'HF_HUB_DISABLE_XET': '0'}), \
             patch('builtins.__import__', side_effect=observe_import):
            ensure_model_cache(cache, 'small')
        self.assertTrue(observed)
        self.assertTrue(all(value == '1' for value in observed))

    def test_zero_byte_snapshot_is_repaired_once_from_official_source(self):
        cache = self.directory / 'models'
        snapshot = self.cached_model(cache / 'models--Systran--faster-whisper-small' / 'snapshots' / ('a' * 40))
        (snapshot / 'model.bin').write_bytes(b'')
        self.assertNotIn('small', model_inventory(cache))
        def repair(repo, **options):
            self.assertEqual(repo, 'Systran/faster-whisper-small')
            self.assertTrue(options['force_download'])
            self.assertFalse(options['token'])
            self.assertEqual(options['endpoint'], 'https://huggingface.co')
            self.assertEqual(options['max_workers'], 2)
            self.assertEqual(os.environ['HF_HUB_DISABLE_XET'], '1')
            (snapshot / 'model.bin').write_bytes(b'repaired-public-weights')
            return str(snapshot)
        with patch('faster_whisper.utils.download_model', return_value=str(snapshot)) as first, \
             patch('huggingface_hub.snapshot_download', side_effect=repair) as second:
            result = ensure_model_cache(cache, 'small')
        first.assert_called_once()
        second.assert_called_once()
        self.assertTrue(result['cache_repaired'])
        self.assertEqual(result['reason'], 'requested_cached')

    def test_incomplete_snapshot_repair_is_bounded_and_failure_safe(self):
        cache = self.directory / 'models'
        snapshot = self.cached_model(cache / 'small')
        (snapshot / 'model.bin').write_bytes(b'')
        for effect in (None, RuntimeError('private?token=do-not-show')):
            with self.subTest(effect=effect), \
                 patch('faster_whisper.utils.download_model', return_value=str(snapshot)) as first, \
                 patch('huggingface_hub.snapshot_download', side_effect=effect) as repair:
                with self.assertRaises(RecognitionError) as caught:
                    ensure_model_cache(cache, 'small')
            first.assert_called_once()
            repair.assert_called_once()
            self.assertEqual(caught.exception.code, 'model_unavailable')
            self.assertNotIn('token', str(caught.exception))


if __name__ == '__main__':
    unittest.main()
