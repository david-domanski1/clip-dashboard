from clipdash import captions as cap

CAMP = {"creator": "LACY", "required_names": ["LACY"], "required_hashtags": ["#LACY"], "platforms": ["youtube", "tiktok", "instagram"]}
WORDS = [[0.0, 0.3, "Yo"], [0.3, 0.6, "this"], [0.6, 0.9, "is"], [0.9, 1.2, "crazy."], [2.0, 2.3, "Fuck"], [2.3, 2.5, "that."],
         [4.0, 4.2, "We"], [4.2, 4.5, "are"], [4.5, 4.8, "so"], [4.8, 5.0, "back!"], [6.0, 6.2, "ok"]]


def test_profanity_detection_and_censor():
    assert cap.has_profanity("what the FUCK") and not cap.has_profanity("what the heck")
    assert cap.censor("fuck that") == "f*** that"


def test_hook_candidates_are_clean_complete_sentences():
    c = cap.hook_candidates(WORDS)
    texts = [x["text"] for x in c]
    assert "Yo this is crazy." in texts and "We are so back!" in texts
    assert not any(cap.has_profanity(t) for t in texts)
    assert all(t[-1] in ".!?" for t in texts)
    assert "ok" not in " ".join(texts).lower().split()


def test_default_hook_falls_back_without_transcript():
    assert "LACY" in cap.default_hook("LACY", [], ["lucia", "lol"])


def test_required_name_and_tag_present_on_every_platform():
    out = cap.build_all(CAMP, "this is crazy.", ["Lucia"])
    for p, pt in out.items():
        full = pt["title"] + " " + pt["text"]
        assert "LACY" in full and "#LACY" in full, p
    assert out["youtube"]["title"].endswith("#Shorts") and len(out["youtube"]["title"]) <= 100


def test_no_tag_stuffing():
    tags = cap.build_hashtags(CAMP, ["a", "b", "c", "d", "e", "f"], "tiktok")
    assert tags[0] == "#LACY" and len(tags) <= 6 and len(set(t.lower() for t in tags)) == len(tags)


def test_moment_tags_skip_profanity_and_junk():
    assert cap.moment_tags(["fuck", "lucia", "the", "http://x"]) == ["#lucia"]
