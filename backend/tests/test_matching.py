from allocator.ingest.matching import CourseResolver, NameMatcher, extract_code

ROSTER = [
    ("nsk", "N Suresh Kumar Reddy", []),
    ("abhi", "Abhishek Sarkar", []),
    ("pardha", "Pardha Saradhi Gurugubelli Venkata", []),
    ("skd", "Satish Kumar Dubey", []),
    ("krc", "K. Ram Chandra Murthy", []),
    ("amol", "Amol Vuppuluri", []),
    ("brajesh", "Brajesh Kumar Panigrahi", []),
    ("jp", "Jayaprakash K S", []),
    ("jal", "Jalaiah Nandanavanam", []),
]


def test_names_with_titles_order_initials_and_typos():
    m = NameMatcher(ROSTER)
    assert m.match("Prof. NSK Reddy") == "nsk"
    assert m.match("Prof. Abhisek Sarkar") == "abhi"
    assert m.match("Pardha Saradhi GV") == "pardha"
    assert m.match("SATISH KUMAR DUBEY - IC") == "skd"
    assert m.match("Prof. VUPPULURI AMOL - IC") == "amol"
    assert m.match("Prof. Brajesh Panigrahi") == "brajesh"
    assert m.match("Jayaprakash KS") == "jp"
    assert m.match("Jalaiah N-IC") == "jal"


def test_aliases_and_rejections():
    m = NameMatcher(ROSTER, {"KRC": "K. Ram Chandra Murthy"})
    assert m.match("KRC") == "krc"
    assert m.match("Civil-RS") is None
    assert m.match("Someone Else Entirely") is None


def test_ambiguous_name_is_not_guessed():
    m = NameMatcher([("a", "Ravi Kumar", []), ("b", "Ravi Kumar Singh", [])])
    assert m.match("Ravi") is None


def test_course_codes():
    assert extract_code("ME F221 - MECHANISMS AND MACHINES") == "ME F221"
    assert extract_code("MF/ITEB F421 - SUPPLY CHAIN MANAGEMENT") == "MF F421"
    assert extract_code("BITSF241 SYSTEMS ENGG PRINCIPLES") == "BITS F241"
    assert extract_code("Robotics") is None


def test_course_resolution_by_alias_title_and_bare_code():
    r = CourseResolver(
        {"ME F219": "Manufacturing Processes", "ME G538": "TOYOTA PRODUCTION SYSTEMS"},
        {"MP": "ME F219"},
    )
    assert r.resolve("Mp lab") == "ME F219"
    assert r.resolve("Manufacturing Processes") == "ME F219"
    assert r.resolve("G538 - TOYOTA PRODUCTION SYSTEM") == "ME G538"
    assert r.resolve("n") is None
