"""Optional local codecs; CI does not download another media runtime."""
def require_media(test):
    from creatoros.integrations.media_tools import media_tool
    try:
        media_tool("ffmpeg")
        media_tool("ffprobe")
    except ValueError:
        test.skipTest("Media tools unavailable; set CREATOROS_FFMPEG and CREATOROS_FFPROBE to executable paths, or add both to PATH.")
