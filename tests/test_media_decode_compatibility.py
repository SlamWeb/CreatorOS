"""Exercise real ffmpeg decode across supported audio/video fixtures."""
import subprocess
import tempfile
import unittest
import wave
from pathlib import Path

from PIL import Image

from creatoros.integrations.media_tools import decode_media, media_tool, probe_media


class MediaDecodeCompatibilityTests(unittest.TestCase):
    def test_video_audio_and_corrupted_input(self):
        try:
            ffmpeg = media_tool('ffmpeg')
            media_tool('ffprobe')
        except ValueError:
            self.skipTest('ffmpeg and ffprobe required')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            frame, audio, video, bad = (root / name for name in ('frame.png', 'tone.wav', 'video.mp4', 'bad.mp4'))
            Image.new('RGB', (32, 32), 'black').save(frame)
            with wave.open(str(audio), 'wb') as stream:
                stream.setnchannels(1)
                stream.setsampwidth(2)
                stream.setframerate(8000)
                stream.writeframes(bytes(16000))
            made = subprocess.run([ffmpeg, '-v', 'error', '-y', '-loop', '1', '-framerate', '2',
                '-i', str(frame), '-i', str(audio), '-t', '1', '-c:v', 'libx264', '-pix_fmt', 'yuv420p',
                '-c:a', 'aac', '-shortest', str(video)], capture_output=True, timeout=30)
            self.assertEqual(made.returncode, 0, made.stderr.decode(errors='replace'))
            self.assertTrue(probe_media(video)['has_video'])
            self.assertTrue(probe_media(video)['has_audio'])
            decode_media(video)
            decode_media(audio)
            bad.write_bytes(video.read_bytes()[:64])
            with self.assertRaises(ValueError):
                decode_media(bad)


if __name__ == '__main__':
    unittest.main()
