"""Recording/version matching uses invented phrases, never a song database."""
import copy
import math
import unittest

from src.lyric_match import match_reference, normalize_text


PHRASES = [
    '小龙背着星光去散步', '小鲸把云朵装进口袋', '克劳德煎蛋翻到了月亮',
    '糖罐里的纸条说晚安', '风把围巾悄悄吹成花', '雨滴在车窗上画小猫',
    '三个人围着面包开会', '最软的靠垫留给朋友', '银色的尾巴摇动铃铛',
    '今晚的星星全部准时', '茶壶冒出一团小彩虹', '我们把快乐装进明天',
]


def recording(*, offset=1.25, scale=1., phrases=PHRASES, starts=None):
    starts = starts if starts is not None else [8.+i*7. for i in range(len(phrases))]
    cues = [{'at':at,'text':text} for at,text in zip(starts,phrases)]
    lines = [{'start':at*scale+offset,'end':at*scale+offset+3.5,
              'text':text,'confidence':.96} for at,text in zip(starts,phrases)]
    return cues, lines


class ReferenceMatchTests(unittest.TestCase):
    def test_fixed_offset_preserves_literal_text_and_blank_cues(self):
        cues, lines = recording()
        cues.insert(0,{'at':0.,'text':'作词：小龙工作室'})
        cues.append({'at':95.,'text':''})
        original = copy.deepcopy((cues,lines))
        result = match_reference(cues,lines,100.)
        self.assertTrue(result['accepted'],result)
        self.assertEqual(result['matched_anchors'],len(PHRASES))
        self.assertEqual(result['offset_seconds'],1.25)
        self.assertEqual(result['scale'],1.)
        self.assertEqual(result['median_error_seconds'],0.)
        self.assertEqual([cue['text'] for cue in result['cues']],[cue['text'] for cue in cues])
        self.assertEqual(result['cues'][1]['at'],9.25)
        self.assertEqual((cues,lines),original)

    def test_nfkc_case_and_punctuation_are_comparison_only(self):
        self.assertEqual(normalize_text('Ａstra，晚安！ １２'), 'astra晚安12')
        cues,lines = recording()
        for line in lines:
            line['text']='  '+line['text']+'！！'
        result=match_reference(cues,lines,100.)
        self.assertTrue(result['accepted'],result)
        self.assertEqual(result['cues'][0]['text'],PHRASES[0])

    def test_fuzzy_word_errors_do_not_replace_reference_text(self):
        cues,lines=recording()
        for i in (2,5,8):
            line=lines[i]
            line['text']=line['text'][:3]+'错'+line['text'][4:]
        result=match_reference(cues,lines,100.)
        self.assertTrue(result['accepted'],result)
        self.assertEqual([cue['text'] for cue in result['cues']],PHRASES)

    def test_adjacent_asr_split_lines_use_original_phrase_onset(self):
        cues,lines=recording()
        split=[]
        for i,line in enumerate(lines):
            if i%2==0:
                first=dict(line,text=line['text'][:4],end=line['start']+1.3)
                second=dict(line,text=line['text'][4:],start=line['start']+1.4)
                split.extend([first,second])
            else:
                split.append(line)
        result=match_reference(cues,split,100.)
        self.assertTrue(result['accepted'],result)
        self.assertEqual(result['offset_seconds'],1.25)

    def test_three_asr_fragments_can_match_one_reference_phrase(self):
        cues,lines=recording()
        split=[]
        for line in lines:
            for index,(first,last) in enumerate(((0,3),(3,6),(6,None))):
                split.append(dict(line,text=line['text'][first:last],
                                  start=line['start']+index,end=line['start']+index+.9))
        result=match_reference(cues,split,100.)
        self.assertTrue(result['accepted'],result)
        self.assertEqual(result['offset_seconds'],1.25)

    def test_phonetic_normalization_never_replaces_reference_characters(self):
        cues,lines=recording()
        # Invented comparison-only homophone mapping, not a model or dictionary.
        substitute=str.maketrans({'龙':'隆','鲸':'京','云':'芸','光':'广','小':'晓'})
        for line in lines:line['text']=line['text'].translate(substitute)
        result=match_reference(cues,lines,100.,text_normalizer=lambda text:text.translate(substitute))
        self.assertTrue(result['accepted'],result)
        self.assertEqual([cue['text'] for cue in result['cues']],PHRASES)

    def test_invalid_comparison_normalizer_cannot_change_output_type(self):
        cues,lines=recording()
        with self.assertRaises(ValueError):
            match_reference(cues,lines,100.,text_normalizer=lambda text:42)

    def test_phonetic_comparison_still_rejects_reordered_and_short_versions(self):
        cues,lines=recording()
        substitute=str.maketrans({'龙':'隆','鲸':'京','云':'芸','光':'广','小':'晓'})
        normalize=lambda text:text.translate(substitute)
        wrong=copy.deepcopy(lines)
        for line,text in zip(wrong,PHRASES[6:]+PHRASES[:6]):
            line['text']=text.translate(substitute)
        for version in (wrong,lines[:7]):
            result=match_reference(cues,version,100.,text_normalizer=normalize)
            self.assertFalse(result['accepted'],result)
            self.assertEqual(result['cues'],[])

    def test_adjacent_reference_split_lines_can_use_a_merged_asr_phrase(self):
        cues,lines=recording()
        merged=[]
        for index in range(0,len(lines),2):
            first,second=lines[index:index+2]
            merged.append(dict(first,end=second['end'],text=first['text']+second['text']))
        result=match_reference(cues,merged,100.)
        self.assertTrue(result['accepted'],result)
        self.assertEqual(result['offset_seconds'],1.25)

    def test_near_unity_scale_is_fitted_only_when_it_explains_drift(self):
        for scale in (.9745,1.012):
            with self.subTest(scale=scale):
                cues,lines=recording(offset=.7,scale=scale)
                result=match_reference(cues,lines,100.)
                self.assertTrue(result['accepted'],result)
                self.assertAlmostEqual(result['scale'],scale,places=6)
                self.assertAlmostEqual(result['offset_seconds'],.7,places=6)
                self.assertEqual(result['reason'],'matched_near_unity_scale')

    def test_large_tempo_difference_is_rejected(self):
        cues,lines=recording(offset=0.,scale=1.12)
        result=match_reference(cues,lines,110.)
        self.assertFalse(result['accepted'])
        self.assertEqual(result['cues'],[])
        self.assertEqual(result['reason'],'scale_outside_supported_range')

    def test_an_edited_instrumental_bridge_is_not_hidden_by_a_global_offset(self):
        cues,lines=recording()
        for line in lines[6:]:
            line['start']+=8
            line['end']+=8
        result=match_reference(cues,lines,110.)
        self.assertFalse(result['accepted'])
        self.assertEqual(result['cues'],[])

    def test_reordered_verses_are_rejected(self):
        cues,lines=recording()
        rotated=PHRASES[6:]+PHRASES[:6]
        for line,text in zip(lines,rotated):line['text']=text
        result=match_reference(cues,lines,100.)
        self.assertFalse(result['accepted'])
        self.assertEqual(result['cues'],[])

    def test_shortened_song_and_matching_title_only_are_not_enough(self):
        cues,lines=recording()
        for short in (lines[:3],lines[:7]):
            result=match_reference(cues,short,100.)
            self.assertFalse(result['accepted'],result)
            self.assertEqual(result['cues'],[])
        empty=match_reference([{'at':0,'text':'小龙早餐歌'}],lines,100.)
        self.assertFalse(empty['accepted'])

    def test_repeated_choruses_keep_their_correct_monotonic_occurrences(self):
        text=PHRASES[:6]+PHRASES[:6]+PHRASES[6:]
        cues,lines=recording(phrases=text)
        result=match_reference(cues,lines,145.)
        self.assertTrue(result['accepted'],result)
        self.assertEqual(len(result['cues']),len(text))
        self.assertEqual(result['cues'][0]['at'],9.25)
        self.assertEqual(result['cues'][6]['at'],51.25)

    def test_identical_short_refrain_is_not_six_independent_anchors(self):
        cues,lines=recording(phrases=['小龙小龙快回家']*12)
        result=match_reference(cues,lines,100.)
        self.assertFalse(result['accepted'])
        self.assertEqual(result['reason'],'insufficient_independent_anchors')

    def test_anchor_cluster_in_one_verse_does_not_validate_the_whole_song(self):
        cues,lines=recording()
        for line in lines[8:]:
            line['confidence']=.1
        result=match_reference(cues,lines,100.)
        self.assertFalse(result['accepted'])
        self.assertEqual(result['reason'],'insufficient_timeline_coverage')

    def test_outlier_at_the_end_cannot_validate_a_cluster_of_early_anchors(self):
        cues,lines=recording(phrases=PHRASES+['月光把门牌擦得亮晶晶'])
        for line in lines[9:-1]:
            line['confidence']=.1
        lines[-1]['start']+=3
        lines[-1]['end']+=3
        result=match_reference(cues,lines,110.)
        self.assertFalse(result['accepted'],result)
        self.assertEqual(result['reason'],'insufficient_inlier_timeline_coverage')

    def test_isolated_timing_outlier_does_not_shift_other_lines(self):
        cues,lines=recording()
        lines[4]['start']+=1.1
        lines[4]['end']+=1.1
        result=match_reference(cues,lines,100.)
        self.assertTrue(result['accepted'],result)
        self.assertEqual(result['offset_seconds'],1.25)

    def test_negative_offset_bounds_non_sung_headers_without_wrapping_time(self):
        cues,lines=recording(offset=-2.)
        cues.insert(0,{'at':0.,'text':'作曲：小鲸工作室'})
        cues.append({'at':101.,'text':''})
        result=match_reference(cues,lines,100.)
        self.assertTrue(result['accepted'],result)
        self.assertEqual(result['cues'][0]['at'],0.)
        self.assertTrue(all(0<=cue['at']<=100 for cue in result['cues']))

    def test_sung_line_outside_recording_causes_rejection(self):
        cues,lines=recording()
        cues.append({'at':105.,'text':'这一句其实不属于录音'})
        result=match_reference(cues,lines,100.)
        self.assertFalse(result['accepted'])
        self.assertEqual(result['reason'],'reference_exceeds_recording')

    def test_invalid_timestamps_types_confidence_and_order_are_rejected(self):
        cues,lines=recording()
        invalid=[([{'at':math.nan,'text':'你好'}],lines),
                 ([{'at':True,'text':'你好'}],lines),
                 ([{'at':2,'text':'晚安'},{'at':1,'text':'早安'}],lines),
                 (cues,[dict(lines[0],end=101.)]),
                 (cues,[dict(lines[0],confidence=1.1)]),
                 (cues,[dict(lines[0],text='坏\0词')]),
                 (cues,[dict(lines[0],start=5,end=3)]),
                 (cues,list(reversed(lines)))]
        for reference,observed in invalid:
            with self.subTest(reference=reference[:1],observed=observed[:1]),self.assertRaises(ValueError):
                match_reference(reference,observed,100.)


if __name__=='__main__':
    unittest.main()
