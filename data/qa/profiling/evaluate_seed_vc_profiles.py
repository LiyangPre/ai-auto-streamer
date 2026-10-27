"""Preserve raw telemetry error and evaluate successfully generated/QC'd audio."""
from pathlib import Path
import json
BASE=Path(__file__).resolve().parent
solo=json.loads((BASE/'seed-vc-stage-profile.json').read_text(encoding='utf-8'))
pair=json.loads((BASE/'seed-vc-parallel-pair.json').read_text(encoding='utf-8'))
memory=json.loads((BASE/'seed-vc-parallel-memory.json').read_text(encoding='utf-8'))
assert solo['status']=='complete'
for worker in pair['workers']:
    assert worker['output']['finite'] and worker['output']['baseline_shape_equal']
    assert worker['pitch']['within_one_semitone_fraction']>=.95
    # Raw worker errors occurred only while querying its peak memory after the
    # audio and pitch checks had completed. Do not relabel raw failed reports.
    assert worker.get('error_type')=='ArgumentError' and 'int too long to convert' in worker.get('error','')
report={'status':'output_quality_passed_memory_telemetry_recovered',
    'raw_results':'seed-vc-parallel-pair.json','raw_worker_status':'failed after audio/pitchQC during memory self-probe',
    'telemetry_issue':'ctypes GetCurrentProcess pseudo-handle overflow without64bit argtypes; scripts now fixed, no benchmark rerun',
    'recovery':'external typed WinAPI sampler read both known benchmark PIDs and lifetime peak counters before exit',
    'reference_sha256':pair['workers'][0]['full_reference_sha256'],'quality_settings_changed':False,
    'solo6threads':{'conversion_seconds':solo['total_conversion_seconds'],'source_seconds':4,
        'rtf':solo['rtf'],'sample_identical_to_approved_preview':solo['output']['baseline_max_absolute_sample_difference']==0,
        'stages':solo['stages'],'stage_percent':solo['stage_percent']},
    'pair2x4threads':{'conversion_wall_seconds':pair['pair_overlapped_conversion_wall_seconds'],
        'total_source_seconds':8,'effective_rtf':pair['comparison']['pair_effective_rtf'],
        'throughput_vs_current_solo':pair['comparison']['speedup_vs_current_solo_sequential'],
        'throughput_vs_older_solo_under_different_load':pair['comparison']['speedup_vs_prior_solo_sequential'],
        'both_outputs_finite':True,'both_within_one_semitone_fraction':pair['workers'][0]['pitch']['within_one_semitone_fraction'],
        'max_sample_difference_from6thread_baseline':max(worker['output']['baseline_max_absolute_sample_difference'] for worker in pair['workers'])},
    'memory':memory,
    'summed_lifetime_peak_working_set_upper_bound_gib':sum(worker['peak_working_set_gib'] for worker in memory['workers'].values()),
    'summed_lifetime_peak_private_commit_upper_bound_gib':sum(worker['peak_private_commit_gib'] for worker in memory['workers'].values()),
    'recommendation':{'workers':1,'threads':6,'main_job_steps':50,
        'reason':'Two4thread model copies were slower than contemporaneous one6thread worker; four model copies untested and longsong memory can exceed4s samples',
        'quality_neutral_reference_cache':'Exact reference feature cache would save roughly1.2seconds pertrack on this sample; diffusion and vocoder dominate94% of CPUtime',
        'not_tested':['4simultaneous workers','50step timing','denormal flush','different reference duration','fullsong RAM']},
    'limitations':['One4s excerpt perworker; wholetrack timings depend on chunk size, backgroundload and source length',
                   'Typed memory sampler started late but perprocess lifetime peak counters include earlier allocations',
                   'No timbre or fullsong quality claim follows from pitch statistics alone']}
(BASE/'seed-vc-parallel-evaluation.json').write_text(json.dumps(report,indent=2,allow_nan=False)+'\n',encoding='utf-8')
print(json.dumps({'status':report['status'],'recommendation':report['recommendation'],'peak_rss_pair_gib':report['summed_lifetime_peak_working_set_upper_bound_gib']}))
