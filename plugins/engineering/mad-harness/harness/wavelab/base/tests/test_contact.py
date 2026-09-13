from wavelab.contact import clean_contact


def test_clean_contact_returns_a_copy_not_the_original():
    raw = {"email": "A@B.com", "phone": "+61 400 000 000"}
    out = clean_contact(raw)
    assert out == raw
    assert out is not raw, "callers must not be able to mutate the input through the result"
