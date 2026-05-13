from whitenoise.storage import CompressedManifestStaticFilesStorage


class CustomStaticFilesStorage(CompressedManifestStaticFilesStorage):
    """Static files storage that skips missing files instead of raising ValueError.

    This fixes issues with third-party packages (e.g. drf-yasg) that reference
    static files which may not exist in the collected static files.
    """

    manifest_strict = False

    def url(self, name, force=False):
        try:
            return super().url(name, force=force)
        except ValueError:
            # Return the original path if the file is not found
            return f"{self.base_url}{name}"
