import hashlib
import tempfile
import unittest
from pathlib import Path

from creatoros.integrations.bgm_analysis import validate_analysis


class BgmAnalysisTests(unittest.TestCase):
    def test_analysis_matches_original_audio_and_preserves_events(self):
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "music.wav"
            audio.write_bytes(b"original audio")
            receipt = {"inputSha256": hashlib.sha256(audio.read_bytes()).hexdigest(),
                       "sourceStartSeconds": 12.0, "sourceEndSeconds": 17.0}
            events = {"analysisOffset": 0, "analysisDurationSeconds": 5,
                      "events": [{"id": str(i), "timeSeconds": i / 10,
                                  "accepted": i % 2 == 0} for i in range(40)]}
            validate_analysis(audio, receipt, events, audio_duration=25)
            self.assertEqual(len(events["events"]), 40)
            self.assertFalse(events["events"][1]["accepted"])
            self.assertEqual(events["events"][0]["timeSeconds"], 0)
            audio.write_bytes(b"another audio")
            with self.assertRaisesRegex(ValueError, "不匹配"):
                validate_analysis(audio, receipt, events, audio_duration=25)


if __name__ == "__main__":
    unittest.main()
