"""The prepared-story roster and narration shape are part of the user contract."""
import re
import unittest

from src.config import SafetyConfig
from src.safety import SafetyGate
from src.story import StoryStudio
from src.story_catalog import STORIES
from src.config import TTSConfig


class CozyStoryCatalogTests(unittest.TestCase):
    def test_ten_stable_ids_and_distinct_original_scripts(self):
        self.assertEqual([entry['id'] for entry in STORIES], [
            'breakfast', 'cake', 'cleaning', 'blackout', 'laundry', 'noodles',
            'mint', 'first-stream', 'rainy-drive', 'goodnight-home'])
        self.assertEqual(len({entry['title'] for entry in STORIES}), 10)
        self.assertEqual(len({entry['text'] for entry in STORIES}), 10)

    def test_every_story_keeps_the_three_roommates_and_no_other_model_characters(self):
        banned = re.compile(r'GPT|ChatGPT|GLM|MiMo|Gemini|智谱|米莫|豆包|千问|通义|文心|元宝|Kimi', re.I)
        for entry in STORIES:
            with self.subTest(story=entry['id']):
                for member in ('Astra', 'Claude', 'DeepSeek'):
                    self.assertIn(member, entry['scene'])
                for member in ('我', '克劳德', '小鲸'):
                    self.assertIn(member, entry['text'])
                self.assertIsNone(banned.search(entry['scene'] + entry['text']))

    def test_scripts_are_short_tagged_narration_and_pass_the_local_output_gate(self):
        safety = SafetyGate(SafetyConfig())
        for entry in STORIES:
            with self.subTest(story=entry['id']):
                chinese = re.findall(r'[\u4e00-\u9fff]', entry['text'])
                self.assertGreaterEqual(len(chinese), 180)
                self.assertLessEqual(len(chinese), 260)
                self.assertGreaterEqual(len(re.findall(r'（[^）]+）', entry['text'])), 3)
                self.assertLessEqual(len(re.findall(r'（[^）]+）', entry['text'])), 5)
                self.assertTrue(safety.allows_output(entry['text']))
                self.assertNotRegex(entry['text'], r'我也终于明白|最重要的道理|人生的意义')

    def test_car_story_keeps_the_car_parked_during_food_and_book_handling(self):
        story = next(entry for entry in STORIES if entry['id'] == 'rainy-drive')
        self.assertIn('尚未启动', story['scene'])
        self.assertIn('车一直停在院子里', story['text'])

    def test_changing_a_script_invalidates_its_prepared_audio_token(self):
        studio = StoryStudio('.', None)
        story = STORIES[0]
        changed = {**story, 'text': story['text'] + '（轻笑）再给你留一小块。'}
        self.assertNotEqual(studio.token('astra', TTSConfig(), story),
                            studio.token('astra', TTSConfig(), changed))
