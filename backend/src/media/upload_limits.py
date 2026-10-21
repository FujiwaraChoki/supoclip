"""Shared copy for the configured upload size cap."""

# Multipart boundaries and part headers are small next to the video. The
# declared Content-Length may exceed the file by this much and still be a
# legal upload of a file that sits on the cap.
MULTIPART_FRAMING_BYTES = 1024 * 1024


def format_upload_limit(max_bytes: int) -> str:
    """Human size for the cap, using decimal GB to match the client."""
    if max_bytes >= 1_000_000_000:
        gigabytes = max_bytes / 1_000_000_000
        rounded = round(gigabytes, 1)
        if abs(rounded - round(rounded)) < 0.05:
            return f"{round(rounded):.0f} GB"
        return f"{rounded:.1f} GB"
    megabytes = max(1, round(max_bytes / 1_000_000))
    return f"{megabytes} MB"


def content_length_values(headers: list[tuple[bytes, bytes]]) -> list[str]:
    return [
        value.decode("latin-1").strip()
        for name, value in headers
        if name.lower() == b"content-length"
    ]


def transfer_encoding_values(headers: list[tuple[bytes, bytes]]) -> list[str]:
    return [
        value.decode("latin-1").strip()
        for name, value in headers
        if name.lower() == b"transfer-encoding"
    ]


def reject_content_length(lengths: list[str], encodings: list[str], max_bytes: int) -> str | None:
    """Classify a declared body length before any byte is stored.

    Returns ``"malformed"``, ``"conflict"``, ``"too_large"``, or None when the
    body may be read. A missing length (chunked upload) is allowed; the reader
    still stops at the cap.
    """
    chunked = any("chunked" in item.lower() for item in encodings)
    if chunked and lengths:
        return "conflict"
    if len(lengths) > 1 or (lengths and not lengths[0].isdigit()):
        return "malformed"
    if lengths and int(lengths[0]) > max_bytes + MULTIPART_FRAMING_BYTES:
        return "too_large"
    return None


def upload_too_large_detail(max_bytes: int) -> str:
    """Error text for a file over the cap. Keeps the historical 'too large' phrase."""
    return (
        "Uploaded file is too large. "
        f"The upload limit is {format_upload_limit(max_bytes)}."
    )
