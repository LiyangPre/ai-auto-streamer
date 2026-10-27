"""One30step/4thread short quality-preserving benchmark worker."""
from pathlib import Path
from types import SimpleNamespace
import argparse,ctypes,hashlib,json,os,sys,time

parser=argparse.ArgumentParser();parser.add_argument('--worker-id',required=True);args_cli=parser.parse_args()
if args_cli.worker_id not in ('A','B'):raise SystemExit('Only short benchmark workers A/B are supported')
BASE=Path(__file__).resolve().parent
PROJECT=BASE.parents[2];ENGINE=PROJECT/'.tools/seed-vc';OUT=BASE/('parallel-'+args_cli.worker_id)
OUT.mkdir(parents=True,exist_ok=True)
os.environ.update(OMP_NUM_THREADS='4',MKL_NUM_THREADS='4',OPENBLAS_NUM_THREADS='4',NUMBA_NUM_THREADS='4',
    HF_HUB_OFFLINE='1',HF_HUB_DISABLE_IMPLICIT_TOKEN='1',LIBROSA_CACHE_DIR=str(OUT/'.librosa-cache'),
    MPLCONFIGDIR=str(OUT/'.matplotlib'))
sys.path.insert(0,str(ENGINE))
import numpy as np
import torch,torchaudio,soundfile as sf
torch.set_num_threads(4);torch.set_num_interop_threads(1)
import inference as seed

full=PROJECT/'data/voice-references/astra-current-voice.wav'
approval=json.loads(full.with_suffix('.json').read_text(encoding='utf-8'))
digest=hashlib.sha256(full.read_bytes()).hexdigest()
if approval.get('approved') is not True or approval.get('reference_sha256')!=digest:
    raise SystemExit('Current reference approval/hash mismatch')
reference=ENGINE/'astra-current-reference-8s.wav';source=ENGINE/'astra-benchmark-real-source.wav'
approved,approved_rate=sf.read(full,dtype='float32',always_2d=True)
excerpt,excerpt_rate=sf.read(reference,dtype='float32',always_2d=True)
if approved_rate!=excerpt_rate or not np.array_equal(approved[:approved_rate*8],excerpt):
    raise SystemExit('Reference excerpt mismatch')
source_info=sf.info(source)
if source_info.duration>4.01:raise SystemExit('Short benchmark refuses longer source')
report={'status':'starting','worker_id':args_cli.worker_id,'pid':os.getpid(),'steps':30,'threads':4,
    'source_seconds':source_info.duration,'source_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),
    'full_reference_sha256':digest,'reference_seconds':8,'torch':str(torch.__version__),
    'fp16':False,'f0_condition':True,'auto_f0_adjust':False,'length_adjust':1.0,'semitone_shift':0}
proof=OUT/'result.json'
def save():proof.write_text(json.dumps(report,indent=2,allow_nan=False)+'\n',encoding='utf-8')
def memory():
    from ctypes import wintypes
    class Counters(ctypes.Structure):
        _fields_=[('cb',wintypes.DWORD),('PageFaultCount',wintypes.DWORD)]+[(key,ctypes.c_size_t) for key in
            ('PeakWorkingSetSize','WorkingSetSize','QuotaPeakPagedPoolUsage','QuotaPagedPoolUsage',
             'QuotaPeakNonPagedPoolUsage','QuotaNonPagedPoolUsage','PagefileUsage','PeakPagefileUsage')]
    value=Counters();value.cb=ctypes.sizeof(value)
    ctypes.windll.kernel32.GetCurrentProcess.restype=wintypes.HANDLE
    ctypes.windll.psapi.GetProcessMemoryInfo.argtypes=[wintypes.HANDLE,ctypes.POINTER(Counters),wintypes.DWORD]
    ctypes.windll.psapi.GetProcessMemoryInfo.restype=wintypes.BOOL
    if not ctypes.windll.psapi.GetProcessMemoryInfo(ctypes.windll.kernel32.GetCurrentProcess(),ctypes.byref(value),value.cb):
        return None
    return {'peak_working_set_gib':value.PeakWorkingSetSize/1024**3,'working_set_gib':value.WorkingSetSize/1024**3,
            'peak_private_commit_gib':value.PeakPagefileUsage/1024**3}
runargs=SimpleNamespace(source=str(source),target=str(reference),output=str(OUT/'output'),
    diffusion_steps=30,length_adjust=1.0,inference_cfg_rate=.7,f0_condition=True,auto_f0_adjust=False,
    semi_tone_shift=0,checkpoint=None,config=None,fp16=False)
os.chdir(ENGINE);save()
try:
    started=time.perf_counter();models=list(seed.load_models(runargs))
    report['model_load_seconds']=time.perf_counter()-started
    f0_calls=[];original_f0=models[2]
    def remember_f0(*args,**kwargs):
        result=original_f0(*args,**kwargs);f0_calls.append(result.copy());return result
    models[2]=remember_f0;seed.load_models=lambda unused:models
    torch.manual_seed(0)
    report.update(status='converting',conversion_started_monotonic=time.perf_counter());save()
    seed.main(runargs)
    report['conversion_ended_monotonic']=time.perf_counter()
    report['conversion_seconds']=report['conversion_ended_monotonic']-report['conversion_started_monotonic']
    output=Path(runargs.output)/'vc_astra-benchmark-real-source_astra-current-reference-8s_1.0_30_0.7.wav'
    wave,rate=sf.read(output,dtype='float32',always_2d=True)
    if rate!=44100 or wave.shape[1]!=1 or not np.isfinite(wave).all():raise ValueError('Nonfinite/invalid output')
    peak=float(np.abs(wave).max());rms=float(np.sqrt(np.mean(wave.astype('float64')**2)))
    if peak>1.5 or rms>.6:raise ValueError('Implausible output amplitude')
    output_16k=torchaudio.functional.resample(torch.from_numpy(wave[:,0]).unsqueeze(0),rate,16000)[0]
    converted_f0=original_f0(output_16k,thred=.03);source_f0=f0_calls[1]
    n=min(len(source_f0),len(converted_f0));source_f0=source_f0[:n];converted_f0=converted_f0[:n]
    voiced=(source_f0>50)&(converted_f0>50)
    error=np.abs(12*np.log2(converted_f0[voiced]/source_f0[voiced]))
    baseline=ENGINE/'astra-benchmark/vc_astra-benchmark-real-source_astra-current-reference-8s_1.0_30_0.7.wav'
    prior,prior_rate=sf.read(baseline,dtype='float32',always_2d=True)
    report['output']={'path':str(output),'finite':True,'samples':len(wave),'sample_rate':rate,'peak':peak,'rms':rms,
        'baseline_shape_equal':wave.shape==prior.shape and rate==prior_rate,
        'baseline_max_absolute_sample_difference':float(np.max(np.abs(wave-prior))) if wave.shape==prior.shape else None}
    report['pitch']={'voiced_intersection_frames':int(voiced.sum()),'median_absolute_semitones':float(np.median(error)),
        'p90_absolute_semitones':float(np.percentile(error,90)),'within_one_semitone_fraction':float(np.mean(error<=1))}
    report['memory']=memory();report['rtf']=report['conversion_seconds']/source_info.duration
    report['status']='complete'
except BaseException as error:
    report.update(status='failed',error_type=type(error).__name__,error=str(error)[:500]);raise
finally:save()
