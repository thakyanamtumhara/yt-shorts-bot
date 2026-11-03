import contextlib
import copy
from datetime import datetime, timezone
import io
import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import retro_disclose as rd


MAIN_CHANNEL = "UCdgOMA7WO48MYimj6q6mvNQ"
MUTABLE = set(rd.MUTABLE_STATUS_FIELDS) | {"containsSyntheticMedia"}
NOW = datetime(2026, 11, 3, 4, 0, tzinfo=timezone.utc)
TODAY = "2026-11-03"


def video(vid, published, privacy="public", *, title=None, live=False, channel=rd.BOT_CHANNEL, **status):
    item = {"id": vid,
            "snippet": {"title": title or f"Short {vid}", "publishedAt": published, "channelId": channel},
            "status": {"uploadStatus": "processed", "privacyStatus": privacy, "license": "youtube",
                       "embeddable": True, "publicStatsViewable": True, "madeForKids": False,
                       "selfDeclaredMadeForKids": False, **status}}
    if live:
        item["liveStreamingDetails"] = {"actualStartTime": published}
    return item


def call(fn):
    return SimpleNamespace(execute=fn)


class FakeYouTube:
    """Uploads playlist newest first; videos.update replaces the status part like the real API."""

    def __init__(self, videos, channel=rd.BOT_CHANNEL, *, quota_after=None, mutate=None, returns_flag=False):
        self.store = {v["id"]: copy.deepcopy(v) for v in videos}
        self.playlist = [v["id"] for v in videos if v["snippet"]["channelId"] == rd.BOT_CHANNEL]
        self.channel = channel
        self.quota_after = quota_after
        self.mutate = mutate
        self.returns_flag = returns_flag
        self.updates = []

    def channels(self):
        return SimpleNamespace(list=lambda **kw: call(lambda: {"items": [
            {"id": self.channel, "contentDetails": {"relatedPlaylists": {"uploads": "UPLOADS"}}}]}))

    def playlistItems(self):
        def list_(*, part, playlistId, maxResults, pageToken=None):
            start = int(pageToken or 0)
            response = {"items": [{"contentDetails": {"videoId": vid}}
                                  for vid in self.playlist[start:start + maxResults]]}
            if start + maxResults < len(self.playlist):
                response["nextPageToken"] = str(start + maxResults)
            return call(lambda: response)
        return SimpleNamespace(list=list_)

    def videos(self):
        def list_(*, part, id):
            ids = id.split(",")
            assert len(ids) <= 50

            def run():
                items = []
                for vid in ids:
                    if vid in self.store:
                        item = copy.deepcopy(self.store[vid])
                        if not self.returns_flag:
                            item["status"].pop("containsSyntheticMedia", None)
                        items.append(item)
                return {"items": items}
            return call(run)

        def update(*, part, body):
            assert part == "status"

            def run():
                if self.quota_after is not None and len(self.updates) >= self.quota_after:
                    raise RuntimeError("<HttpError 403 ... 'reason': 'quotaExceeded'>")
                self.updates.append(copy.deepcopy(body))
                old = self.store[body["id"]]["status"]
                new = {key: value for key, value in old.items() if key not in MUTABLE}
                new.update(copy.deepcopy(body["status"]))
                if self.mutate:
                    new = self.mutate(body["id"], new)
                self.store[body["id"]]["status"] = new
                return {"id": body["id"], "status": copy.deepcopy(new)}
            return call(run)
        return SimpleNamespace(list=list_, update=update)


def run(youtube, state, **kwargs):
    kwargs.setdefault("now", NOW)
    kwargs.setdefault("sleep", lambda seconds: None)
    with contextlib.redirect_stdout(io.StringIO()):
        return rd.run(youtube, state, **kwargs)


def flagged_ids(youtube):
    return [body["id"] for body in youtube.updates]


