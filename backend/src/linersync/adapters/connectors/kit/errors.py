"""The user-facing failure messages every connector words the same way."""

from dataclasses import dataclass

from linersync.domain.tracks import ServiceId
from linersync.ports.errors import PlaylistNotFound, ReadFailed, WriteFailed


@dataclass(frozen=True, slots=True)
class Errors:
    service: ServiceId
    name: str  # the service as the user knows it, e.g. "Spotify"

    # -- reads -------------------------------------------------------------

    def not_found(self, playlist_id: str) -> PlaylistNotFound:
        return PlaylistNotFound(
            self.service, f"{self.name} has no playlist {playlist_id}"
        )

    def not_understood(self, what: str) -> ReadFailed:
        return ReadFailed(
            self.service, f"{self.name}'s reply for {what} was not understood"
        )

    def no_length(self, playlist: str) -> ReadFailed:
        return ReadFailed(
            self.service, f'{self.name} did not say how long "{playlist}" is'
        )

    def no_version(self, playlist: str) -> ReadFailed:
        # Without a version a change during the read could go unnoticed.
        return ReadFailed(
            self.service, f'{self.name} did not give a version of "{playlist}"'
        )

    def empty_page(self, playlist: str) -> ReadFailed:
        return ReadFailed(
            self.service, f'{self.name} returned an empty page of "{playlist}"'
        )

    def too_many(self, playlist: str) -> ReadFailed:
        return ReadFailed(
            self.service,
            f'{self.name} returned more songs than it reported in "{playlist}"; '
            "it will be read again next run",
        )

    def short(self, got: int, total: int | None, playlist: str) -> ReadFailed:
        return ReadFailed(
            self.service,
            f'{self.name} returned {got} of {total} songs in "{playlist}"',
        )

    def total_moved(self, now: int, before: int, playlist: str) -> ReadFailed:
        return ReadFailed(
            self.service,
            f'{self.name} reported {now} songs in "{playlist}" after '
            f"reporting {before}; it will be read again next run",
        )

    def changed_during_read(self, playlist: str) -> ReadFailed:
        return ReadFailed(
            self.service,
            f'{self.name} playlist "{playlist}" changed while it was being read; '
            "it will be read again next run",
        )

    # -- writes ------------------------------------------------------------

    def changed(self) -> WriteFailed:
        return WriteFailed(
            self.service,
            f"The {self.name} playlist changed since it was planned; "
            "it will be replanned",
            False,
        )

    def create_not_understood(self) -> WriteFailed:
        # The playlist may well exist.
        return WriteFailed(
            self.service,
            f"{self.name}'s reply to creating a playlist was not understood",
            True,
        )

    def write_not_understood(self, what: str) -> WriteFailed:
        """A read made before a write was not understood; nothing was sent."""
        return WriteFailed(
            self.service,
            f"{self.name}'s reply for {what} was not understood",
            False,
        )
