"""Download verified public CTranslate2 weights; no credentials or remote code."""
import hashlib
import json
from pathlib import Path
import sys
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
MODEL_ROOT = ROOT / '.tools/lyrics-models/large-v3-turbo'
REPOSITORY = 'Pengzhendong/faster-whisper-large-v3-turbo'
MODEL_SHA256 = 'e76620f83d5f5b69efd3d87e3dc180c1bd21df9fbebacfd4335e5e1efcc018da'
FILES = ('config.json', 'preprocessor_config.json', 'tokenizer.json', 'vocabulary.json', 'model.bin')
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def download(item):
    path = MODEL_ROOT / item['Path']
    if path.is_file() and path.stat().st_size == item['Size'] and digest(path) == item['Sha256']:
        print(json.dumps({'file':path.name,'state':'verified-cache'}), flush=True)
        return
    partial = path.with_suffix(path.suffix + '.part')
    for attempt in range(4):
        try:
            size = partial.stat().st_size if partial.exists() else 0
            if size > item['Size']:
                raise ValueError('Partial model exceeds the expected size')
            if size != item['Size']:
                url = f'https://modelscope.cn/models/{REPOSITORY}/resolve/{item["Revision"]}/{item["Path"]}'
                headers = {'Accept-Encoding':'identity'}
                if size:
                    headers['Range'] = f'bytes={size}-'
                with OPENER.open(urllib.request.Request(url,headers=headers),timeout=40) as response:
                    append = size > 0 and response.status == 206
                    if append and not response.headers.get('Content-Range','').startswith(f'bytes {size}-'):
                        raise ValueError('Unexpected partial model response')
                    if not append:
                        size = 0
                    with partial.open('ab' if append else 'wb') as stream:
                        reported = time.monotonic()
                        while block := response.read(1024 * 1024):
                            size += len(block)
                            if size > item['Size']:
                                raise ValueError('Model response exceeds declared size')
                            stream.write(block)
                            if time.monotonic()-reported >= 10:
                                print(json.dumps({'file':path.name,'downloaded_mb':round(size/1e6),'total_mb':round(item['Size']/1e6)}),flush=True)
                                reported = time.monotonic()
            if partial.stat().st_size != item['Size'] or digest(partial) != item['Sha256']:
                # Only this generated partial is discarded. Otherwise a corrupt
                # complete-size partial would fail every resume without fetching.
                partial.unlink(missing_ok=True)
                raise ValueError('Model checksum does not match the verified manifest')
            partial.replace(path)
            print(json.dumps({'file':path.name,'state':'verified','bytes':item['Size']}),flush=True)
            return
        except (OSError,ValueError) as error:
            print(json.dumps({'file':path.name,'state':'retry','attempt':attempt+1,'error':type(error).__name__}),flush=True)
            if attempt == 3:
                raise RuntimeError('Public model download did not complete; partial download retained.') from None
            time.sleep(attempt+1)


def main():
    MODEL_ROOT.mkdir(parents=True,exist_ok=True)
    url=f'https://modelscope.cn/api/v1/models/{REPOSITORY}/repo/files?Revision=master&Recursive=true'
    with OPENER.open(url,timeout=20) as response:
        data=json.load(response)
    manifest={item['Path']:item for item in data['Data']['Files'] if item.get('Path') in FILES}
    if set(manifest) != set(FILES) or manifest['model.bin']['Sha256'] != MODEL_SHA256:
        raise RuntimeError('Mirror weights differ from the verified upstream model.')
    for name in FILES:
        download(manifest[name])
    (MODEL_ROOT/'verified-model.json').write_text(json.dumps({
        'upstream':'dropbox-dash/faster-whisper-large-v3-turbo',
        'mirror':REPOSITORY,'model_sha256':MODEL_SHA256,
        'files':{name:{key:manifest[name][key] for key in ('Size','Sha256','Revision')} for name in FILES},
    },indent=2),encoding='utf-8')
    print(json.dumps({'state':'ready','model_path':str(MODEL_ROOT)}),flush=True)


if __name__ == '__main__':
    main()
