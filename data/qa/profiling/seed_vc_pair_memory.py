"""Read memory counters only for the two benchmark child PIDs, no injection."""
from pathlib import Path
import ctypes,json,time
from ctypes import wintypes
BASE=Path(__file__).resolve().parent
class Counters(ctypes.Structure):
    _fields_=[('cb',wintypes.DWORD),('PageFaultCount',wintypes.DWORD)]+[(key,ctypes.c_size_t) for key in
        ('PeakWorkingSetSize','WorkingSetSize','QuotaPeakPagedPoolUsage','QuotaPagedPoolUsage',
         'QuotaPeakNonPagedPoolUsage','QuotaNonPagedPoolUsage','PagefileUsage','PeakPagefileUsage')]
class MemoryStatus(ctypes.Structure):
    _fields_=[('dwLength',wintypes.DWORD),('dwMemoryLoad',wintypes.DWORD)]+[(key,ctypes.c_ulonglong) for key in
        ('ullTotalPhys','ullAvailPhys','ullTotalPageFile','ullAvailPageFile','ullTotalVirtual','ullAvailVirtual','ullAvailExtendedVirtual')]
kernel=ctypes.windll.kernel32;psapi=ctypes.windll.psapi
kernel.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD];kernel.OpenProcess.restype=wintypes.HANDLE
kernel.CloseHandle.argtypes=[wintypes.HANDLE];kernel.CloseHandle.restype=wintypes.BOOL
kernel.GetExitCodeProcess.argtypes=[wintypes.HANDLE,ctypes.POINTER(wintypes.DWORD)]
psapi.GetProcessMemoryInfo.argtypes=[wintypes.HANDLE,ctypes.POINTER(Counters),wintypes.DWORD]
psapi.GetProcessMemoryInfo.restype=wintypes.BOOL
kernel.GlobalMemoryStatusEx.argtypes=[ctypes.POINTER(MemoryStatus)]
processes={}
for worker in ('A','B'):
    info=json.loads((BASE/f'parallel-{worker}/result.json').read_text(encoding='utf-8'))
    handle=kernel.OpenProcess(0x410,False,info['pid'])
    if not handle:raise SystemExit('Benchmark child process not readable')
    processes[worker]=(info['pid'],handle)
report={'status':'sampling','workers':{},'peak_combined_working_set_gib':0,'peak_combined_private_commit_gib':0,
        'minimum_system_available_gib':None,'samples':0}
proof=BASE/'seed-vc-parallel-memory.json';started=time.perf_counter()
try:
    while time.perf_counter()-started<180:
        alive=False;working=0;private=0
        for worker,(pid,handle) in processes.items():
            exitcode=wintypes.DWORD();kernel.GetExitCodeProcess(handle,ctypes.byref(exitcode))
            alive|=exitcode.value==259
            value=Counters();value.cb=ctypes.sizeof(value)
            if psapi.GetProcessMemoryInfo(handle,ctypes.byref(value),value.cb):
                prior=report['workers'].get(worker,{})
                report['workers'][worker]={'pid':pid,'peak_working_set_gib':value.PeakWorkingSetSize/1024**3,
                    'working_set_gib':value.WorkingSetSize/1024**3,'private_commit_gib':value.PagefileUsage/1024**3,
                    'peak_private_commit_gib':value.PeakPagefileUsage/1024**3}
                working+=value.WorkingSetSize;private+=value.PagefileUsage
        status=MemoryStatus();status.dwLength=ctypes.sizeof(status);kernel.GlobalMemoryStatusEx(ctypes.byref(status))
        available=status.ullAvailPhys/1024**3
        report['minimum_system_available_gib']=min(report['minimum_system_available_gib'] or available,available)
        report['peak_combined_working_set_gib']=max(report['peak_combined_working_set_gib'],working/1024**3)
        report['peak_combined_private_commit_gib']=max(report['peak_combined_private_commit_gib'],private/1024**3)
        report['samples']+=1;proof.write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
        if not alive:break
        time.sleep(.5)
    report['status']='complete';report['sampling_seconds']=time.perf_counter()-started
finally:
    for pid,handle in processes.values():kernel.CloseHandle(handle)
    proof.write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
