"""Runtime paths are separate from source and are configurable per user."""
from dataclasses import dataclass
import os
from pathlib import Path


@dataclass(frozen=True)
class AppPaths:
    home: Path

    @classmethod
    def current(cls):
        base = Path(os.environ.get('LOCALAPPDATA', Path.home() / '.local' / 'share'))
        return cls(Path(os.environ.get('CREATOR_NOTES_HOME', base / 'CreatorNotes')).expanduser().resolve())

    @property
    def session(self):
        return self.home / 'session'

    @property
    def reports(self):
        return Path(os.environ.get('CREATOR_NOTES_OUTPUT', self.home / 'reports')).expanduser().resolve()

    @property
    def models(self):
        return Path(os.environ.get('CREATOR_NOTES_MODELS', self.home / 'models')).expanduser().resolve()

    @property
    def work(self):
        return self.home / 'work'

    def prepare(self):
        for directory in (self.home, self.session, self.reports, self.models, self.work):
            directory.mkdir(parents=True, exist_ok=True)
        return self
