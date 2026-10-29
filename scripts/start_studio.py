"""Start the local studio and open its controller and HTML output."""
import argparse
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from src.config import Config


def studio_ready(base):
    try:
        # This is a local controller request, subject to the same browser
        # boundary as other private API reads. Never proxy loopback probes.
        request=urllib.request.Request(base+'/api/config',headers={'X-Studio-API':'1'})
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(request,timeout=1) as response:
            value=json.load(response)
        return isinstance(value,dict) and {'renderer','platform','tts_engine'} <= value.keys()
    except (urllib.error.URLError,TimeoutError,OSError,ValueError):
        return False


def stop_server(process):
    """Stop only the server tree created by this launcher (venv on Windows forks)."""
    if process.poll() is not None:
        return
    if sys.platform == 'win32':
        subprocess.run(['taskkill','/PID',str(process.pid),'/T','/F'],
                       stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,check=False)
    else:
        process.terminate()
    process.wait(timeout=5)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',default='config.yaml')
    parser.add_argument('--no-browser',action='store_true',help='Print URLs without opening browser tabs')
    parser.add_argument('--check',action='store_true',help='Validate configuration and print URLs without starting')
    args=parser.parse_args()
    config=Config.load(args.config)
    host=config.server.host if config.server.host not in {'0.0.0.0','::'} else '127.0.0.1'
    if ':' in host and not host.startswith('['):host='['+host+']'
    base=f'http://{host}:{config.server.port}'
    urls=[base+'/control',base+'/?overlay=1&controls=0']
    print('Controller: '+urls[0],flush=True)
    print('HTML preview / Bilibili browser source: '+urls[1],flush=True)
    print(f'Capture size: {config.display.width} x {config.display.height}',flush=True)
    if args.check:
        print('Configuration valid. No server or browser was started.',flush=True)
        return 0

    def open_pages():
        if not args.no_browser:
            for url in urls:webbrowser.open_new_tab(url)

    if studio_ready(base):
        print('The studio is already running; reusing it.',flush=True)
        open_pages()
        return 0
    process=subprocess.Popen([sys.executable,'-X','utf8',str(ROOT/'main.py'),'--config',args.config],cwd=ROOT)
    try:
        # Song conversion can saturate the CPU while imports and the local
        # moderation engine initialize. Do not kill a healthy slow startup.
        deadline=time.monotonic()+120
        while time.monotonic()<deadline:
            if process.poll() is not None:
                print('The server exited. Read its error above.',flush=True)
                return process.returncode or 1
            if studio_ready(base):break
            time.sleep(.2)
        else:
            print('Startup timed out. Check the server log above.',flush=True)
            stop_server(process)
            return 1
        open_pages()
        print('Keep this console open. Press Ctrl+C here to stop the studio.',flush=True)
        return process.wait()
    except KeyboardInterrupt:
        # Windows delivers console Ctrl+C to the server too. Allow graceful exit.
        try:process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            stop_server(process)
        return 0


if __name__=='__main__':
    raise SystemExit(main())
