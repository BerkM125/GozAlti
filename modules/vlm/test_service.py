#!/usr/bin/env python3
"""Tests for the vlm service. Stdlib unittest — no pytest, no venv.

Two tiers:

  ./test_service.py            unit only. Pure functions: contract validation, enum
                               discipline, the schema-miss degradation path. No GPU, no
                               models, runs anywhere in ~50 ms.

  ./test_service.py --live     also hits a running service on :8040 with a real
                               FrameRecord and asserts the response satisfies §6.2.
                               Requires the service up and a frame on disk.

Run the unit tier in CI and before every push; run --live on the box after deploying.
"""
import argparse, json, sys, time, unittest, urllib.error, urllib.request, os
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

# service.py imports lab/prompts at module load; keep that working from any cwd
import service


class TestFlagEnum(unittest.TestCase):
    """SPEC §6.2 says flags are a closed enum defined in modules/vlm/SPEC.md."""

    def test_enum_is_closed_and_unique(self):
        self.assertEqual(len(service.FLAGS), len(set(service.FLAGS)), "duplicate flags")
        self.assertEqual(service.FLAGSET, set(service.FLAGS))

    def test_every_mapped_flag_is_in_the_enum(self):
        for f in service.WALKWAY_FLAG.values():
            self.assertIn(f, service.FLAGSET, f"walkway maps to unknown flag {f}")
        for f in service.EVENT_FLAG.values():
            self.assertIn(f, service.FLAGSET, f"event maps to unknown flag {f}")

    def test_no_verdict_flags(self):
        """The module never emits a judgement about danger or about people."""
        banned = ("safe", "unsafe", "danger", "risk", "suspicious", "threat", "loiter",
                  "homeless", "drug")
        for f in service.FLAGS:
            for b in banned:
                self.assertNotIn(b, f.lower(), f"flag '{f}' asserts a verdict")


class TestValidate(unittest.TestCase):
    def good(self):
        return {"camera_id": "CMR-0176", "frame_ts": "2026-08-16T05:00:00Z",
                "read_at": "2026-08-16T05:00:06Z", "model": "torchvision/fasterrcnn+qwen3-vl:8b",
                "people_count": 2, "flags": ["construction"], "caption": "two people crossing",
                "detections": [{"label": "person", "cx": 0.4, "cy": 0.6, "conf": 0.9},
                               {"label": "person", "cx": 0.1, "cy": 0.2, "conf": 0.7}]}

    def test_valid_passes(self):
        self.assertEqual(service.validate(self.good()), [])

    def test_missing_required_field_fails(self):
        for k in ("camera_id", "frame_ts", "read_at", "model", "people_count",
                  "detections", "flags", "caption"):
            o = self.good(); o.pop(k)
            self.assertTrue(any(k in p for p in service.validate(o)), f"{k} not caught")

    def test_unknown_flag_rejected(self):
        o = self.good(); o["flags"] = ["looks_sketchy"]
        self.assertTrue(any("not in enum" in p for p in service.validate(o)))

    def test_coordinates_must_be_normalised(self):
        for bad in (1.4, -0.1, 640):
            o = self.good(); o["detections"][0]["cx"] = bad
            self.assertTrue(any("cx" in p for p in service.validate(o)),
                            f"cx={bad} should be rejected; §6.2 requires [0,1]")

    def test_detection_missing_conf_rejected(self):
        o = self.good(); del o["detections"][0]["conf"]
        self.assertTrue(any("conf" in p for p in service.validate(o)))

    def test_people_count_must_be_int(self):
        o = self.good(); o["people_count"] = "2"
        self.assertTrue(any("people_count" in p for p in service.validate(o)))

    def test_empty_observation_is_valid(self):
        o = self.good(); o["people_count"] = 0; o["detections"] = []; o["flags"] = ["no_people"]
        self.assertEqual(service.validate(o), [])


class TestAsList(unittest.TestCase):
    """Cosmos-Reason1 returns enum lists as bare strings; trusting the schema iterates
    them character by character. Regression test for a bug we actually shipped."""

    def test_bare_string_is_not_iterated_as_characters(self):
        self.assertEqual(service.as_list("construction"), ["construction"])

    def test_comma_string_splits(self):
        self.assertEqual(service.as_list("construction, queue"), ["construction", "queue"])

    def test_list_passes_through(self):
        self.assertEqual(service.as_list(["a", "b"]), ["a", "b"])

    def test_junk_is_dropped(self):
        self.assertEqual(service.as_list(None), [])
        self.assertEqual(service.as_list(17), [])
        self.assertEqual(service.as_list([1, "ok", None]), ["ok"])


