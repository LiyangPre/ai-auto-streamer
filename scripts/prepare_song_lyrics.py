"""Recognize local vocal stems once and save recording-matched lyric defaults.

Run with .venv-lyrics. Models and recordings stay local. Import uses only the
existing loopback Studio API; the song audio, pitch, tempo and bans are untouched.
"""
import argparse
from dataclasses import asdict
import hashlib
import importlib.metadata
import json
from pathlib import Path
import re
import sys
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.atomic_json import write_json_atomic
from src.lyric_alignment import build_lyric_cues, to_lrc
from src.lyrics import parse_lrc

MODEL_ROOT = ROOT / '.tools/lyrics-models/large-v3-turbo'
OUTPUT_ROOT = ROOT / 'data/lyrics'
OPTIONS = dict(language='zh', task='transcribe', beam_size=5, word_timestamps=True,
               condition_on_previous_text=False, vad_filter=True,
               vad_parameters=dict(threshold=.35, min_silence_duration_ms=2000, speech_pad_ms=400),
               hallucination_silence_threshold=2.0)


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


class Studio:
    def __init__(self, port):
        self.root = f'http://127.0.0.1:{port}'
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def call(self, endpoint='', data=None):
        headers = {'X-Studio-API':'1','Origin':self.root,'Content-Type':'application/json'}
        request = urllib.request.Request(self.root+'/api/singing'+endpoint, headers=headers,
            data=json.dumps(data,ensure_ascii=False).encode('utf-8') if data is not None else None)
        with self.opener.open(request, timeout=10) as response:
            return json.load(response)

    def song(self, song_id):
        data = self.call()
        return next((song for song in data['songs'] if song['id']==song_id),None)


def track_source(song):
    bvid = song.get('conversion_provenance',{}).get('bvid','')
    if not re.fullmatch(r'BV[A-Za-z0-9]{10}', bvid):
        return None
    source = ROOT/'data/songs-converted'/bvid/'vocals.wav'
    mix = ROOT/'data/songs'/(song['id']+'.mp3')
    if not source.is_file() or not mix.is_file():
        return None
    expected = song['conversion_provenance'].get('mix_sha256')
    if not expected or digest(mix) != expected:
        raise ValueError('Song audio no longer matches its conversion provenance')
    import av
    with av.open(str(source)) as audio:
        stream = audio.streams.audio[0]
        duration = float(stream.duration*stream.time_base) if stream.duration is not None else audio.duration/av.time_base
    if abs(duration-float(song['duration'])) > .15:
        raise ValueError('Original vocals are not aligned with the saved recording')
    return bvid, source, duration


def recognize(model, source, target, seconds, signature, *, options=None):
    if target.is_file():
        cached = json.loads(target.read_text(encoding='utf-8'))
        if cached.get('signature')==signature and cached.get('complete'):
            return cached
    options = dict(OPTIONS if options is None else options)
    if seconds:
        options['clip_timestamps'] = [0,seconds]
    started = time.monotonic()
    segments, info = model.transcribe(str(source),**options)
    result = {'signature':signature,'complete':False,'language':info.language,'segments':[]}
    for segment in segments:
        value = asdict(segment)
        result['segments'].append(value)
        result['elapsed_seconds'] = round(time.monotonic()-started,2)
        write_json_atomic(target,result)
        print(json.dumps({'stage':'recognizing','end_seconds':round(segment.end,2),
                          'elapsed_seconds':result['elapsed_seconds'],'characters':len(segment.text)}),flush=True)
    result['complete'] = True
    result['elapsed_seconds'] = round(time.monotonic()-started,2)
    write_json_atomic(target,result)
    return result