class RetroDiscloseTest(unittest.TestCase):
    def catalog(self):
        return [  # uploads playlist order: newest first
            video("newShort001", "2026-11-02T03:30:00Z"),
            video("liveStream1", "2026-10-01T05:00:00Z", live=True),
            video("warehouseLv", "2026-09-01T05:00:00Z", title="Sale91 Warehouse Live — Office Camera"),
            video("midShort001", "2026-06-01T13:30:00Z"),
            video("doneShort01", "2026-05-01T13:30:00Z"),
            video("oldShort002", "2026-02-12T13:30:00Z"),
            video("realFootage", "2026-02-11T13:30:00Z"),
            video("oldShort001", "2026-02-10T13:30:00Z"),
        ]

    def state(self):
        return {"flagged": {"doneShort01": "seen on page 2026-11-03"},
                "skip": {"realFootage": "real footage, no AI scene"}}

    def test_oldest_first_once_each_then_nothing_left_means_no_writes(self):
        youtube, state = FakeYouTube(self.catalog()), self.state()
        first = run(youtube, state, max_updates=2)
        self.assertEqual(flagged_ids(youtube), ["oldShort001", "oldShort002"])
        self.assertEqual((first["flagged"], first["pending"], first["skipped_real"], first["skip_list"],
                          first["done_before"]), (2, 2, 2, 1, 1))
        self.assertEqual(state["flagged"]["oldShort001"], f"flagged {TODAY}")

        second = run(youtube, state, max_updates=2)
        self.assertEqual(flagged_ids(youtube)[2:], ["midShort001", "newShort001"])
        self.assertEqual((second["flagged"], second["pending"]), (2, 0))

        settled = copy.deepcopy(state)
        third = run(youtube, state, max_updates=120)
        self.assertEqual(len(youtube.updates), 4)
        self.assertEqual((third["flagged"], third["pending"], third["done_before"]), (0, 0, 5))
        self.assertEqual(state, settled)
        for untouched in ("liveStream1", "warehouseLv", "doneShort01", "realFootage"):
            self.assertNotIn(untouched, flagged_ids(youtube))

    def test_new_upload_the_normal_path_missed_is_flagged_after_its_first_day(self):
        youtube = FakeYouTube(self.catalog())
        state = self.state()
        run(youtube, state)
        youtube.store["brandNew001"] = video("brandNew001", "2026-11-03T03:30:00Z", "private",
                                             publishAt="2026-11-03T13:30:00Z")
        youtube.playlist.insert(0, "brandNew001")
        same_day = run(youtube, state)
        self.assertEqual((same_day["flagged"], same_day["too_new"]), (0, 1))
        self.assertNotIn("brandNew001", flagged_ids(youtube))
        youtube.store["brandNew001"] = video("brandNew001", "2026-11-03T13:30:00Z")  # went public at its slot
        self.assertEqual(run(youtube, state, now=NOW.replace(day=4, hour=5))["too_new"], 1)
        next_day = run(youtube, state, now=NOW.replace(day=4, hour=14))
        self.assertEqual(flagged_ids(youtube)[-1:], ["brandNew001"])
        self.assertEqual((next_day["flagged"], next_day["too_new"]), (1, 0))
        self.assertEqual(run(youtube, state, only_ids=["brandNew001"])["flagged"], 1)

    def test_update_resends_every_status_field_unchanged(self):
        scheduled = video("schedShort1", "2026-11-01T02:00:00Z", "private", publishAt="2026-11-05T13:30:00Z",
                          embeddable=False, publicStatsViewable=False, license="creativeCommon")
        youtube = FakeYouTube([scheduled])
        before = copy.deepcopy(scheduled["status"])
        result = run(youtube, {"flagged": {}, "skip": {}})
        self.assertEqual(result["flagged"], 1)
        sent = youtube.updates[0]["status"]
        self.assertEqual(sent, {**{k: before[k] for k in rd.MUTABLE_STATUS_FIELDS}, "containsSyntheticMedia": True})
        self.assertEqual(youtube.store["schedShort1"]["status"], {**before, "containsSyntheticMedia": True})

    def test_halts_when_the_readback_shows_another_field_moved(self):
        def lose_embedding(vid, status):
            return {**status, "embeddable": False} if vid == "oldShort002" else status
        youtube, state = FakeYouTube(self.catalog(), mutate=lose_embedding), self.state()
        result = run(youtube, state)
        self.assertIn("oldShort002", result["halt"])
        self.assertEqual(flagged_ids(youtube), ["oldShort001", "oldShort002"])
        self.assertIn("oldShort001", state["flagged"])
        self.assertNotIn("oldShort002", state["flagged"])

    def test_quota_stop_keeps_what_was_done(self):
        youtube, state = FakeYouTube(self.catalog(), quota_after=1), self.state()
        result = run(youtube, state)
        self.assertTrue(result["halted"])
        self.assertEqual(result["pending"], 3)
        self.assertEqual(sorted(state["flagged"]), ["doneShort01", "oldShort001"])

    def test_explicit_ids_reflag_exactly_those_but_never_a_skipped_one(self):
        youtube, state = FakeYouTube(self.catalog()), self.state()
        result = run(youtube, state, only_ids=["newShort001", "doneShort01", "realFootage"])
        self.assertEqual(flagged_ids(youtube), ["doneShort01", "newShort001"])
        self.assertEqual((result["flagged"], result["skip_list"]), (2, 1))
        self.assertEqual(state["flagged"]["doneShort01"], f"flagged {TODAY}")
        self.assertNotIn("realFootage", state["flagged"])

    def test_explicit_ids_from_another_channel_or_missing_write_nothing(self):
        catalog = self.catalog() + [video("mainVideo01", "2026-10-01T05:00:00Z", channel=MAIN_CHANNEL)]
        for ids in (["oldShort001", "mainVideo01"], ["oldShort001", "notThere001"]):
            youtube, state = FakeYouTube(catalog), self.state()
            with self.assertRaises(rd.Halt):
                run(youtube, state, only_ids=ids)
            self.assertEqual(youtube.updates, [])
            self.assertEqual(state, self.state())

    def test_refuses_a_token_for_any_other_channel(self):
        youtube = FakeYouTube(self.catalog(), channel=MAIN_CHANNEL)
        with self.assertRaises(rd.Halt):
            run(youtube, self.state())
        self.assertEqual(youtube.updates, [])

    def test_api_readback_true_is_recorded_without_a_write(self):
        catalog = [video("apiFlagged1", "2026-02-01T05:00:00Z", containsSyntheticMedia=True)]
        youtube, state = FakeYouTube(catalog, returns_flag=True), {"flagged": {}, "skip": {}}
        result = run(youtube, state)
        self.assertEqual((youtube.updates, result["api_true"]), ([], 1))
        self.assertEqual(state["flagged"], {"apiFlagged1": f"api {TODAY}"})

    def test_dry_run_writes_nothing(self):
        youtube, state = FakeYouTube(self.catalog()), self.state()
        result = run(youtube, state, dry_run=True, max_updates=3)
        self.assertEqual(youtube.updates, [])
        self.assertEqual((result["flagged"], result["pending"]), (3, 1))
        self.assertEqual(state, self.state())

    def test_inconsistent_schedule_is_left_alone(self):
        odd = video("oddSchedule", "2026-02-01T05:00:00Z", "public", publishAt="2026-11-05T13:30:00Z")
        youtube = FakeYouTube([odd])
        result = run(youtube, {"flagged": {}, "skip": {}})
        self.assertEqual((youtube.updates, result["errors"]), ([], 1))

    def test_ids_and_state_file(self):
        self.assertEqual(rd.parse_ids(" _RjEtLF7hKI, ficzcfsYeBs\n_RjEtLF7hKI "), ["_RjEtLF7hKI", "ficzcfsYeBs"])
        self.assertEqual(rd.parse_ids(""), [])
        with self.assertRaises(ValueError):
            rd.parse_ids("_RjEtLF7hKI, https://youtu.be/x")
        with TemporaryDirectory() as folder:
            path = Path(folder) / "state.json"
            self.assertEqual(rd.load_state(path), {"flagged": {}, "skip": {}})
            rd.save_state({"note": "n", "flagged": {"b": "1", "a": "2"}, "skip": {}}, path)
            text = path.read_text(encoding="utf-8")
            self.assertTrue(text.endswith("}\n"))
            self.assertLess(text.index('"a"'), text.index('"b"'))
            self.assertEqual(rd.load_state(path)["note"], "n")

    def test_committed_state_is_well_formed(self):
        state = json.loads(rd.STATE_FILE.read_text(encoding="utf-8"))
        ids = list(state["flagged"]) + list(state["skip"])
        self.assertFalse(set(state["flagged"]) & set(state["skip"]))
        self.assertTrue(all(len(vid) == 11 and rd.parse_ids(vid) == [vid] for vid in ids))
        self.assertTrue(all(isinstance(reason, str) and reason for reason in state["skip"].values()))


if __name__ == "__main__":
    unittest.main()