class TestParseJson(unittest.TestCase):
    def test_clean(self):
        self.assertEqual(service.parse_json('{"a":1}'), {"a": 1})

    def test_wrapped_in_prose(self):
        self.assertEqual(service.parse_json('here you go: {"a":1} done'), {"a": 1})

    def test_trailing_comma_repaired(self):
        self.assertEqual(service.parse_json('{"a":1,}'), {"a": 1})

    def test_unparseable_returns_none(self):
        self.assertIsNone(service.parse_json("no json here"))
        self.assertIsNone(service.parse_json(""))


class TestDeadCamera(unittest.TestCase):
    """A stale frame must never reach a model or receive an invented caption."""

    def test_stale_frame_short_circuits(self):
        obs = service.observe({"camera_id": "CMR-0001", "captured_at": "2026-08-16T05:00:00Z",
                               "path": "/does/not/exist.jpg", "stale": True})
        self.assertEqual(obs["flags"], ["camera_dead"])
        self.assertEqual(obs["detections"], [])
        self.assertEqual(obs["people_count"], 0)
        self.assertEqual(obs["model"], "none")
        self.assertEqual(service.validate(obs), [])

    def test_missing_frame_raises(self):
        with self.assertRaises(FileNotFoundError):
            service.observe({"camera_id": "X", "path": "/nope/missing.jpg", "stale": False})


try:
    import cv2 as _cv2
    HAVE_CV2 = True
except Exception:
    HAVE_CV2 = False


@unittest.skipUnless(HAVE_CV2, "cv2 lives in the vLLM container, not in system python; "
                               "the live tier covers illumination end to end")
class TestIllumination(unittest.TestCase):
    """Lighting is measured, not asked of the model. safe-walk caught the VLM calling a
    2 a.m. street 'daylight'; a histogram cannot do that."""

    def frames(self, pat):
        return sorted((HERE / "lab" / "samples").glob(pat))

    def test_night_frames_are_darker_than_day_frames(self):
        night = [service.illumination(f) for f in self.frames("night__*.jpg")]
        day = [service.illumination(f) for f in self.frames("crowd__*.jpg")]
        night = [x for x in night if x]; day = [x for x in day if x]
        if not night or not day:
            self.skipTest("need both night and daylight samples")
        nm = sum(x["mean_luma"] for x in night) / len(night)
        dm = sum(x["mean_luma"] for x in day) / len(day)
        self.assertLess(nm, dm, f"night mean luma {nm:.1f} should be below day {dm:.1f}")

    def test_shape_and_ranges(self):
        f = self.frames("*.jpg")[0]
        lum = service.illumination(f)
        self.assertIsNotNone(lum)
        self.assertIn(lum["bucket"], ("dark", "dim", "lit"))
        self.assertGreaterEqual(lum["mean_luma"], 0.0)
        self.assertLessEqual(lum["mean_luma"], 255.0)
        self.assertGreaterEqual(lum["dark_fraction"], 0.0)
        self.assertLessEqual(lum["dark_fraction"], 1.0)

    def test_missing_file_returns_none_not_raise(self):
        """Never raise out of the illumination path: a bad frame must not sink a read."""
        self.assertIsNone(service.illumination(Path("/nope/missing.jpg")))