def main(args):
    from faster_whisper import WhisperModel
    from opencc import OpenCC
    studio = Studio(args.port)
    snapshot = studio.call()
    candidates = [song for song in snapshot['songs'] if not song.get('blocked')
                  and song.get('source')=='upload' and (not args.title or song['title'] in args.title)]
    skipped = [{'title':song['title'],'reason':'blocked'} for song in snapshot['songs'] if song.get('blocked')]
    print(json.dumps({'allowed':[song['title'] for song in candidates],'skipped':skipped},ensure_ascii=False),flush=True)
    prepared = []
    for song in candidates:
        source = track_source(song)
        if source and (not song.get('lyrics_cues') or args.replace_existing or args.sample_seconds):
            prepared.append((song,source))
    if not prepared:
        print(json.dumps({'state':'nothing-to-prepare'}),flush=True)
        return
    if not (MODEL_ROOT/'verified-model.json').is_file():
        raise SystemExit('Download the verified lyric model first: scripts/download_lyric_model.py')
    model_info = json.loads((MODEL_ROOT/'verified-model.json').read_text(encoding='utf-8'))
    model = WhisperModel(str(MODEL_ROOT),device='cpu',compute_type='int8',cpu_threads=args.threads,
                         num_workers=1,local_files_only=True)
    converter = OpenCC('t2s')
    summary = {'profile_id':snapshot['profile_id'],'skipped':skipped,'tracks':[]}
    for song,(bvid,source,duration) in prepared:
        current = studio.song(song['id'])
        if not current or current.get('blocked'):
            continue
        output = OUTPUT_ROOT/bvid
        output.mkdir(parents=True,exist_ok=True)
        stem = f'sample-{args.sample_seconds}' if args.sample_seconds else 'lyrics'
        options = {**OPTIONS, 'language':None if args.language == 'auto' else args.language}
        if args.no_vad:
            options['vad_filter'] = False
        signature_data = {'source_sha256':digest(source),'model_sha256':model_info['model_sha256'],
                          'options':options,'sample_seconds':args.sample_seconds,
                          'runtime':importlib.metadata.version('faster-whisper')}
        signature = hashlib.sha256(json.dumps(signature_data,sort_keys=True).encode()).hexdigest()
        print(json.dumps({'title':song['title'],'stage':'start','duration':duration},ensure_ascii=False),flush=True)
        raw = recognize(model,source,output/(stem+'-asr.json'),args.sample_seconds,signature,options=options)
        for segment in raw['segments']:
            segment['text'] = converter.convert(segment['text'])
            for word in segment.get('words') or []:
                word['word'] = converter.convert(word['word'])
        limit = min(duration,args.sample_seconds) if args.sample_seconds else duration
        aligned = build_lyric_cues(raw['segments'],limit,max_chars=48 if raw['language']=='en' else 18)
        lrc = to_lrc(aligned['cues'],song['title'])
        parsed = parse_lrc(lrc)
        if any(cue['at'] > duration+.02 for cue in parsed['cues']):
            raise ValueError('Lyric timestamp exceeds the saved song duration')
        lrc_path = output/(song['title']+('-sample' if args.sample_seconds else '')+'.lrc')
        lrc_path.write_text(lrc,encoding='utf-8')
        report = {'title':song['title'],'song_id':song['id'],'bvid':bvid,
                  'source':signature_data,'duration':duration,'method':'local-whisper-word-timestamps',
                  'alignment_verified':False,'language':raw['language'],'lrc_sha256':digest(lrc_path),'lrc_path':str(lrc_path),
                  'recognition_seconds':raw['elapsed_seconds'],'cue_count':len(parsed['cues']),
                  'lines':aligned['lines'],'warnings':aligned['warnings'],'imported':False}
        if args.apply and not args.sample_seconds:
            # Recheck the current character, ban and manual edits before committing.
            current_data = studio.call()
            current = next((s for s in current_data['songs'] if s['id']==song['id']),None)
            if current_data['profile_id'] != snapshot['profile_id'] or not current or current.get('blocked'):
                report['warnings'].append('Character or song ban changed; lyric import skipped.')
            elif current.get('lyrics_cues') and not args.replace_existing:
                report['warnings'].append('Existing lyrics retained.')
            else:
                backup=output/'song-metadata-before-lyrics.json'
                if not backup.exists():
                    write_json_atomic(backup,current)
                studio.call('/lyrics',{'song_id':song['id'],'lrc_text':lrc})
                saved = studio.song(song['id'])
                if saved.get('lyrics_cues') != parsed['cues']:
                    raise RuntimeError('Saved lyric data did not match the prepared LRC')
                report['imported'] = True
        write_json_atomic(output/(stem+'-report.json'),report)
        summary['tracks'].append({key:report[key] for key in ('title','song_id','cue_count','recognition_seconds','lrc_path','imported')})
        print(json.dumps({'stage':'ready',**summary['tracks'][-1]},ensure_ascii=False),flush=True)
        write_json_atomic(OUTPUT_ROOT/'preparation.json',summary)


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port',type=int,default=8000,choices=range(1024,65536))
    parser.add_argument('--threads',type=int,default=6,choices=range(1,13))
    parser.add_argument('--language',choices=('zh','en','auto'),default='zh',help='Recognition language; use en for English recordings')
    parser.add_argument('--no-vad',action='store_true',help='Keep sustained singing that speech detection may otherwise skip')
    parser.add_argument('--title',action='append',help='Restrict to an allowed saved song title')
    parser.add_argument('--sample-seconds',type=float,default=0)
    parser.add_argument('--apply',action='store_true',help='Save prepared lyrics to the local song library')
    parser.add_argument('--replace-existing',action='store_true',help='Replace an existing saved LRC intentionally')
    args=parser.parse_args()
    if not 0 <= args.sample_seconds <= 600:
        parser.error('sample-seconds must be between zero and 600')
    main(args)
