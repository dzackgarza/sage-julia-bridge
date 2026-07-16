# Bridge behavior under Sage-preparsed literal semantics: in a .sage file,
# integer literals are Sage Integers and quotients are exact rationals, so
# these exercise the input codec on the values interactive Sage users
# actually produce (the .py tests only send explicit ZZ()/QQ() wrappers).

from sage_julia_bridge import Julia


def test_preparsed_literals_roundtrip():
    with Julia() as bridge:
        bridge.set("x", 5)
        assert bridge.get_sage("x") == 5
        bridge.set("q", 1/3)
        assert bridge.get_sage("q") == 1/3
        assert bridge.call("+", 2/7, 3/7) == 5/7
