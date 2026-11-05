"""B47 media evidence tests against local, controlled, decoded MP4 bytes."""
from copy import deepcopy
import hashlib
import json
import math
import os
from pathlib import Path
import struct
import subprocess
from tempfile import TemporaryDirectory
import unittest
import wave
import zipfile

from PIL import Image
from creatoros.evaluation.benchmark.video_evidence import collect_video_evidence

FFMPEG = Path("D:/Anaconda4.7g/envs/llmpc/Lib/site-packages/imageio_ffmpeg/binaries/ffmpeg-win-x86_64-v7.1.exe")


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def wave_file(path, hz):
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1); out.setsampwidth(2); out.setframerate(8000)
        out.writeframes(b"".join(struct.pack("<h", int(12000 * math.sin(2 * math.pi * hz * n / 8000))) for n in range(16000)))


@unittest.skipUnless(FFMPEG.is_file(), "controlled ffmpeg fixture unavailable")
class VideoEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(prefix="video-evidence-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        root = self.root
        (root/"inputs").mkdir(); (root/"materials").mkdir(); (root/"output").mkdir()
        Image.new("RGB",(64,64),"blue").save(root/"inputs/first.png")
        Image.new("RGB",(64,64),"red").save(root/"inputs/second.png")
        wave_file(root/"inputs/tone.wav",440)
        (root/"materials/audio.wav").write_bytes((root/"inputs/tone.wav").read_bytes())
        self.items = [{"run_id":"R0","revision_id":"REV0","order":i,"checksum":sha(root/f"inputs/{name}.png"),"filename":f"{i:04}.png"}
                      for i,name in enumerate(("first","second"),1)]
        with zipfile.ZipFile(root/"materials/images.zip", "w") as archive:
            for item,name in zip(self.items,("first","second")):
                archive.write(root/f"inputs/{name}.png",item["filename"])
            archive.writestr("manifest.json",json.dumps({"collection_id":"COLL","items":self.items}))
        self.make_video(("first","second"),440)
        self.build_evidence()

    def make_video(self, order, frequency):
        root=self.root
        wave_file(root/"output/audio_input.wav", frequency)
        cmd=[str(FFMPEG),"-v","error","-y","-loop","1","-framerate","8","-t","1","-i",str(root/f"inputs/{order[0]}.png"),
             "-loop","1","-framerate","8","-t","1","-i",str(root/f"inputs/{order[1]}.png"),
             "-i",str(root/"output/audio_input.wav"),"-filter_complex","[0:v][1:v]concat=n=2:v=1:a=0[v]",
             "-map","[v]","-map","2:a:0","-c:v","libx264","-pix_fmt","yuv420p","-c:a","aac","-t","2",str(root/"output/video.mp4")]
        done=subprocess.run(cmd,capture_output=True,timeout=30)
        if done.returncode:
            raise RuntimeError(done.stderr.decode(errors="replace")[:500])

    def build_evidence(self):
        root=self.root
        files={p.relative_to(root).as_posix():sha(p) for p in root.rglob("*") if p.is_file()}
        spec={"kind":"video","skills":[{"skill_id":"VID"}], "materials":[
            {"id":"BGM","kind":"audio","path":str(root/"materials/audio.wav"),"sha256":sha(root/"materials/audio.wav")},
            {"id":"ZIP","kind":"images_zip","path":str(root/"materials/images.zip"),"sha256":sha(root/"materials/images.zip")}]}
        frozen={"creator_id":"A","topic_id":"T","task_id":"TASK","execution_spec":spec}
        run={"id":"RUN","topic_id":"T","task_id":"TASK","active_revision_number":1}
        revision={"id":"REV","content_run_id":"RUN","revision_number":1,"production_input_json":json.dumps(frozen),
                  "artifact_directory":str(root/"output"),"validation_json":json.dumps({"video":{"path":"video.mp4","sha256":sha(root/"output/video.mp4")}})}
        source_run={"id":"R0","topic_id":"source-topic","active_revision_number":1}
        source_revision={"id":"REV0","content_run_id":"R0","revision_number":1,
            "production_input_json":json.dumps({"creator_id":"A"}), "artifact_directory":str(root/"inputs"),
            "validation_json":json.dumps({"images":[{"order":i,"path":name+".png","sha256":self.items[i-1]["checksum"]}
                                                    for i,name in enumerate(("first","second"),1)]})}
        collection={"id":"COLL","creator_id":"A","run_ids_json":json.dumps(["R0"])}
        self.evidence={"variables":{"creator_a":"A","topic_a":"T","collection_a":"COLL","video_skill":"VID","bgm_material":"BGM"},
          "before":{"database":{"content_runs":[source_run],"content_revisions":[source_revision],"media_collections":[collection]},"files":files},
          "after":{"database":{"content_runs":[source_run,run],"content_revisions":[source_revision,revision]},"files":files},
          "background":{"initial_collections":{"COLL":{"items":self.items}}}}

    def test_correct_video_supports_order_and_audio_but_not_absolute_provenance(self):
        result=collect_video_evidence(self.root,self.evidence,ffmpeg=str(FFMPEG))
        self.assertNotIn("error",result)
        self.assertEqual(result["input_binding"]["status"],"passed")
        self.assertEqual(result["frames"]["assessment"],"sampled_consistent")
        self.assertEqual(result["audio"]["assessment"],"waveform_consistent")
        self.assertEqual(result["status"],"needs_review")

    def test_reversed_frames_and_different_audio_do_not_masquerade_as_matches(self):
        self.make_video(("second","first"),660); self.build_evidence()
        result=collect_video_evidence(self.root,self.evidence,ffmpeg=str(FFMPEG))
        self.assertEqual(result["frames"]["assessment"],"inconclusive")
        self.assertEqual(result["audio"]["assessment"],"inconclusive")
        self.assertEqual(result["status"],"needs_review")

    def test_tampered_file_missing_identity_and_missing_tool_are_unknown(self):
        data=deepcopy(self.evidence); data["variables"]["video_skill"]="other"
        self.assertEqual(collect_video_evidence(self.root,data,ffmpeg=str(FFMPEG))["input_binding"]["status"],"needs_review")
        data=deepcopy(self.evidence); data["background"]["initial_collections"]["COLL"]["items"].reverse()
        self.assertEqual(collect_video_evidence(self.root,data,ffmpeg=str(FFMPEG))["input_binding"]["status"],"needs_review")
        data=deepcopy(self.evidence); data["before"]["database"]["media_collections"][0]["creator_id"]="B"
        self.assertEqual(collect_video_evidence(self.root,data,ffmpeg=str(FFMPEG))["input_binding"]["status"],"needs_review")
        data=deepcopy(self.evidence); data["before"]["database"]["content_revisions"][0]["production_input_json"] = json.dumps({"creator_id":"B"})
        self.assertEqual(collect_video_evidence(self.root,data,ffmpeg=str(FFMPEG))["input_binding"]["status"],"needs_review")
        data=deepcopy(self.evidence); data["before"]["database"]["content_runs"][0]["active_revision_number"] = 2
        self.assertEqual(collect_video_evidence(self.root,data,ffmpeg=str(FFMPEG))["input_binding"]["status"],"needs_review")
        data=deepcopy(self.evidence)
        self.assertEqual(collect_video_evidence(self.root,data,ffmpeg=str(self.root/"no-ffmpeg"))["status"],"needs_review")
        (self.root/"output/video.mp4").write_bytes(b"modified")
        self.assertEqual(collect_video_evidence(self.root,self.evidence,ffmpeg=str(FFMPEG))["input_binding"]["status"],"needs_review")

    def test_wrong_zip_member_cannot_pass_by_rewriting_only_archive_hash(self):
        path = self.root/"materials/images.zip"
        with zipfile.ZipFile(path, "w") as archive:
            archive.write(self.root/"inputs/second.png", "0001.png")
            archive.write(self.root/"inputs/first.png", "0002.png")
            archive.writestr("manifest.json", json.dumps({"collection_id":"COLL", "items":self.items}))
        data=deepcopy(self.evidence)
        rev=data["after"]["database"]["content_revisions"][1]
        frozen=json.loads(rev["production_input_json"])
        frozen["execution_spec"]["materials"][1]["sha256"]=sha(path)
        rev["production_input_json"]=json.dumps(frozen)
        data["after"]["files"]["materials/images.zip"]=sha(path)
        result=collect_video_evidence(self.root,data,ffmpeg=str(FFMPEG))
        self.assertEqual(result["input_binding"]["status"],"needs_review")

if __name__=="__main__":
    unittest.main()
