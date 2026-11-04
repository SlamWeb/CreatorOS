"""Run the existing lightweight analyzers on one canonical audio interval."""
import argparse, hashlib, json, math, os, subprocess, sys, wave
from pathlib import Path

HERE=Path(__file__).resolve().parent
p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--audio',type=Path,required=True)
p.add_argument('--output-dir',type=Path,required=True)
p.add_argument('--start',type=float,default=0,help='Input-file seconds; default 0.')
p.add_argument('--duration',type=float,help='Selected seconds; default whole input up to 300s.')
p.add_argument('--tools-dir',type=Path,default=Path(os.environ.get('BGM_ANALYSIS_ROOT','D:/SelfMedia/管理/工具/bgm-analysis')))
p.add_argument('--onset-script',type=Path,default=HERE/'analyze_onsets.py')
p.add_argument('--ffmpeg',help='Optional FFmpeg executable path.')
args=p.parse_args()
if not math.isfinite(args.start) or args.start<0 or (args.duration is not None and (not math.isfinite(args.duration) or not 0<args.duration<=300)): p.error('start must be finite/nonnegative and duration in (0,300].')
if not args.audio.is_file(): p.error('Audio file does not exist.')
if args.output_dir.exists(): p.error('Use a new output directory; never overwrite previous analysis.')
if not (args.tools_dir/'models/small0.ckpt').is_file(): p.error('Beat This small0 model is missing under --tools-dir; see references/runtime.md.')
if not args.onset_script.is_file(): p.error('Existing onset script not found; pass --onset-script.')
if args.ffmpeg: ffmpeg=args.ffmpeg
else:
 import imageio_ffmpeg
 ffmpeg=imageio_ffmpeg.get_ffmpeg_exe()
out=args.output_dir.resolve();out.mkdir(parents=True)
env=os.environ.copy();env['PYTHONDONTWRITEBYTECODE']='1'
env['PYTHONPATH']=str((args.tools_dir/'packages').resolve())+os.pathsep+env.get('PYTHONPATH','')
for variable in ('TEMP','TMP'):
 if (args.tools_dir/'temp').is_dir(): env[variable]=str((args.tools_dir/'temp').resolve())
wav=out/'canonical.wav'
command=[ffmpeg,'-n','-v','error','-i',str(args.audio.resolve()),'-ss',str(args.start)]
# Read at most 301 seconds, so oversized input fails instead of silently truncating.
command+=['-t',str(args.duration if args.duration is not None else 301),'-map','0:a:0','-vn','-ar','44100','-ac','2','-c:a','pcm_s16le',str(wav)]
subprocess.run(command,check=True,env=env)
with wave.open(str(wav),'rb') as w: actual=w.getnframes()/w.getframerate()
if actual<=0 or actual>300.001: raise ValueError('No audio or selection exceeds 300 seconds; choose --start and --duration.')
if args.duration is not None and actual+0.05<args.duration: raise ValueError('Requested interval exceeds source audio; choose a shorter duration.')
def run(name,command):
 log=out/(name+'.log')
 with log.open('w',encoding='utf-8') as f:
  result=subprocess.run([sys.executable,*map(str,command)],stdout=f,stderr=subprocess.STDOUT,env=env)
 if result.returncode: raise RuntimeError(f'{name} failed; inspect {log}')
run('sonara',[args.tools_dir/'analyze_light.py',wav,'--output',out/'sonara.json'])
run('beat-this',[args.tools_dir/'analyze_beats.py',wav,'--output',out/'beat-this.json'])
run('onsets',[args.onset_script,'--audio',wav,'--output-dir',out/'onsets','--max-analysis-seconds',str(max(120,actual+1))])
run('merge',[HERE/'merge_analysis.py','--sonara',out/'sonara.json','--beats',out/'beat-this.json','--onsets',out/'onsets/events.json','--output',out/'music-analysis.json'])
with args.audio.open('rb') as f: input_hash=hashlib.file_digest(f,'sha256').hexdigest()
receipt={'inputFile':str(args.audio.resolve()),'inputSha256':input_hash,
 'canonicalWav':str(wav),'sourceStartSeconds':args.start,'sourceEndSeconds':round(args.start+actual,6),
 'resultTimeOrigin':'canonical WAV start; add sourceStartSeconds to map to input file',
 'result':str(out/'music-analysis.json'),'status':'unreviewed_candidates'}
(out/'source-receipt.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(receipt,ensure_ascii=False,indent=2))
