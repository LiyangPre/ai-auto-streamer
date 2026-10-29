"""Match supplied reference LRCs to local vocals, then save lyric defaults.

This reads reference files and transcription caches from data/lyrics. It sends
only the resulting LRC to the local Studio; it performs no remote lookup or ASR.
"""
import argparse
import json
from pathlib import Path
import re
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.atomic_json import write_json_atomic
from scripts.prepare_song_lyrics import Studio, digest, track_source
from src.lyric_alignment import to_lrc
from src.lyric_match import match_reference
from src.lyrics import parse_lrc


def main(args):
    from opencc import OpenCC
    from pypinyin import lazy_pinyin
    convert=OpenCC('t2s')
    def phonetic(text):
        return ''.join(lazy_pinyin(convert.convert(text),errors='default'))
    studio=Studio(args.port)
    initial=studio.call()
    results=[]
    for song in initial['songs']:
        if args.title and song['title'] not in args.title:
            continue
        if song.get('source')!='upload':
            continue
        if song.get('blocked'):
            results.append({'title':song['title'],'state':'skipped-banned'})
            continue
        source=track_source(song)
        if source is None:
            continue
        bvid,vocals,duration=source
        folder=ROOT/'data/lyrics'/bvid
        name=re.sub(r'[<>:"/\\|?*\x00-\x1f]','_',song['title']).strip('. ')[:80] or song['id']
        reference=folder/(name+'-reference.lrc')
        transcript=folder/'lyrics-report.json'
        if not reference.is_file() or not transcript.is_file():
            results.append({'title':song['title'],'state':'reference-or-transcript-missing'})
            continue
        recognized=json.loads(transcript.read_text(encoding='utf-8'))
        if recognized['source']['source_sha256']!=digest(vocals):
            raise ValueError('Vocal recording changed; refusing to reuse old timing evidence')
        parsed=parse_lrc(reference.read_text(encoding='utf-8'))
        original=[{**cue,'at':cue['at']+parsed['offset_seconds']} for cue in parsed['cues']]
        corrections=[]
        if song['title']=='小幸运' and any('那陪我' in line['text'] for line in recognized['lines']):
            for cue in original:
                if '他陪我淋的雨' in cue['text']:
                    cue['text']=cue['text'].replace('他陪我淋的雨','那陪我淋的雨')
                    corrections.append({'from':'他陪我淋的雨','to':'那陪我淋的雨'})
        comparable=[{**cue,'text':convert.convert(cue['text'])} for cue in original]
        fit=match_reference(comparable,recognized['lines'],duration)
        comparison='characters'
        if not fit['accepted']:
            fit=match_reference(original,recognized['lines'],duration,text_normalizer=phonetic)
            comparison='Chinese pronunciation'
            if fit['accepted'] and (fit['matched_anchors']<12 or fit['coverage']<.9):
                fit.update(accepted=False,reason='phonetic-match-needs-more-recording-evidence',cues=[])
        report={'title':song['title'],'song_id':song['id'],'profile_id':initial['profile_id'],
                'comparison':comparison,'reference_sha256':digest(reference),'audio_sha256':song['conversion_provenance']['mix_sha256'],
                'source_vocals_sha256':recognized['source']['source_sha256'],'corrections':corrections,
                'human_timing_reviewed':False,'imported':False,
                **{key:value for key,value in fit.items() if key!='cues'}}
        if fit['accepted']:
            if len(fit['cues'])!=len(original):
                raise ValueError('Reference text association changed during timing matching')
            cues=[{'at':adjusted['at'],'text':supplied['text']} for adjusted,supplied in zip(fit['cues'],original)]
            lrc=to_lrc(cues,song['title'],artist=parsed['metadata'].get('artist','Astra'))
            prepared=parse_lrc(lrc)
            target=folder/(name+'-matched.lrc')
            target.write_text(lrc,encoding='utf-8')
            report.update(lrc_path=str(target),lrc_sha256=digest(target),cue_count=len(cues),
                          line_count=sum(bool(cue['text']) for cue in cues))
            if args.apply:
                snapshot=studio.call()
                current=next((entry for entry in snapshot['songs'] if entry['id']==song['id']),None)
                if snapshot['profile_id']!=initial['profile_id'] or not current or current.get('blocked'):
                    report['reason']='profile-or-ban-changed-before-import'
                elif current.get('lyrics_cues') and not args.replace_existing:
                    report['reason']='existing-lyrics-retained'
                else:
                    backup=folder/'song-metadata-before-lyrics.json'
                    if not backup.exists():write_json_atomic(backup,current)
                    studio.call('/lyrics',{'song_id':song['id'],'lrc_text':lrc})
                    saved=studio.song(song['id'])
                    if saved.get('lyrics_cues')!=prepared['cues'] or saved.get('lyrics_offset')!=0:
                        raise RuntimeError('Saved default lyrics differ from the matched LRC')
                    report['imported']=True
        write_json_atomic(folder/'matching-report.json',report)
        results.append({key:value for key,value in report.items() if key not in {'reference_sha256','audio_sha256','source_vocals_sha256'}})
        print(json.dumps(results[-1],ensure_ascii=False),flush=True)
    write_json_atomic(ROOT/'data/lyrics/matching-summary.json',{'results':results})


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port',type=int,default=8000,choices=range(1024,65536))
    parser.add_argument('--title',action='append',help='Restrict matching to these saved song titles')
    parser.add_argument('--apply',action='store_true')
    parser.add_argument('--replace-existing',action='store_true')
    main(parser.parse_args())
