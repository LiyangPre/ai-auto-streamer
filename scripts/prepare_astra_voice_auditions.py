"""Create private speaking-voice auditions without changing Astra or converting songs."""
import argparse
import asyncio
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from mutagen.mp3 import MP3
from src.api_settings import ConnectionStore, connection_path
from src.characters import library_path, load_library
from src.config import Config, PROJECT_ROOT
from src.studio import load_saved, settings_path
from src.tts import TTSEngine


TEXT = '晚上好，今天过得怎么样？慢慢说，我在听。要不要一起去看看夜景？'
CANDIDATES = [
    ('A', '清冷从容', '成年女性，声线中低音，清冷从容，音质干净，略带自然气声。以亲近而克制的口吻聊天，语速自然，停顿流畅，咬字清晰，不夸张表演，不使用童声。'),
    ('B', '温柔自然', '成年女性，自然柔和的中音声线，温暖、轻松、亲切，像朋友在安静地聊天。声音有真实的呼吸感和轻微笑意，语速自然，表达细腻，咬字清晰，不使用童声，不用播音腔。'),
    ('C', '低声慵懒', '成年女性，偏低的圆润声线，略微沙哑，慵懒而沉稳，带一点轻柔气声。像在夜晚近距离轻松聊天，语速略慢但不拖长，停顿自然，咬字清晰，不刻意耳语，不使用童声。'),
    ('B-young', '温柔清甜', '年轻成年女性，二十岁左右，声线明亮清甜，音高偏高，轻盈柔和，有青春活力和自然的可爱感。保留温暖、亲切、细腻的聊天口吻，带一点轻柔笑意，像朋友在分享开心的小事。语速自然轻快，停顿流畅，咬字清晰，声音有真实的呼吸感，不用播音腔，不夸张表演。'),
]


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--candidate', choices=[item[0] for item in CANDIDATES], action='append',
                        help='Generate only this audition; repeat to select several.')
    args = parser.parse_args()
    selected = set(args.candidate or ['A', 'B', 'C'])
    config = Config.load()
    studio_path = settings_path('config.yaml')
    load_saved(config, studio_path)
    library = load_library(config, library_path(studio_path))
    library.profiles['astra'].settings(library.scene).apply(config)
    connections = ConnectionStore(connection_path(studio_path), config)
    connections.load()
    connections.apply(config)
    destination = PROJECT_ROOT / 'data/voice-auditions'
    destination.mkdir(parents=True, exist_ok=True)
    manifest = {'status': 'awaiting_user_choice', 'approved': None,
                'model': 'mimo-v2.5-tts-voicedesign', 'text': TEXT, 'candidates': []}
    manifest_path = destination / 'auditions.json'
    if manifest_path.exists():
        previous = json.loads(manifest_path.read_text(encoding='utf-8'))
        manifest['candidates'] = previous.get('candidates', [])
        for key in ('approved', 'selection', 'reference'):
            if key in previous:
                manifest[key] = previous[key]
        if manifest['approved']:
            manifest['status'] = 'approved_with_new_audition'
    generated = []
    for candidate_id, name, style in CANDIDATES:
        if candidate_id not in selected:
            continue
        target = destination / f'astra-{candidate_id}.mp3'
        if candidate_id == manifest['approved'] and target.is_file():
            print(json.dumps({'id': candidate_id, 'status': 'kept_approved_audition'}, ensure_ascii=False), flush=True)
            continue
        result = {'id': candidate_id, 'name': name, 'style': style, 'audio': str(target)}
        voice = config.tts.model_copy(deep=True)
        voice.engine = 'mimo-tts'
        voice.mimo_model = manifest['model']
        voice.mimo_style = style
        voice.timeout = 90
        engine = TTSEngine(voice, destination)
        try:
            started = time.perf_counter()
            url = await engine.synthesize(TEXT, 'normal')
            result['generation_ms'] = round((time.perf_counter() - started) * 1000)
            result['ok'] = bool(url)
            if url:
                source = destination / url.rsplit('/', 1)[-1]
                source.replace(target)
                result['duration_seconds'] = round(MP3(target).info.length, 3)
                result['bytes'] = target.stat().st_size
            else:
                result['error'] = engine.last_error
        finally:
            await engine.close()
        manifest['candidates'] = [item for item in manifest['candidates'] if item['id'] != candidate_id]
        manifest['candidates'].append(result)
        generated.append(result)
        if candidate_id == 'B-young':
            manifest.update(status='approved_with_new_audition' if manifest['approved'] else 'awaiting_revised_voice_review', requested_base='B',
                            requested_changes=['younger', 'cuter', 'higher_pitch'])
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(result, ensure_ascii=False), flush=True)
    if not all(item['ok'] for item in generated):
        raise SystemExit(1)


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    asyncio.run(main())
