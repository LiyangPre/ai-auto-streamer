"""Download selected public recordings through bili-dl's bundled yt-dlp engine.

The bili-dl CLI requires a saved login even for public media. This uses its engine
without cookies for anonymously accessible audio, with no video or browser access.
"""
import argparse,json,re,subprocess,time
from pathlib import Path
import imageio_ffmpeg
from yt_dlp import YoutubeDL


class QuietLog:
    def debug(self,*args):pass
    def warning(self,*args):pass
    def error(self,*args):pass


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--manifest',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True);args=parser.parse_args();args.output.mkdir(parents=True,exist_ok=True)
    requested=json.loads(args.manifest.read_text(encoding='utf-8'));proof_path=args.output/'download-results.json'
    results=json.loads(proof_path.read_text(encoding='utf-8')) if proof_path.exists() else []
    for entry in requested:
        bvid=entry['bvid']
        if not re.fullmatch(r'BV[a-zA-Z0-9]{10}',bvid):raise ValueError('Invalid video ID')
        if any(old['bvid']==bvid and old.get('ok') and Path(old['mp3']).is_file() for old in results):continue
        result={'bvid':bvid,'requested_title':entry['title'],'source_url':'https://www.bilibili.com/video/'+bvid+'/','engine':'bili-dl dependency yt-dlp, anonymous audio-only'}
        try:
            with YoutubeDL({'format':'bestaudio','noplaylist':True,'quiet':True,'no_warnings':True,'logger':QuietLog(),
                            'proxy':'','socket_timeout':20,'retries':2,'outtmpl':str(args.output/(bvid+'.%(ext)s'))}) as ydl:
                info=ydl.extract_info(result['source_url'],download=True);source=Path(ydl.prepare_filename(info))
            name=re.sub(r'[<>:"/\\|?*]','_',entry['title'])+'-'+bvid+'.mp3';target=args.output/name
            completed=subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(),'-hide_banner','-loglevel','error','-y','-i',str(source),
                                      '-vn','-c:a','libmp3lame','-b:a','192k',str(target)],capture_output=True)
            if completed.returncode or not target.is_file() or not target.stat().st_size:raise RuntimeError('Audio conversion failed')
            result.update(ok=True,title=info.get('title'),uploader=info.get('uploader'),duration=info.get('duration'),
                          original_audio=str(source.resolve()),mp3=str(target.resolve()),bytes=target.stat().st_size)
        except Exception as error:result.update(ok=False,error_type=type(error).__name__)
        results=[old for old in results if old['bvid']!=bvid]+[result]
        proof_path.write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(result,ensure_ascii=False),flush=True)
        time.sleep(1)
    if any(not item.get('ok') for item in results):raise SystemExit(1)


if __name__=='__main__':main()
