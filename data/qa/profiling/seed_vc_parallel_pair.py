"""Launch exactly two short4thread benchmarks and report measured throughput."""
from pathlib import Path
import ctypes,json,subprocess,sys,time
from ctypes import wintypes

BASE=Path(__file__).resolve().parent
class Counters(ctypes.Structure):
    _fields_=[('cb',wintypes.DWORD),('PageFaultCount',wintypes.DWORD)]+[(key,ctypes.c_size_t) for key in
        ('PeakWorkingSetSize','WorkingSetSize','QuotaPeakPagedPoolUsage','QuotaPagedPoolUsage',
         'QuotaPeakNonPagedPoolUsage','QuotaNonPagedPoolUsage','PagefileUsage','PeakPagefileUsage')]
ctypes.windll.kernel32.OpenProcess.restype=wintypes.HANDLE
ctypes.windll.kernel32.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]
ctypes.windll.kernel32.CloseHandle.argtypes=[wintypes.HANDLE]
ctypes.windll.psapi.GetProcessMemoryInfo.argtypes=[wintypes.HANDLE,ctypes.POINTER(Counters),wintypes.DWORD]
ctypes.windll.psapi.GetProcessMemoryInfo.restype=wintypes.BOOL
def working_set(pid):
    handle=ctypes.windll.kernel32.OpenProcess(0x410,False,pid)
    if not handle:return None
    try:
        value=Counters();value.cb=ctypes.sizeof(value)
        if ctypes.windll.psapi.GetProcessMemoryInfo(handle,ctypes.byref(value),value.cb):return value.WorkingSetSize
    finally:ctypes.windll.kernel32.CloseHandle(handle)

started=time.perf_counter();workers=[];logs=[];peak_sum=0
try:
    for worker_id in ('A','B'):
        log=(BASE/f'parallel-{worker_id}.log').open('w',encoding='utf-8');logs.append(log)
        process=subprocess.Popen([sys.executable,'-X','utf8',str(BASE/'seed_vc_parallel_worker.py'),
            '--worker-id',worker_id],stdout=log,stderr=subprocess.STDOUT,creationflags=subprocess.CREATE_NO_WINDOW)
        workers.append((worker_id,process))
    while any(process.poll() is None for _,process in workers):
        values=[working_set(process.pid) for _,process in workers if process.poll() is None]
        peak_sum=max(peak_sum,sum(value for value in values if value is not None));time.sleep(.5)
finally:
    for log in logs:log.close()
wall=time.perf_counter()-started
results=[json.loads((BASE/f'parallel-{worker_id}/result.json').read_text(encoding='utf-8')) for worker_id,_ in workers]
valid=all(value.get('status')=='complete' and value['output']['finite'] and
    value['pitch']['within_one_semitone_fraction']>=.95 for value in results)
conversion_wall=max(value['conversion_ended_monotonic'] for value in results)-min(value['conversion_started_monotonic'] for value in results)
solo=json.loads((BASE/'seed-vc-stage-profile.json').read_text(encoding='utf-8'))
solo_seconds=solo['total_conversion_seconds']
report={'status':'complete' if valid else 'failed_quality_guard','workers':results,'pair_wall_seconds_including_startup':wall,
    'pair_overlapped_conversion_wall_seconds':conversion_wall,'total_source_seconds':8,
    'peak_combined_sampled_working_set_gib':peak_sum/1024**3 if peak_sum else None,
    'comparison':{'prior_solo6thread_seconds':89.594,'current_solo6thread_seconds':solo_seconds,
        'speedup_vs_prior_solo_sequential':2*89.594/conversion_wall,
        'speedup_vs_current_solo_sequential':2*solo_seconds/conversion_wall,
        'pair_effective_rtf':conversion_wall/8},
    'limitation':'One4s excerpt perworker; fulltrack CPU/RAM/streamer responsiveness can differ; qualitysettings unchanged'}
(BASE/'seed-vc-parallel-pair.json').write_text(json.dumps(report,indent=2,allow_nan=False)+'\n',encoding='utf-8')
print(json.dumps(report),flush=True)
