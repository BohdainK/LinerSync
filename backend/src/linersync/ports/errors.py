from linersync.domain.tracks import ServiceId


class ConnectorError(Exception):
    def __init__(
        self, service: ServiceId, message: str = "", *, auth_required: bool = False
    ) -> None:
        super().__init__(f"{service}: {message}" if message else str(service))
        self.service = service
        self.message = message
        self.auth_required = auth_required


class ReadFailed(ConnectorError):
    """Errors, timeouts, auth failures, partial pages."""


class PlaylistNotFound(ConnectorError):
    """Authoritative: the playlist does not exist or is no longer accessible."""


class WriteFailed(ConnectorError):
    def __init__(
        self,
        service: ServiceId,
        message: str = "",
        maybe_applied: bool = False,
        *,
        auth_required: bool = False,
    ) -> None:
        super().__init__(service, message, auth_required=auth_required)
        self.maybe_applied = maybe_applied
