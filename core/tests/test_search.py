import unittest
from mito.search import rank_entries


class SearchTests(unittest.TestCase):
    def setUp(self):
        self.entries = [(key,dict(description=description,version=version),'Compatible') for key,description,version in (
            ('main/activemq','Java message server','6.1'),
            ('extras/anypoint-studio','Based on Eclipse IDE','7'),
            ('extras/eclipse','Eclipse IDE','4'),
            ('java/temurin21-jdk','Java development kit','21.0.1'),
            ('java/temurin11-jdk','Java development kit','11.0.1'),
            ('main/python','Python programming language','3.14'),
            ('versions/python313','Python programming language','3.13'))]

    def test_identifier_precedes_description(self):
        self.assertEqual(rank_entries(self.entries,'eclipse')[0][0],'extras/eclipse')
        self.assertTrue(rank_entries(self.entries,'java')[0][0].startswith('java/'))

    def test_slashes_and_multiple_words(self):
        for query in ('python','python/','/python'):
            self.assertEqual(rank_entries(self.entries,query)[0][0],'main/python')
        self.assertEqual(rank_entries(self.entries,'java 21 lts')[0][0],'java/temurin21-jdk')
        self.assertEqual(rank_entries(self.entries,'java21')[0][0],'java/temurin21-jdk')

    def test_disabled_buckets(self):
        rows=rank_entries(self.entries,'python',{'main'})
        self.assertEqual(rows[0][0],'versions/python313')
        self.assertFalse(rank_entries(self.entries,'python',{'main','versions'}))

    def test_empty_query_and_changed_description(self):
        rows = rank_entries(self.entries, '', {'main'})
        self.assertEqual([r[0] for r in rows], sorted(r[0] for r in self.entries if not r[0].startswith('main/')))
        self.assertFalse(rank_entries(self.entries, 'uniquephrase'))
        self.entries[0][1]['description'] = 'uniquephrase'
        self.assertEqual(rank_entries(self.entries, 'uniquephrase')[0][0], 'main/activemq')
