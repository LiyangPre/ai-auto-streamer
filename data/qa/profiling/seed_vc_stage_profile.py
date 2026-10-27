"""One short, unchanged-quality SVC stage profile; never imports the live app."""
from pathlib import Path
import hashlib,json,os,sys,time
from types import SimpleNamespace

OUT=Path(__file__).resolve().parent
PROJECT=OUT.parents[2]
ENGINE=PROJECT/'.tools/seed-vc'
os.environ.update(OMP_NUM_THREADS='6',MKL_NUM_THREADS='6',OPENBLAS_NUM_THREADS='6',NUMBA_NUM_THREADS='6',
                  HF_HUB_OFFLINE='1',HF_HUB_DISABLE_IMPLICIT_TOKEN='1',
                  LIBROSA_CACHE_DIR=str(OUT/'.librosa-cache'),MPLCONFIGDIR=str(OUT/'.matplotlib'))
sys.path.insert(0,str(ENGINE))
import numpy as np
import torch
import soundfile as sf
torch.set_num_threads(6);torch.set_num_interop_threads(2)
import inference as seed

full_reference=PROJECT/'data/voice-references/astra-current-voice.wav'
approval=json.loads(full_reference.with_suffix('.json').read_text(encoding='utf-8'))
reference_hash=hashlib.sha256(full_reference.read_bytes()).hexdigest()
if approval.get('approved') is not True or approval.get('reference_sha256')!=reference_hash:
    raise SystemExit('Current reference approval/hash mismatch')
reference=ENGINE/'astra-current-reference-8s.wav'
original,rate=sf.read(full_reference,dtype='float32',always_2d=True)
excerpt,excerpt_rate=sf.read(reference,dtype='float32',always_2d=True)
if rate!=excerpt_rate or not np.array_equal(original[:rate*8],excerpt):
    raise SystemExit('Existing8s reference differs from approved voice')
source=ENGINE/'astra-benchmark-real-source.wav'
source_info=sf.info(source)
if source_info.duration>4.01:raise SystemExit('Profile refuses longer than4s source')

report={'status':'starting','source':str(source),'source_seconds':source_info.duration,
    'reference':str(reference),'full_reference_sha256':reference_hash,'reference_seconds':len(excerpt)/rate,
    'steps':30,'threads':6,'torch':str(torch.__version__),'fp16':False,'f0_condition':True,
    'auto_f0_adjust':False,'length_adjust':1.0,'semitone_shift':0,'stages':{},'calls':[]}
proof=OUT/'seed-vc-stage-profile.json'
def save():proof.write_text(json.dumps(report,indent=2,allow_nan=False)+'\n',encoding='utf-8')
def timed(fn,label):
    def wrapped(*args,**kwargs):
        started=time.perf_counter()
        result=fn(*args,**kwargs)
        seconds=time.perf_counter()-started
        report['stages'][label]=report['stages'].get(label,0)+seconds
        report['calls'].append({'stage':label,'seconds':seconds})
        print(json.dumps({'stage':label,'seconds':seconds}),flush=True)
        return result
    return wrapped
def sequence(fn,labels):
    count=0
    def wrapped(*args,**kwargs):
        nonlocal count
        label=labels[min(count,len(labels)-1)];count+=1
        return timed(fn,label)(*args,**kwargs)
    return wrapped

args=SimpleNamespace(source=str(source),target=str(reference),output=str(OUT/'output'),
    diffusion_steps=30,length_adjust=1.0,inference_cfg_rate=.7,f0_condition=True,
    auto_f0_adjust=False,semi_tone_shift=0,checkpoint=None,config=None,fp16=False)
os.chdir(ENGINE)
save()
started=time.perf_counter()
models=list(seed.load_models(args))
report['model_load_seconds']=time.perf_counter()-started
models[1]=sequence(models[1],['whisper_source','whisper_reference'])
models[2]=sequence(models[2],['rmvpe_reference','rmvpe_source'])
models[3]=timed(models[3],'bigvgan_vocoder')
models[4]=timed(models[4],'campplus_reference')
models[5]=sequence(models[5],['mel_source','mel_reference'])
models[0].cfm.inference=timed(models[0].cfm.inference,'cfm_30steps')
models[0].length_regulator.forward=sequence(models[0].length_regulator.forward,
    ['length_regulator_source','length_regulator_reference'])
seed.load_models=lambda unused:models
torch.manual_seed(0)
report['status']='profiling';save()
started=time.perf_counter()
try:
    seed.main(args)
    report['total_conversion_seconds']=time.perf_counter()-started
    output=Path(args.output)/'vc_astra-benchmark-real-source_astra-current-reference-8s_1.0_30_0.7.wav'
    audio,audio_rate=sf.read(output,dtype='float32',always_2d=True)
    baseline=ENGINE/'astra-benchmark/vc_astra-benchmark-real-source_astra-current-reference-8s_1.0_30_0.7.wav'
    prior,prior_rate=sf.read(baseline,dtype='float32',always_2d=True)
    report['output']={'path':str(output),'sample_rate':audio_rate,'samples':len(audio),
        'finite':bool(np.isfinite(audio).all()),'peak':float(np.abs(audio).max()),
        'rms':float(np.sqrt(np.mean(audio.astype('float64')**2))),
        'baseline_shape_equal':audio.shape==prior.shape and audio_rate==prior_rate,
        'baseline_max_absolute_sample_difference':float(np.max(np.abs(audio-prior))) if audio.shape==prior.shape else None}
    report['unattributed_seconds']=report['total_conversion_seconds']-sum(report['stages'].values())
    report['rtf']=report['total_conversion_seconds']/source_info.duration
    report['stage_percent']={label:seconds/report['total_conversion_seconds']*100
        for label,seconds in report['stages'].items()}
    report['status']='complete'
except BaseException as error:
    report.update(status='failed',error_type=type(error).__name__,error=str(error)[:500]);raise
finally:save()
print(json.dumps(report),flush=True)
