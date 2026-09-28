"""Preserve speech clocks and source text while enforcing short display cues."""
import random
import unittest

from autodub.speechmap import SpeechMap
from autodub.srt_utils import Segment
from autodub.asr.screen_pack import pack


class IndexedSpeechWindows(unittest.TestCase):
    def test_nested_observation_is_not_lost_by_fixed_lookback(self):
        marks=[(0,20),(1,2),(2,3),(3,4),(4,5),(5,6)]
        self.assertEqual(SpeechMap(marks).window(9,11),[(0,20)])

    def test_matches_exhaustive_oracle_with_overlaps(self):
        rng=random.Random(20260927)
        sm=SpeechMap([(a:=rng.uniform(0,500),a+rng.uniform(.01,50)) for _ in range(2000)])
        for _ in range(300):
            start=rng.uniform(0,530);end=start+rng.uniform(.01,20)
            expected=[(a,b) for a,b in sm.marks if start <= (a+b)/2 <= end]
            self.assertEqual(sm.window(start,end),expected)

    def test_nonfinite_observations_do_not_poison_index(self):
        sm=SpeechMap([(0,1),(float('nan'),2),(2,float('inf'))])
        self.assertEqual(sm.window(0,2),[(0,1)])

    def test_boundaries_and_transforms_preserve_query_contract(self):
        sm=SpeechMap([(1,2),(2,3)])
        self.assertEqual(sm.window(1.5,2.5),sm.marks)
        self.assertEqual(sm.shift(10).window(11.5,12.5),[(11,12),(12,13)])
        self.assertEqual(sm.scale(2).window(3,5),[(2,4),(4,6)])


class ShortDisplayCues(unittest.TestCase):
    def test_protected_long_phrase_cannot_override_hard_character_limit(self):
        text='中华人民共和国中央人民政府最高人民法院行政管理委员会'
        marks=[(i*.15,(i+1)*.15) for i in range(len(text))]
        out=pack([Segment(1,0,marks[-1][1],text)],speech_map=SpeechMap(marks),
                 protected_words=[text],max_chars=10,hard_max_chars=14)
        self.assertEqual(''.join(s.text for s in out),text)
        self.assertTrue(all(len(s.text)<=14 for s in out))
        self.assertEqual(out[0].start,0)
        self.assertEqual(out[-1].end,marks[-1][1])
        self.assertTrue(all(s.start in {a for a,b in marks} and s.end in {b for a,b in marks}
                            for s in out))

    def test_slow_protected_phrase_splits_on_existing_clocks(self):
        text='中华人民共和国'
        marks=[(i*.9,(i+1)*.9) for i in range(len(text))]
        out=pack([Segment(1,0,6.3,text)],speech_map=SpeechMap(marks),
                 protected_words=[text],max_duration=2,hard_max_duration=2.8)
        self.assertEqual(''.join(s.text for s in out),text)
        self.assertTrue(all(s.duration<=2.8 for s in out))

    def test_indivisible_bad_clock_is_reported_without_fabrication(self):
        review=[]
        pack([Segment(1,0,10,'我')],speech_map=SpeechMap([(0,10)]),
             hard_max_duration=4,review=review)
        self.assertTrue(any(r['reason']=='token_exceeds_screen_limit' and r['withheld']
                            for r in review))

    def test_many_lengths_preserve_text_and_clock_limits(self):
        for size in range(15,90,7):
            text=('中华人民共和国中央人民政府'*8)[:size]
            marks=[(i*.13,(i+1)*.13) for i in range(size)]
            with self.subTest(size=size):
                out=pack([Segment(1,0,marks[-1][1],text)],speech_map=SpeechMap(marks),
                         protected_words=[text],max_chars=10,hard_max_chars=14,
                         max_duration=1.5,hard_max_duration=2)
                self.assertEqual(''.join(s.text for s in out),text)
                self.assertTrue(all(len(s.text)<=14 and s.duration<=2+1e-6 for s in out))
                self.assertTrue(all(a.end<=b.start for a,b in zip(out,out[1:])))
