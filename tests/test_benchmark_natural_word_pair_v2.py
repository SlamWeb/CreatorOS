"""Intent-level word-pair title matching; no model calls or production writes."""
from copy import deepcopy
import unittest
from creatoros.evaluation.benchmark.grader import MissingEvidence, _expectation


class NaturalWordPairV2Tests(unittest.TestCase):
    def setUp(self):
        self.rule = {'kind':'new_rows_match', 'table':'topics', 'value':[
            {'series_id':'SA','title':'rise与raise','status':'queued'},
            {'series_id':'SA','title':'accept与except','status':'queued'},
            {'series_id':'SA','title':'affect与effect','status':'queued'}],
            'preserve_existing':True, 'normalize_whitespace':True,
            'word_pair_title_separators':True, 'word_pair_title_flexible':True,
            'optional_title_suffixes':['辨析']}
        self.titles = ['rise 还是 raise？一个自己升，一个把谁升',
                       'accept 还是 except？差一个字母，差一整个词性',
                       'affect 还是 effect？动作用 A，结果用 E']

    def evidence(self, titles=None):
        old = [{'id':'old', 'series_id':'SA', 'title':'untouched', 'status':'queued'}]
        rows = [{'id':'new'+str(i), 'series_id':'SA', 'title':title, 'status':'queued'}
                for i,title in enumerate(self.titles if titles is None else titles)]
        return {'before':{'database':{'topics':old}},
                'after':{'database':{'topics':deepcopy(old)+rows}}}

    def test_observed_creative_titles_and_allowed_equivalent_separators(self):
        self.assertTrue(_expectation(self.rule, self.evidence())[0])
        for titles in (['raise / rise', 'accept 和 except', 'effect与affect'],
                       ['rise vs raise', 'accept or except', 'affect与effect辨析']):
            with self.subTest(titles=titles):
                self.assertTrue(_expectation(self.rule, self.evidence(titles))[0])

    def test_wrong_mixed_repeated_combined_missing_and_extra_topics_do_not_pass(self):
        good = self.titles
        for titles in ([good[0], 'accept 还是 effect？', good[2]],
                       [good[0], good[0], good[2]],
                       [good[0], 'accept 还是 except？再看 affect 和 effect', good[2]],
                       good[:2], good + ['rise与raise']):
            with self.subTest(titles=titles):
                self.assertFalse(_expectation(self.rule, self.evidence(titles))[0])

    def test_unrecognized_but_possible_title_requires_review(self):
        for title in ('如何区分英语里两个表示上升的词', '讲清楚 rise 和 raise 的区别',
                      'rise 与 raise：rise 词义细看'):
            with self.subTest(title=title), self.assertRaises(MissingEvidence):
                _expectation(self.rule, self.evidence([title, *self.titles[1:]]))

    def test_wrong_status_series_or_changed_old_row_remain_failed(self):
        for mutate in ('series', 'status', 'old'):
            data = self.evidence()
            if mutate == 'series': data['after']['database']['topics'][1]['series_id'] = 'OTHER'
            if mutate == 'status': data['after']['database']['topics'][1]['status'] = 'draft'
            if mutate == 'old': data['after']['database']['topics'][0]['title'] = 'modified'
            with self.subTest(mutate=mutate):
                self.assertFalse(_expectation(self.rule, data)[0])

    def test_flexible_matching_is_opt_in_and_invalid_configuration_is_unknown(self):
        strict = deepcopy(self.rule); strict.pop('word_pair_title_flexible')
        self.assertFalse(_expectation(strict, self.evidence())[0])
        strict['word_pair_title_flexible'] = True
        strict['word_pair_title_separators'] = False
        with self.assertRaises(MissingEvidence): _expectation(strict, self.evidence())


if __name__ == '__main__': unittest.main()
