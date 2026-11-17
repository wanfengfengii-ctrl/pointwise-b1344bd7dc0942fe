class LiveRecordError(Exception):
    pass


class InputError(LiveRecordError, ValueError):
    pass


class SourceConflict(LiveRecordError):
    pass


class FetchError(LiveRecordError):
    pass


class PlaylistError(InputError):
    pass


class DeclarationConflict(PlaylistError):
    pass


class IntegrityError(LiveRecordError):
    pass


class ArchiveConflict(LiveRecordError):
    pass


class PublicationError(LiveRecordError):
    pass


class CaptureConflict(LiveRecordError):
    pass


class CaptureNotFound(LiveRecordError):
    pass


class ExportConflict(LiveRecordError):
    pass
