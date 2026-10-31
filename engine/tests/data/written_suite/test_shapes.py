from shapes import Colour, Empty, Frozen, Labelled, Moved, Pair, Point, Row, Span, snippet


def test_dataclass():
    assert repr(Point(1, 2)) == "Point(x=1, y=2)"


def test_frozen_slots():
    assert Frozen("a") == Frozen("a")


def test_empty():
    assert Empty() == Empty()


def test_named_tuple():
    assert Pair("a", "b").key == "a"


def test_functional_namedtuple():
    assert Row(1, 2).b == 2


def test_record():
    assert Span(0, 4).end == 4


def test_dataclass_subclass():
    assert Labelled(1, 2, "p").label == "p"


def test_plain_subclass():
    assert Moved(1, 2).y == 2


def test_enum():
    assert Colour(1) is Colour.RED


def test_exec():
    assert snippet() == 4
