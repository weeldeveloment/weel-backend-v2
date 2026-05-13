from whitenoise.storage import CompressedManifestStaticFilesStorage


class CustomStaticFilesStorage(CompressedManifestStaticFilesStorage):
    """Static files storage that skips missing files instead of raising ValueError.

    This fixes issues with third-party packages (e.g. drf-yasg) that reference
    static files which may not exist in the collected static files.
    """

    manifest_strict = False
