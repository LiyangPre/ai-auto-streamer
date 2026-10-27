"""Offline Seed-VC wrapper: preserve melody/duration and use the prepared Astra reference.

Install Seed-VC in a separate environment. This wrapper never installs/trains a model
or changes the stream runtime. Run --status first; use --plan to inspect a conversion.
"""
import argparse,json,os,subprocess,sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path)
    parser.add_argument('--reference',type=Path,default=ROOT/'data/voice-references/astra-voicedesign.wav')
    parser.add_argument('--seed-vc-root',type=Path,default=Path(os.environ.get('SEED_VC_ROOT',str(ROOT/'.tools/seed-vc'))))
    parser.add_argument('--python',type=Path,default=ROOT/'.venv-svc/Scripts/python.exe')
    parser.add_argument('--output',type=Path,default=ROOT/'data/songs-converted')
    parser.add_argument('--steps',type=int,choices=range(4,101),default=30)
    parser.add_argument('--semitones',type=int,choices=range(-12,13),default=0)
    parser.add_argument('--status',action='store_true');parser.add_argument('--plan',action='store_true')
    args=parser.parse_args();entry=args.seed_vc_root/'inference.py'
    status={'engine':'Seed-VC v1 singing conversion','reference_ready':args.reference.is_file(),
            'runner_ready':entry.is_file() and args.python.is_file(),'mode':'offline',
            'preserve_source_pitch':True,'preserve_source_duration':True}
    if args.status:print(json.dumps(status));return
    if not args.source or not args.source.is_file():parser.error('Supply a local source recording with --source.')
    if not args.reference.is_file():parser.error('Astra voice reference is missing.')
    command=[str(args.python),str(entry),'--source',str(args.source.resolve()),'--target',str(args.reference.resolve()),
             '--output',str(args.output.resolve()),'--diffusion-steps',str(args.steps),'--length-adjust','1.0',
             '--inference-cfg-rate','0.7','--f0-condition','True','--auto-f0-adjust','False',
             '--semi-tone-shift',str(args.semitones),'--fp16','False']
    if args.plan:print(json.dumps({**status,'command':command},ensure_ascii=False,indent=2));return
    if not status['runner_ready']:parser.error('Install Seed-VC in .tools/seed-vc and its isolated .venv-svc environment first; see START_HERE.md.')
    args.output.mkdir(parents=True,exist_ok=True)
    with (args.output/'conversion.log').open('w',encoding='utf-8') as log:
        result=subprocess.run(command,cwd=args.seed_vc_root,stdout=log,stderr=subprocess.STDOUT,shell=False)
    print(json.dumps({'ok':result.returncode==0,'output_directory':str(args.output.resolve()),'log':'conversion.log'}))
    raise SystemExit(result.returncode)


if __name__=='__main__':main()
