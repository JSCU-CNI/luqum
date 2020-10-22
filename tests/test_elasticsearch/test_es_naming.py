from unittest import TestCase, skipIf

from elasticsearch_dsl import Q

from luqum.naming import (
    auto_name, element_from_name, HTMLMarker, matching_from_names, MatchingPropagator,
)
from luqum.parser import parser


from .es_integration_utils import (
    add_book_data, book_query_builder, book_search, get_es, remove_book_index,
)


@skipIf(get_es() is None, "Skipping ES test as ES seems unreachable")
class LuqumNamingTestCase(TestCase):
    """This test is testing naming of queries integration with ES
    """

    @classmethod
    def setUpClass(cls):
        cls.es_client = get_es()
        if cls.es_client is None:
            return
        cls.es_builder = book_query_builder(cls.es_client)
        cls.propagate_matching = MatchingPropagator()
        cls.search = book_search(cls.es_client)
        add_book_data(cls.es_client)
        cls.make_html = HTMLMarker()

    def test_keyword_naming(self):
        ltree = parser.parse("illustrators.nationality:UK")
        names = auto_name(ltree)
        query = self.es_builder(ltree)
        results = list(self.search.filter(query).execute())
        book = results[0]
        self.assertEqual(book.meta.matched_queries, ["a"])
        self.assertEqual(
            element_from_name(ltree, book.meta.matched_queries[0], names),
            ltree,
        )
        paths_ok, paths_ko = self.propagate_matching(
            ltree, matching_from_names(book.meta.matched_queries, names),
        )
        self.assertEqual(
            self.make_html(ltree, paths_ok, paths_ko),
            '<span class="ok">illustrators.nationality:UK</span>',
        )

    def test_or_operation(self):
        ltree = parser.parse("n_pages:360 OR edition:Lumos")
        names = auto_name(ltree)
        query = self.es_builder(ltree)
        # the one matching Lumos
        book, = list(self.search.filter(query).filter("term", ref="BB1").execute())
        self.assertEqual(len(book.meta.matched_queries), 1)
        paths_ok, paths_ko = self.propagate_matching(
            ltree, matching_from_names(book.meta.matched_queries, names),
        )
        self.assertEqual(
            self.make_html(ltree, paths_ok, paths_ko),
            '<span class="ok"><span class="ko">n_pages:360 </span>OR edition:Lumos</span>',
        )
        # the one matching n_pages
        book, = list(self.search.filter(query).filter("term", ref="HP8").execute())
        self.assertEqual(len(book.meta.matched_queries), 1)
        paths_ok, paths_ko = self.propagate_matching(
            ltree, matching_from_names(book.meta.matched_queries, names),
        )
        self.assertEqual(
            self.make_html(ltree, paths_ok, paths_ko),
            '<span class="ok">n_pages:360 OR<span class="ko"> edition:Lumos</span></span>',
        )
        # matching None
        book, = list(self.search.filter(Q(query) | Q("match_all")).filter(Q("term", ref="HP7")).execute())
        self.assertFalse(hasattr(book.meta, "matched_queries"))

    def test_and_operation_matching(self):
        ltree = parser.parse("n_pages:157 AND edition:Lumos")
        names = auto_name(ltree)
        query = self.es_builder(ltree)
        # matching Lumos and n_pages
        book, = list(self.search.filter(query).filter("term", ref="BB1").execute())
        self.assertEqual(len(book.meta.matched_queries), 2)
        paths_ok, paths_ko = self.propagate_matching(
            ltree, matching_from_names(book.meta.matched_queries, names),
        )
        self.assertEqual(
            self.make_html(ltree, paths_ok, paths_ko),
            '<span class="ok">n_pages:157 AND edition:Lumos</span>',
        )

    def test_and_operation_not_matching(self):
        ltree = parser.parse("n_pages:360 AND edition:Lumos")
        names = auto_name(ltree)
        query = self.es_builder(ltree)
        # matching only Lumos
        book, = list(self.search.filter(Q(query) | Q("term", ref="BB1")).execute())
        self.assertEqual(len(book.meta.matched_queries), 1)
        paths_ok, paths_ko = self.propagate_matching(
            ltree, matching_from_names(book.meta.matched_queries, names),
        )
        self.assertEqual(
            self.make_html(ltree, paths_ok, paths_ko),
            '<span class="ko">n_pages:360 AND<span class="ok"> edition:Lumos</span></span>',
        )
        # matching None
        book, = list(self.search.filter(Q(query) | Q("term", ref="HP7")).execute())
        self.assertFalse(hasattr(book.meta, "matched_queries"))

    def _negation_test(self, operator):
        ltree = parser.parse(f"{operator}n_pages:360 AND edition:Lumos")
        names = auto_name(ltree)
        query = self.es_builder(ltree)
        # matching Lumos
        book, = list(self.search.filter(query).filter("term", ref="BB1").execute())
        self.assertEqual(len(book.meta.matched_queries), 1)
        paths_ok, paths_ko = self.propagate_matching(
            ltree, matching_from_names(book.meta.matched_queries, names),
        )
        self.assertEqual(
            self.make_html(ltree, paths_ok, paths_ko),
            f'<span class="ok">{operator}n_pages:360 AND edition:Lumos</span>',
        )
        # matching n_pages and not lumos
        book, = list(self.search.filter(Q(query) | Q("term", ref="HP8")).exclude(Q("term", ref="BB1")).execute())
        self.assertEqual(len(book.meta.matched_queries), 1)
        paths_ok, paths_ko = self.propagate_matching(
            ltree, matching_from_names(book.meta.matched_queries, names),
        )
        self.assertEqual(
            self.make_html(ltree, paths_ok, paths_ko),
            f'<span class="ko"><span class="ok">{operator}n_pages:360 </span>'
            f'AND edition:Lumos</span>',
        )
        # matching none
        book, = list(self.search.filter(Q(query) | Q("term", ref="HP7")).exclude(Q("term", ref="BB1")).execute())
        self.assertFalse(hasattr(book.meta, "matched_queries"))

    def test_not(self):
        self._negation_test("NOT ")

    def test_minus(self):
        self._negation_test("-")
        
    def _simple_test(self, matching_query, ref, num_match=1):
        """simple scenario

        :param str matching_query: the query that match the book
        :param str ref: ref of expected matching book
        """
        ltree = parser.parse(f"{matching_query} OR n_pages:1000")
        names = auto_name(ltree)
        query = self.es_builder(ltree)
        book, = list(self.search.filter(query).execute())
        self.assertEqual(book.ref, ref)
        self.assertEqual(len(book.meta.matched_queries), num_match)
        paths_ok, paths_ko = self.propagate_matching(
            ltree, matching_from_names(book.meta.matched_queries, names),
        )
        self.assertEqual(
            self.make_html(ltree, paths_ok, paths_ko),
            f'<span class="ok">{matching_query} OR<span class="ko"> n_pages:1000</span></span>',
        )

    def test_fuzzy(self):
        self._simple_test("ref:BB~1", "BB1")

    def test_proximity(self):
        self._simple_test('title:"Harry Potter Phoenix"~6', "HP5")

    def test_boost(self):
        self._simple_test('title:"Phoenix"^4', "HP5")

    def test_plus(self):
        self._simple_test('+title:"Phoenix"', "HP5")

    def test_range(self):
        self._simple_test('publication_date:[2000-01-01 TO 2001-01-01]', "HP4")

    def test_field_group(self):
        self._simple_test('title:(Phoenix AND Potter)', "HP5", num_match=2)

    def test_group(self):
        self._simple_test('(title:Phoenix AND ref:HP5)', "HP5", num_match=2)

    def test_unknown_operation(self):
        self._simple_test('(title:Phoenix ref:HP5)', "HP5", num_match=2)

    @classmethod
    def tearDownClass(cls):
        remove_book_index(cls.es_client)