class TestLive(unittest.TestCase):
    """Only run with --live: requires the service up and a real frame."""
    BASE = os.environ.get("VLM_URL", "http://127.0.0.1:8040")

    def get(self, path):
        with urllib.request.urlopen(f"{self.BASE}{path}", timeout=10) as r:
            return json.load(r)

    def post(self, path, payload, timeout=300):
        req = urllib.request.Request(f"{self.BASE}{path}", data=json.dumps(payload).encode(),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)

    def test_health(self):
        h = self.get("/health")
        self.assertTrue(h["ok"])
        self.assertEqual(h["module"], "vlm")
        self.assertEqual(h["port"], 8040)

    def test_flags_endpoint_matches_module(self):
        self.assertEqual(set(self.get("/flags")["flags"]), service.FLAGSET)

    def test_read_returns_valid_observation(self):
        frames = sorted((HERE / "lab" / "samples").glob("*.jpg"))
        self.assertTrue(frames, "no sample frames on disk")
        f = frames[0]
        obs = self.post("/read", {
            "camera_id": f.name.split("__")[1] if f.name.count("__") >= 2 else "TEST",
            "captured_at": "2026-08-16T05:00:00Z", "lat": 47.61, "lon": -122.33,
            "kind": "frame", "path": str(f), "source": "sdot-snapshot", "stale": False})
        problems = service.validate(obs)
        self.assertEqual(problems, [], f"response violates §6.2: {problems}")
        self.assertIsInstance(obs["people_count"], int)
        self.assertEqual(obs["people_count"], len(obs["detections"]))
        for d in obs["detections"]:
            self.assertGreaterEqual(d["cx"], 0.0); self.assertLessEqual(d["cx"], 1.0)

    def test_read_stale_does_not_call_models(self):
        obs = self.post("/read", {"camera_id": "CMR-DEAD", "captured_at": "2026-08-16T05:00:00Z",
                                  "kind": "frame", "path": "whatever.jpg",
                                  "source": "sdot-snapshot", "stale": True})
        self.assertIn("camera_dead", obs["flags"])
        self.assertEqual(obs["model"], "none")

    def test_batch_rejects_oversized(self):
        """A runaway caller must not be able to queue unbounded GPU work."""
        big = [{"camera_id": f"T{i}", "captured_at": "2026-08-16T05:00:00Z", "kind": "frame",
                "path": "x.jpg", "source": "sdot-snapshot", "stale": True}
               for i in range(service.MAX_BATCH + 1)]
        try:
            self.post("/read_batch", {"frames": big}, timeout=30)
            self.fail("oversized batch should be rejected with 413")
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 413)

    def test_batch_isolates_a_bad_frame(self):
        """One missing file must not sink the whole sweep."""
        good = sorted((HERE / "lab" / "samples").glob("*.jpg"))[0]
        out = self.post("/read_batch", {"frames": [
            {"camera_id": "GOOD", "captured_at": "2026-08-16T05:00:00Z", "kind": "frame",
             "path": str(good), "source": "sdot-snapshot", "stale": False},
            {"camera_id": "MISSING", "captured_at": "2026-08-16T05:00:00Z", "kind": "frame",
             "path": "/nope/gone.jpg", "source": "sdot-snapshot", "stale": False}]})
        obs = out["observations"]
        self.assertEqual(len(obs), 2, "order and length must be preserved")
        self.assertEqual(obs[0]["camera_id"], "GOOD")
        self.assertNotIn("error", obs[0], "good frame should still succeed")
        self.assertEqual(obs[1]["camera_id"], "MISSING")
        self.assertIn("error", obs[1])
        self.assertEqual(out["ok"], 1)
        self.assertEqual(out["failed"], 1)

    def test_batch_concurrency_beats_serial(self):
        """The whole point of the batch endpoint: it must be faster per frame than /read."""
        frames = sorted((HERE / "lab" / "samples").glob("*.jpg"))[:4]
        if len(frames) < 4:
            self.skipTest("need 4 sample frames")
        # Compare the SAME frames both ways. An earlier version timed one cheap frame
        # serially against four mixed frames batched and "failed" on frame difficulty
        # rather than on concurrency.
        def recs(tag):
            return [{"camera_id": f"{tag}{i}", "captured_at": "2026-08-16T05:00:00Z",
                     "kind": "frame", "path": str(f), "source": "sdot-snapshot",
                     "stale": False} for i, f in enumerate(frames)]

        def clear():
            # Both halves must measure COMPUTE. On a warm cache a batch returns in ~0 ms,
            # which made this test order-dependent and divided by zero on a second run.
            urllib.request.urlopen(
                urllib.request.Request(f"{self.BASE}/cache", method="DELETE"), timeout=10).read()

        clear()
        t0 = time.time()
        for r in recs("S"):
            self.post("/read", r, timeout=300)
        serial_per_frame = (time.time() - t0) / len(frames)
        clear()
        out = self.post("/read_batch", {"frames": recs("B")}, timeout=600)
        self.assertGreater(out["per_frame_s"], 0.0,
                           "batch returned instantly — cache was not cleared, "
                           "so this measured nothing")
        self.assertEqual(out["concurrency"], service.CONCURRENCY)
        speedup = serial_per_frame / out["per_frame_s"]
        print(f"\n    serial {serial_per_frame:.2f}s/frame vs batch "
              f"{out['per_frame_s']:.2f}s/frame at concurrency {out['concurrency']} "
              f"({speedup:.2f}x)")
        self.assertLess(out["per_frame_s"], serial_per_frame,
                        "batch should be faster per frame than one-at-a-time")

    def test_illumination_is_in_the_response(self):
        f = sorted((HERE / "lab" / "samples").glob("night__*.jpg"))[0]
        obs = self.post("/read", {"camera_id": "NIGHT-TEST",
                                  "captured_at": "2026-08-16T07:00:00Z", "kind": "frame",
                                  "path": str(f), "source": "sdot-snapshot", "stale": False},
                        timeout=300)
        lum = obs.get("_ext", {}).get("illumination")
        self.assertIsNotNone(lum, "illumination missing from the observation")
        print(f"\n    {f.name[:34]}: luma {lum['mean_luma']} bucket {lum['bucket']} "
              f"dark_frac {lum['dark_fraction']} -> flags {obs['flags']}")

    def test_accepts_media_ingest_envelope(self):
        """media-ingest pushes {frame_record, image_b64, prior_observations}. That shape
        must work, and image_b64 must remove the dependency on paths this containerised
        service may not be able to resolve."""
        import base64
        f = sorted((HERE / "lab" / "samples").glob("*.jpg"))[0]
        obs = self.post("/read", {
            "frame_record": {"camera_id": "ENVELOPE-TEST",
                             "captured_at": "2026-08-16T08:00:00Z", "kind": "frame",
                             "path": "/a/path/this/service/cannot/see.jpg",
                             "source": "sdot-snapshot", "stale": False},
            "image_b64": base64.b64encode(f.read_bytes()).decode(),
            "prior_observations": [
                {"frame_ts": "2026-08-16T07:45:00Z", "flags": ["construction"],
                 "caption": "cones along the north kerb"}]},
            timeout=300)
        self.assertEqual(service.validate(obs), [], "enveloped read violated §6.2")
        self.assertEqual(obs["camera_id"], "ENVELOPE-TEST")
        self.assertIsInstance(obs["people_count"], int)

    def test_bare_framerecord_still_works(self):
        """The envelope must not break the plain §6.1 shape."""
        f = sorted((HERE / "lab" / "samples").glob("*.jpg"))[0]
        obs = self.post("/read", {"camera_id": "BARE-TEST",
                                  "captured_at": "2026-08-16T08:00:00Z", "kind": "frame",
                                  "path": str(f), "source": "sdot-snapshot",
                                  "stale": False}, timeout=300)
        self.assertEqual(service.validate(obs), [])

    def test_cache_hit_is_fast_and_identical(self):
        """Two users routing past the same camera must not both pay for the GPU."""
        f = sorted((HERE / "lab" / "samples").glob("*.jpg"))[0]
        rec = {"camera_id": "CACHE-TEST", "captured_at": "2026-08-16T09:00:00Z",
               "kind": "frame", "path": str(f), "source": "sdot-snapshot", "stale": False}
        first = self.post("/read", rec, timeout=300)
        t0 = time.time(); second = self.post("/read", rec, timeout=60)
        hit_s = time.time() - t0
        self.assertEqual(first["detections"], second["detections"], "cached read differs")
        self.assertEqual(first["caption"], second["caption"])
        self.assertTrue(second.get("_ext", {}).get("cached"), "second read should be cached")
        self.assertLess(hit_s, 0.5, f"cache hit took {hit_s:.2f}s; should be instant")
        print(f"\n    cache hit in {hit_s*1000:.0f} ms")

    def test_batch(self):
        frames = sorted((HERE / "lab" / "samples").glob("*.jpg"))[:2]
        out = self.post("/read_batch", {"frames": [
            {"camera_id": f"T{i}", "captured_at": "2026-08-16T05:00:00Z", "kind": "frame",
             "path": str(f), "source": "sdot-snapshot", "stale": False}
            for i, f in enumerate(frames)]})
        self.assertEqual(len(out["observations"]), len(frames))
        for o in out["observations"]:
            self.assertNotIn("error", o, f"batch item failed: {o.get('error')}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true", help="also run tests against :8040")
    a, rest = ap.parse_known_args()
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    for cls in (TestFlagEnum, TestValidate, TestAsList, TestParseJson, TestDeadCamera,
                TestIllumination):
        suite.addTests(loader.loadTestsFromTestCase(cls))
    if a.live:
        suite.addTests(loader.loadTestsFromTestCase(TestLive))
    else:
        print("(unit only — pass --live to also test a running service on :8040)\n")
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)


if __name__ == "__main__":
    main()
