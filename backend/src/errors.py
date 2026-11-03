"""Failures caused by the file the user submitted.

These are not outages. Workers must not retry them, and they must not be
reported as tracked exceptions: a video-only upload used to surface later as
``No audio stream`` from the same ``Exception`` site as AssemblyAI 5xx
transcription errors, so the two shared one PostHog fingerprint and paged
on-call.
"""


NO_AUDIO_ERROR_CODE = "no_audio"
VIDEO_NO_AUDIO = "VIDEO_NO_AUDIO"
NO_AUDIO_MESSAGE = (
    "This video has no audio track. SupoClip needs speech to find clips. "
    "Please upload a file with sound or paste the YouTube link instead. "
    "No minutes were used."
)


class UserInputError(Exception):
    """A problem with the user's media. Not retried and not tracked as a crash."""

    retryable = False
    error_code = "user_input"

    @property
    def fingerprint(self) -> str:
        """Stable PostHog grouping key, distinct from provider transcription errors."""
        return f"user-input:{self.error_code}"


class MissingAudioError(UserInputError):
    """The upload has a picture and no audio stream."""

    error_code = NO_AUDIO_ERROR_CODE

    def __init__(self) -> None:
        super().__init__(NO_AUDIO_MESSAGE)
