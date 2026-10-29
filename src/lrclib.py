"""Read-only LRCLIB lookup with sequential throttling and bounded retries."""
from email.utils import parsedate_to_datetime
import json
import math
import time
import urllib.error
import urllib.parse
import urllib.request


USER_AGENT = 'AstraLyrics/0.1 (personal LRCLIB client; https://lrclib.net/docs)'
MAX_RESPONSE = 4 * 1024 * 1024


class LyricsLookupError(RuntimeError):
    pass


class LRCLib:
    def __init__(self, *, opener=None, sleep=time.sleep, clock=time.monotonic):
        self.opener = opener or urllib.request.build_opener(urllib.request.ProxyHandler({}))
        self.sleep, self.clock = sleep, clock
        self._last_request = float('-inf')

    def _get(self, path, query=None):
        url = 'https://lrclib.net/api/' + path
        if query:
            url += '?' + urllib.parse.urlencode(query)
        for attempt in range(4):
            self.sleep(max(0,.35-(self.clock()-self._last_request)))
            self._last_request = self.clock()
            request=urllib.request.Request(url,headers={'User-Agent':USER_AGENT,'Accept':'application/json'})
            try:
                with self.opener.open(request,timeout=20) as response:
                    if 'json' not in response.headers.get('Content-Type','').lower():
                        raise LyricsLookupError('LRCLIB returned an unexpected response type.')
                    raw=response.read(MAX_RESPONSE+1)
                    if len(raw)>MAX_RESPONSE:
                        raise LyricsLookupError('LRCLIB response was too large.')
                    return json.loads(raw)
            except urllib.error.HTTPError as error:
                if error.code==404:
                    return None
                if error.code not in {429,500,502,503,504} or attempt==3:
                    raise LyricsLookupError(f'LRCLIB lookup failed (HTTP {error.code}).') from None
                retry=error.headers.get('Retry-After','1')
                try:
                    seconds=float(retry)
                except ValueError:
                    try:
                        seconds=parsedate_to_datetime(retry).timestamp()-time.time()
                    except (ValueError,TypeError,OverflowError):
                        seconds=1
                if not math.isfinite(seconds) or seconds>60:
                    raise LyricsLookupError('LRCLIB asked for a longer pause; try again later.') from None
                self.sleep(max(1,seconds))
            except (OSError,ValueError):
                raise LyricsLookupError('LRCLIB could not be reached or returned invalid data.') from None

    def search(self, title, artist=''):
        if not isinstance(title,str) or not 1<=len(title)<=160 or not isinstance(artist,str) or len(artist)>160:
            raise ValueError('Use a bounded song title and artist.')
        params={'track_name':title}
        if artist:
            params['artist_name']=artist
        result=self._get('search',params)
        if result is None:
            return []
        if not isinstance(result,list) or len(result)>100:
            raise LyricsLookupError('LRCLIB search format was invalid.')
        return [row for row in result if isinstance(row,dict)]

    def get(self, record_id):
        if type(record_id) is not int or record_id<=0:
            raise ValueError('Invalid LRCLIB record ID.')
        result=self._get('get/'+str(record_id))
        if result is not None and not isinstance(result,dict):
            raise LyricsLookupError('LRCLIB record format was invalid.')
        return result
