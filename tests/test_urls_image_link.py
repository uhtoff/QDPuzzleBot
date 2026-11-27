from bot.utils.urls import image_link


def test_image_link_both():
    assert image_link("https://x.com/", "abc", 5, 9) == "https://x.com/hunts/abc?puzzle=5&image=9"


def test_image_link_only_puzzle():
    assert image_link("https://x.com", "abc", 5) == "https://x.com/hunts/abc?puzzle=5"


def test_image_link_only_media():
    assert image_link("https://x.com", "abc", media_id=9) == "https://x.com/hunts/abc?image=9"


def test_image_link_neither():
    assert image_link("https://x.com", "abc") == "https://x.com/hunts/abc"


def test_image_link_empty_base():
    assert image_link("", "abc", 5) is None
