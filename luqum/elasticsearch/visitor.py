from luqum.elasticsearch.tree import ElasticSearchItemFactory
from luqum.tree import (
    OrOperation, AndOperation, UnknownOperation, SearchField)
from luqum.tree import Word  # noqa: F401
from .tree import (
    EMust, EMustNot, EShould, EWord, AbstractEItem, EPhrase, ERange,
    ENested)
from ..utils import LuceneTreeVisitorV2
from .exceptions import OrAndAndOnSameLevel


class ElasticsearchQueryBuilder(LuceneTreeVisitorV2):
    """
    Query builder to convert a Tree in an Elasticsearch query dsl (json)
    """

    SHOULD = 'should'
    MUST = 'must'

    def __init__(self, default_operator=SHOULD, default_field='text',
                 not_analyzed_fields=None, nested_fields=None):
        """
        :param default_operator: to replace blank operator (MUST or SHOULD)
        :param default_field: to search
        :param not_analyzed_fields: field that are not analyzed in ES
        :param nested_fields: field that are nested in ES
        """

        if not_analyzed_fields:
            self._not_analyzed_fields = not_analyzed_fields
        else:
            self._not_analyzed_fields = []

        self._nested_fields = nested_fields if nested_fields else []

        self.default_operator = default_operator
        self.default_field = default_field
        self.es_item_factory = ElasticSearchItemFactory(
            no_analyze=self._not_analyzed_fields,
            nested_fields=self._nested_fields
        )

    def simplify_if_same(self, children, current_node):
        """
        If two same operation are nested, then simplify
        Should be use only with should and must operations because Not(Not(x))
        can't be simplified as Not(x)
        :param children:
        :param current_node:
        :return:
        """
        for child in children:
            if type(child) is type(current_node):
                yield from self.simplify_if_same(child.children, current_node)
            else:
                yield child

    def _get_operator_extract(self, binary_operation, delta=8):
        """
        Return an extract around the operator
        :param binary_operation: operator to extract
        :param delta: nb of characters to extract before and after the operator
        :return: str

        >>> operation = OrOperation(Word('Python'), Word('Monty'))
        >>> builder = ElasticsearchQueryBuilder()
        >>> builder._get_operator_extract(operation, 3)
        'hon OR Mon'
        """
        node_str = str(binary_operation)
        child_str_1 = str(binary_operation.children[0])
        child_str_2 = str(binary_operation.children[1])
        middle_length = len(node_str) - len(child_str_1) - len(child_str_2)
        position = node_str.find(child_str_2)
        if position - middle_length - delta >= 0:
            start = position - middle_length - delta
        else:
            start = 0
        end = position + delta
        return node_str[start:end]

    def _is_must(self, operation):
        """
        Returns True if the node is a AndOperation or an UnknownOperation when
        the default operator is MUST
        :param node: to check
        :return: Boolean

        >>> ElasticsearchQueryBuilder(
        ...     default_operator=ElasticsearchQueryBuilder.MUST
        ... )._is_must(AndOperation(Word('Monty'), Word('Python')))
        True
        """
        return (
            isinstance(operation, AndOperation) or
            isinstance(operation, UnknownOperation) and
            self.default_operator == ElasticsearchQueryBuilder.MUST
        )

    def _is_should(self, operation):
        """
        Returns True if the node is a OrOperation or an UnknownOperation when
        the default operator is SHOULD
        >>> ElasticsearchQueryBuilder(
        ...     default_operator=ElasticsearchQueryBuilder.MUST
        ... )._is_should(OrOperation(Word('Monty'), Word('Python')))
        True
        """
        return (
            isinstance(operation, OrOperation) or
            isinstance(operation, UnknownOperation) and
            self.default_operator == ElasticsearchQueryBuilder.SHOULD
        )

    def _yield_nested_children(self, parent, children):
        """
        Raise if a OR (should) is in a AND (must) without being in parenthesis

        >>> builder = ElasticsearchQueryBuilder()
        >>> op = OrOperation(Word('yo'), OrOperation(Word('lo'), Word('py')))
        >>> list(builder._yield_nested_children(op, op.children))
        [Word('yo'), OrOperation(Word('lo'), Word('py'))]


        >>> op = OrOperation(Word('yo'), AndOperation(Word('lo'), Word('py')))
        >>> list(builder._yield_nested_children(op, op.children))
        Traceback (most recent call last):
            ...
        luqum.elasticsearch.exceptions.OrAndAndOnSameLevel: lo AND py
        """

        for child in children:
            if (self._is_should(parent) and self._is_must(child) or
               self._is_must(parent) and self._is_should(child)):
                raise OrAndAndOnSameLevel(
                    self._get_operator_extract(child)
                )
            else:
                yield child

    def _binary_operation(self, cls, node, parents):
        children = self.simplify_if_same(node.children, node)
        children = self._yield_nested_children(node, children)
        items = [self.visit(child, parents + [node]) for child in children]
        return self.es_item_factory.build(cls, items)

    def _must_operation(self, *args, **kwargs):
        return self._binary_operation(EMust, *args, **kwargs)

    def _should_operation(self, *args, **kwargs):
        return self._binary_operation(EShould, *args, **kwargs)

    def visit_and_operation(self, *args, **kwargs):
        return self._must_operation(*args, **kwargs)

    def visit_or_operation(self, *args, **kwargs):
        return self._should_operation(*args, **kwargs)

    def visit_word(self, node, parents):
        return self.es_item_factory.build(
            EWord,
            q=node.value,
            default_field=self.default_field
        )

    def _set_search_field_in_all_children(self, enode, field_name):
        """
        Recursive method to set the field name even in nested enode.
        For instance in this case: field:(spam OR eggs OR (monthy AND python))
        """
        if isinstance(enode, AbstractEItem):
            enode.fields.insert(0, field_name)
        else:
            for item in enode.items:
                self._set_search_field_in_all_children(item, field_name)

    def _is_nested(self, node):
        nested_items = []
        for child in node.children:
            if isinstance(child, SearchField):
                nested_items.append(True)
            else:
                nested_items.append(self._is_nested(child))
        return any(nested_items)

    def visit_search_field(self, node, parents):
        enode = self.visit(node.children[0], parents + [node])
        if self._is_nested(node):
            enode = self.es_item_factory.build(
                ENested, nested_path=node.name, items=enode)
            self._set_search_field_in_all_children(enode.items, node.name)
        else:
            self._set_search_field_in_all_children(enode, node.name)

        return enode

    def visit_not(self, node, parents):
        items = [self.visit(n, parents + [node])
                 for n in self.simplify_if_same(node.children, node)]
        return self.es_item_factory.build(EMustNot, items)

    def visit_prohibit(self, *args, **kwargs):
        return self.visit_not(*args, **kwargs)

    def visit_plus(self, *args, **kwargs):
        return self._must_operation(*args, **kwargs)

    def visit_unknown_operation(self, *args, **kwargs):
        if self.default_operator == self.SHOULD:
            return self._should_operation(*args, **kwargs)
        elif self.default_operator == self.MUST:
            return self._must_operation(*args, **kwargs)

    def visit_boost(self, node, parents):
        eword = self.visit(node.children[0], parents + [node])
        eword.boost = float(node.force)
        return eword

    def visit_fuzzy(self, node, parents):
        eword = self.visit(node.term, parents + [node])
        eword.fuzziness = float(node.degree)
        return eword

    def visit_proximity(self, node, parents):
        ephrase = self.visit(node.term, parents + [node])
        ephrase.slop = float(node.degree)
        return ephrase

    def visit_phrase(self, node, parents):
        return self.es_item_factory.build(
            EPhrase,
            phrase=node.value,
            default_field=self.default_field
        )

    def visit_range(self, node, parents):
        kwargs = {
            'gte' if node.include_low else 'gt': node.low.value,
            'lte' if node.include_high else 'lt': node.high.value,
        }
        return self.es_item_factory.build(ERange, **kwargs)

    def visit_group(self, node, parents):
        return self.visit(node.expr, parents + [node])

    def visit_field_group(self, node, parents):
        fields = self.visit(node.expr, parents + [node])
        return fields