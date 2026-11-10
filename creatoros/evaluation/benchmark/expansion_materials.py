"""Disposable materials fixtures; never writes outside the benchmark world."""
from pathlib import Path
import subprocess


def seed(world, case):
    case_id = case.get('id')
    if case_id == 'P01' and case.get('scenario') == 'materials_type':
        from creatoros.integrations.media_tools import media_tool
        from PIL import Image
        path = world.input_dir / 'bench-video.mp4'
        # Some ffmpeg builds omit lavfi's color source; generate the same
        # black input frame locally without requiring the optional filter.
        frame = world.input_dir / 'bench-video-frame.png'
        Image.new('RGB', (32, 32), 'black').save(frame)
        result = subprocess.run([media_tool('ffmpeg'), '-v','error','-y','-loop','1','-framerate','2',
            '-i',str(frame),'-i',str(world.input_dir/'tone.wav'),'-t','2',
            '-c:v','libx264','-pix_fmt','yuv420p','-c:a','aac','-shortest',str(path)],
            stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=30,check=False)
        if result.returncode or not path.is_file():
            raise RuntimeError('Could not generate isolated audiovisual fixture')
        row=world.app.state.materials.put('bench-a','bench-video.mp4',path.read_bytes())
        if row['kind'] != 'video':
            raise ValueError('Audiovisual fixture classified incorrectly')
        world.variables['material_video']=row['id']
    elif case_id == 'P02' and case.get('scenario') == 'materials_mixed_scope':
        row=world.app.state.materials.put('bench-b',world.canary+'.wav', (world.input_dir/'tone.wav').read_bytes())
        world.variables['material_b']=row['id']
