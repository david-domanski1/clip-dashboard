from clipdash import rules


def clip(**over):
    base = {"duration": 65.0, "width": 1080, "height": 1920, "watermark": 0, "ai_text": 0,
            "post_text": {"youtube": {"title": "LACY loses it #Shorts", "text": "LACY loses it\n\n#LACY #gta6"},
                          "tiktok": {"title": "", "text": "LACY loses it #LACY #gta6"}}}
    base.update(over)
    return base


VIDEO = {"published_at": "2026-11-19T03:00:00+00:00"}


def failed(res):
    return [r["rule"] for r in res if not r["ok"]]


def test_good_clip_passes(campaign):
    assert failed(rules.validate_clip(clip(), campaign, VIDEO)) == []


def test_too_short_fails(campaign):
    assert "length" in failed(rules.validate_clip(clip(duration=59.0), campaign, VIDEO))


def test_wrong_resolution_fails(campaign):
    assert "format" in failed(rules.validate_clip(clip(width=1920, height=1080), campaign, VIDEO))


def test_missing_name_and_hashtag_fail(campaign):
    c = clip(post_text={"tiktok": {"title": "", "text": "funny moment #gta6"}, "youtube": {"title": "x", "text": "y"}})
    f = failed(rules.validate_clip(c, campaign, VIDEO))
    assert "name" in f and "hashtag" in f


def test_hashtag_must_be_whole_token(campaign):
    c = clip(post_text={"tiktok": {"title": "", "text": "LACY wins #LACYGTA"}, "youtube": {"title": "LACY", "text": "#LACYclips"}})
    assert "hashtag" in failed(rules.validate_clip(c, campaign, VIDEO))


def test_source_before_window_fails(campaign):
    assert "source_date" in failed(rules.validate_clip(clip(), campaign, {"published_at": "2026-11-10T00:00:00+00:00"}))


def test_watermark_forbidden(campaign):
    assert "no_watermark" in failed(rules.validate_clip(clip(watermark=1), campaign, VIDEO))


def test_profane_post_text_fails(campaign):
    c = clip(post_text={"tiktok": {"title": "", "text": "LACY what the fuck #LACY"}, "youtube": {"title": "LACY", "text": "#LACY"}})
    assert "clean_text" in failed(rules.validate_clip(c, campaign, VIDEO))


def test_platform_allowed(campaign):
    assert rules.platform_allowed(campaign, "tiktok") and not rules.platform_allowed(campaign, "x")


def test_checklist_complete(campaign):
    campaign["checklist"] = ["a", "b"]
    assert not rules.checklist_complete(campaign, {"0": True})
    assert rules.checklist_complete(campaign, {"0": True, "1": True})


def test_parse_rules_text_pool_campaign():
    text = ("$12,500 LACY GTA VI Marathon Pool Campaign. Submit within 30 minutes of posting. Captions must use #LACY. "
            "TikTok, Instagram Reels, YouTube Shorts and X. beginning November 18th, 2026. No logos of any kind. 750,000 TOTAL VIEW REQUIREMENT")
    f = rules.parse_rules_text(text)
    assert f["submit_minutes"] == 30 and f["pay_model"] == "pool" and f["pool_budget"] == 12500
    assert f["min_views"] == 750000 and "#LACY" in f["required_hashtags"] and f["source_start"] == "2026-11-18"
    assert set(f["platforms"]) >= {"tiktok", "instagram", "youtube"} and f["watermark_allowed"] == 0


def test_parse_rules_text_cpm_campaign():
    f = rules.parse_rules_text("Pays $1.50 per 1,000 views, max $300 per clip. Minimum 61 seconds.")
    assert f["pay_model"] == "cpm" and f["rate_per_1k"] == 1.5 and f["per_clip_cap"] == 300 and f["min_len"] == 61
