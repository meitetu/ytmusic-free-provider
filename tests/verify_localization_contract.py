"""Run with the real server image: locale keys, cache hits and playback bypass."""

import asyncio
from types import SimpleNamespace

from music_assistant.controllers.cache import BYPASS_CACHE
from music_assistant.providers.ytmusic_free import (
    CONF_METADATA_LANGUAGE,
    HOME_FEED_ROW_LIMIT,
    YoutubeMusicFreeProvider,
    _build_config_entries,
    use_metadata_cache,
)


class Cache:
    def __init__(self):
        self.entries = {}
        self.keys = []

    async def get_with_freshness(self, key, *, provider, checksum, **kwargs):
        self.keys.append(key)
        if kwargs["allow_bypass"] and BYPASS_CACHE.get():
            return None, False, False
        cache_key = (provider, key, checksum)
        return self.entries.get(cache_key), True, cache_key in self.entries

    async def set(self, *, key, provider, checksum, data, **kwargs):
        self.entries[(provider, key, checksum)] = data


class Probe:
    domain = "ytmusic_free"
    instance_id = "locale-probe"
    _metadata_locale = "ja-JP"
    calls = 0

    @use_metadata_cache(60, allow_expired_cache=True, cache_checksum="parser-version")
    async def metadata(self, item_id: str) -> dict[str, str]:
        self.calls += 1
        return {"name": "日本語" if self._metadata_locale == "ja-JP" else "English"}


class HomeProbe(Probe):
    """Exercise the provider's actual home-feed method with MA's real cache."""

    _get_home_feed = YoutubeMusicFreeProvider._get_home_feed
    account = "brand:0"

    def get_home(self, *, limit):
        assert limit == HOME_FEED_ROW_LIMIT
        self.calls += 1
        return [{"title": "おすすめ" if self._metadata_locale == "ja-JP" else "Recommended",
                 "account": self.account}]


async def main():
    entries = _build_config_entries()
    language = next(entry for entry in entries if entry.key == CONF_METADATA_LANGUAGE)
    assert language.default_value == "ja-JP"
    assert {option.value for option in language.options} == {"ja-JP", "en-US"}
    cache = Cache()
    # A stale pre-localization entry must never be served by the new keys.
    cache.entries[("locale-probe", "metadata.same-id", "parser-version")] = {"name": "old English"}
    tasks = []

    def create_task(coro, **kwargs):
        task = asyncio.create_task(coro)
        tasks.append(task)
        return task

    probe = Probe()
    probe.mass = SimpleNamespace(cache=cache, create_task=create_task)
    assert (await probe.metadata("same-id"))["name"] == "日本語"
    await asyncio.gather(*tasks)
    assert (await probe.metadata("same-id"))["name"] == "日本語"
    assert probe.calls == 1, "same-locale reads must still use MA's cache"
    probe._metadata_locale = "en-US"
    assert (await probe.metadata("same-id"))["name"] == "English"
    await asyncio.gather(*tasks)
    assert probe.calls == 2, "switching locale must fetch instead of serving Japanese"
    assert any("ja-JP" in key for key in cache.keys)
    assert any("en-US" in key for key in cache.keys)
    other = Probe()
    other.mass = probe.mass
    other.instance_id = "other-instance"
    assert (await other.metadata("same-id"))["name"] == "日本語"
    assert other.calls == 1, "two configured instances must not share cache entries"
    token = BYPASS_CACHE.set(True)
    try:
        assert (await probe.metadata("same-id"))["name"] == "English"
        assert probe.calls == 3, "playlist playback cache bypass must survive"
    finally:
        BYPASS_CACHE.reset(token)
    await asyncio.gather(*tasks)
    home = HomeProbe()
    home.instance_id = "home-locale-probe"
    home.mass = probe.mass
    home._ytmusic = SimpleNamespace(get_home=home.get_home)
    cache.entries[(home.instance_id, "_get_home_feed.brand:0", str(HOME_FEED_ROW_LIMIT))] = [
        {"title": "old English"}
    ]
    assert (await home._get_home_feed(home.account))[0]["title"] == "おすすめ"
    await asyncio.gather(*tasks)
    assert (await home._get_home_feed(home.account))[0]["title"] == "おすすめ"
    assert home.calls == 1
    home._metadata_locale = "en-US"
    assert (await home._get_home_feed(home.account))[0]["title"] == "Recommended"
    await asyncio.gather(*tasks)
    assert home.calls == 2, "home-feed locale changes must not reuse Japanese shelves"
    home._metadata_locale = "ja-JP"
    assert (await home._get_home_feed(home.account))[0]["title"] == "おすすめ"
    assert home.calls == 2, "returning to a cached locale should reuse its own feed"
    home.account = "brand:1"
    assert (await home._get_home_feed(home.account))[0]["account"] == "brand:1"
    await asyncio.gather(*tasks)
    assert home.calls == 3, "switching account must still fetch a separate home feed"
    print("real provider home-feed locale/account cache contract ok")
    print("real server localization cache and playback-bypass contract ok")


if __name__ == "__main__":
    asyncio.run(main())
