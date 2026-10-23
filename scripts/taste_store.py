#!/usr/bin/env python3
"""Small local candidate/feedback store. No network or browser credentials."""
import argparse
from datetime import datetime, timedelta, timezone
import json
import math
import os
from pathlib import Path
import re
import sys
import tempfile
from urllib.parse import parse_qsl, urlsplit


BVID = re.compile(r"BV[A-Za-z0-9]{10}\Z")
EVIDENCE = {"metadata", "description", "subtitles", "audiovisual"}
AVAILABILITY = {"page_available", "playback_verified", "unknown"}
ORIGINS = {"favorite", "search", "feed", "creator"}
EVENTS = {"recommended", "opened", "watched", "skip", "feedback"}
KINDS = {"topic", "creator"}
STANCES = {"like", "dislike", "neutral"}
DEFAULTS = {"recommendation_count": 5, "preferred_duration_minutes": [10, 30], "strict_duration": False}


def target_key(kind, target):
    return kind, target.strip().casefold()


def require_text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise StoreError(f"{label} cannot be empty")
    return value


class StoreError(Exception):
    pass


def utc_now():
    return datetime.now(timezone.utc)


def stamp(value):
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_time(value, label, now=None):
    if not isinstance(value, str):
        raise StoreError(f"{label} must be a timezone-aware ISO timestamp")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise StoreError(f"{label} is not a valid ISO timestamp") from None
    if result.tzinfo is None or result.utcoffset() is None:
        raise StoreError(f"{label} must include a timezone")
    result = result.astimezone(timezone.utc)
    if now is not None and result > now:
        raise StoreError(f"{label} cannot be in the future")
    return result


def check_bvid(value):
    if not isinstance(value, str) or not BVID.fullmatch(value):
        raise StoreError("bvid must be BV followed by 10 letters/digits")
    return value


def read_json(path):
    try:
        with Path(path).open(encoding="utf-8-sig") as handle:
            return json.load(handle)
    except (OSError, ValueError) as error:
        raise StoreError(f"Cannot read {path}: {error}") from None


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=f".{path.name}.", suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
            json.dump(value, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def normalize_candidate(raw, now):
    if not isinstance(raw, dict):
        raise StoreError("Each candidate must be an object")
    result = dict(raw)
    bvid = check_bvid(raw.get("bvid"))
    for key in ("title", "creator"):
        if not isinstance(raw.get(key), str) or not raw[key].strip():
            raise StoreError(f"{bvid}: {key} is required")
        result[key] = raw[key].strip()
    if "duration_seconds" not in raw:
        raise StoreError(f"{bvid}: duration_seconds is required (null means unknown)")
    duration = raw["duration_seconds"]
    if duration is not None and (isinstance(duration, bool) or not isinstance(duration, (int, float))
                                 or not math.isfinite(duration) or duration <= 0):
        raise StoreError(f"{bvid}: duration_seconds must be positive or null")
    try:
        parsed = urlsplit(raw.get("url", ""))
        valid_url = (parsed.scheme == "https" and parsed.netloc in {"www.bilibili.com", "bilibili.com"}
                     and parsed.path in {f"/video/{bvid}", f"/video/{bvid}/"})
        positioning_keys = {"p", "t", "start_progress", "start", "time_continue"}
        if parsed.fragment or any(key.lower() in positioning_keys for key, _ in parse_qsl(parsed.query, keep_blank_values=True)):
            raise StoreError(f"{bvid}: part/time-position URLs are unsupported; use a whole-video candidate")
    except (ValueError, TypeError):
        valid_url = False
    if not valid_url:
        raise StoreError(f"{bvid}: url must be an HTTPS Bilibili video URL matching bvid")
    result["url"] = f"https://www.bilibili.com/video/{bvid}"
    result["checked_at"] = stamp(parse_time(raw.get("checked_at"), f"{bvid}.checked_at", now))
    for key, choices in (("evidence", EVIDENCE), ("availability", AVAILABILITY), ("origin", ORIGINS)):
        if not isinstance(raw.get(key), str) or raw[key] not in choices:
            raise StoreError(f"{bvid}: invalid {key}; expected {', '.join(sorted(choices))}")
    topics = raw.get("topics")
    if not isinstance(topics, list) or any(not isinstance(topic, str) or not topic.strip() for topic in topics):
        raise StoreError(f"{bvid}: topics must be a list of non-empty strings")
    result["topics"] = list(dict.fromkeys(topic.strip() for topic in topics))
    if "watched_at" in result:
        result["watched_at"] = stamp(parse_time(result["watched_at"], f"{bvid}.watched_at", now))
    if "taste_reaction" in result and (not isinstance(result["taste_reaction"], str)
                                        or result["taste_reaction"] not in STANCES):
        raise StoreError(f"{bvid}: taste_reaction must be like, dislike or neutral")
    return result


class Store:
    def __init__(self, directory):
        self.directory = Path(directory)

    def path(self, name):
        return self.directory / f"{name}.json"

    def load(self, name):
        value = read_json(self.path(name))
        if not isinstance(value, dict) or value.get("schema_version") != 1:
            raise StoreError(f"{name}.json: expected schema_version 1 object; existing data was not reset")
        key = {"candidates": "items", "events": "events"}.get(name)
        if key and not isinstance(value.get(key), list):
            raise StoreError(f"{name}.json: {key} must be a list; existing data was not reset")
        return value

    def initialize(self):
        defaults = {"profile": {"schema_version": 1, "defaults": DEFAULTS.copy(),
                                "explicit_preferences": {}, "inferred_preferences": [],
                                "preference_rules": [], "session_rules": [],
                                "feedback_observations": [], "learning_history": []},
                    "candidates": {"schema_version": 1, "items": []},
                    "events": {"schema_version": 1, "events": []}}
        # Check existing files first, so a broken store is never silently replaced.
        for name in defaults:
            if self.path(name).exists():
                self.load(name)
        created = []
        for name, value in defaults.items():
            if not self.path(name).exists():
                atomic_json(self.path(name), value)
                created.append(name + ".json")
        return {"state_dir": str(self.directory), "created": created}

    def candidates(self, now):
        value = self.load("candidates")
        value["items"] = [normalize_candidate(item, now) for item in value["items"]]
        ids = [item["bvid"] for item in value["items"]]
        if len(ids) != len(set(ids)):
            raise StoreError("candidates.json contains duplicate bvids; existing data was not reset")
        return value

    def events(self, now):
        value = self.load("events")
        for event in value["events"]:
            if (not isinstance(event, dict) or not isinstance(event.get("event"), str)
                    or event["event"] not in EVENTS):
                raise StoreError("events.json contains an invalid event")
            check_bvid(event.get("bvid"))
            parse_time(event.get("at"), "event.at", now)
        return value

    def ingest(self, raw, now):
        items = raw.get("items") if isinstance(raw, dict) else raw
        if not isinstance(items, list):
            raise StoreError("Import must be a candidate array or an object containing items")
        validated = [normalize_candidate(item, now) for item in items]
        value = self.candidates(now)
        merged = {item["bvid"]: item for item in value["items"]}
        for item in validated:
            previous = merged.get(item["bvid"], {})
            # Refresh metadata without changing the user's viewing/taste decisions.
            for key in ("watched_at", "taste_reaction"):
                if key in previous:
                    item[key] = previous[key]
            merged[item["bvid"]] = {**previous, **item}
        value["items"] = list(merged.values())
        atomic_json(self.path("candidates"), value)
        return {"imported": len(validated), "total": len(merged)}

    def pool(self, now, limit, session=None, include_recent=False):
        candidates = self.candidates(now)["items"]
        events = self.events(now)["events"]
        taste = self.taste(now, session)
        rules = {target_key(rule["kind"], rule["target"]): rule for rule in taste["explicit_rules"]}
        rules.update({target_key(rule["kind"], rule["target"]): rule for rule in taste["session_rules"]})
        hints = {target_key(kind, hint["target"]): {"kind": kind, **hint}
                 for kind, name in (("topic", "topics"), ("creator", "creators"))
                 for hint in taste["inferred"][name]}
        excluded = set()
        for event in events:
            age = now - parse_time(event["at"], "event.at")
            if event["event"] == "watched":
                excluded.add(event["bvid"])
            elif event["event"] == "recommended" and not include_recent and age <= timedelta(days=7):
                excluded.add(event["bvid"])
            elif event["event"] == "skip" and (age <= timedelta(hours=24)
                    or (session and event.get("session") == session)):
                excluded.add(event["bvid"])
        result = []
        for item in candidates:
            if item.get("watched_at") or item["bvid"] in excluded or item["availability"] == "unknown":
                continue
            if now - parse_time(item["checked_at"], "checked_at") > timedelta(days=14):
                continue
            if item.get("taste_reaction") == "dislike":
                continue
            keys = {target_key("creator", item["creator"])}
            keys.update(target_key("topic", topic) for topic in item["topics"])
            if any(rules[key]["stance"] == "dislike" for key in keys if key in rules):
                continue
            result.append({**item, "duration_unknown": item["duration_seconds"] is None,
                           "preference_matches": [rules[key] for key in sorted(keys)
                                                  if key in rules and rules[key]["stance"] == "like"],
                           "inferred_taste_hints": [hints[key] for key in sorted(keys) if key in hints]})
        result.sort(key=lambda item: parse_time(item["checked_at"], "checked_at"), reverse=True)
        return {"items": result[:limit], "eligible_total": len(result), "state_dir": str(self.directory)}

    def profile(self, now):
        profile = self.load("profile")
        if not isinstance(profile.get("defaults", {}), dict):
            raise StoreError("profile.json: defaults must be an object; existing data was not reset")
        for key in ("preference_rules", "session_rules", "feedback_observations", "learning_history"):
            if not isinstance(profile.get(key, []), list):
                raise StoreError(f"profile.json: {key} must be a list; existing data was not reset")
        for key in ("preference_rules", "session_rules"):
            for rule in profile.get(key, []):
                if (not isinstance(rule, dict) or not isinstance(rule.get("kind"), str)
                        or rule["kind"] not in KINDS or not isinstance(rule.get("stance"), str)
                        or rule["stance"] not in STANCES):
                    raise StoreError(f"profile.json: invalid {key} rule")
                require_text(rule.get("target"), "rule.target")
                require_text(rule.get("text"), "rule.text")
                at = parse_time(rule.get("at"), "rule.at", now)
                if key == "session_rules":
                    require_text(rule.get("session"), "rule.session")
                    expires = parse_time(rule.get("expires_at"), "rule.expires_at")
                    if not at < expires <= at + timedelta(hours=24):
                        raise StoreError("session rule lifetime must be positive and at most 24 hours")
        for observation in profile.get("feedback_observations", []):
            if (not isinstance(observation, dict) or not isinstance(observation.get("reaction"), str)
                    or observation["reaction"] not in STANCES):
                raise StoreError("profile.json: invalid feedback observation")
            check_bvid(observation.get("bvid"))
            require_text(observation.get("creator"), "observation.creator")
            require_text(observation.get("text"), "observation.text")
            if (not isinstance(observation.get("topics"), list)
                    or any(not isinstance(topic, str) or not topic.strip() for topic in observation["topics"])):
                raise StoreError("profile.json: invalid observation topics")
            parse_time(observation.get("at"), "observation.at", now)
        return profile

    def learn(self, kind, target, stance, scope, text, now, session=None):
        if kind not in KINDS or stance not in STANCES or scope not in {"long-term", "session"}:
            raise StoreError("Invalid preference kind, stance or scope")
        target = require_text(target, "Preference target").strip()
        require_text(text, "Learning text")
        if scope == "session":
            session = require_text(session, "Session ID").strip()
        profile = self.profile(now)
        key = "session_rules" if scope == "session" else "preference_rules"
        rules = profile.get(key, [])
        def same(rule):
            return (target_key(rule["kind"], rule["target"]) == target_key(kind, target)
                    and (scope != "session" or rule["session"] == session))
        previous = next((rule for rule in reversed(rules) if same(rule)), {})
        remaining = [rule for rule in rules if not same(rule)]
        entry = {"kind": kind, "target": target, "stance": stance, "scope": scope,
                 "text": text, "at": stamp(now)}
        if scope == "session":
            entry.update(session=session, expires_at=stamp(now + timedelta(hours=24)))
        # A session neutral temporarily clears even a long-term opinion, then expires.
        if stance != "neutral" or scope == "session":
            remaining.append({**previous, **entry})
        profile[key] = remaining[-300:] if scope == "session" else remaining
        profile["learning_history"] = (profile.get("learning_history", []) + [entry])[-100:]
        atomic_json(self.path("profile"), profile)
        return {"learned": entry, "cleared": scope == "long-term" and stance == "neutral"}

    def taste(self, now, session=None):
        profile = self.profile(now)
        explicit = profile.get("preference_rules", [])
        temporary = [rule for rule in profile.get("session_rules", [])
                     if session and rule["session"] == session
                     and now < parse_time(rule["expires_at"], "rule.expires_at")]
        groups = {"topic": {}, "creator": {}}
        observations = profile.get("feedback_observations", [])
        # One latest observation per video; repeated feedback is never independent evidence.
        latest = {}
        for observation in observations:
            bvid = observation["bvid"]
            if bvid not in latest or parse_time(observation["at"], "at") >= parse_time(latest[bvid]["at"], "at"):
                latest[bvid] = observation
        # Feedback evaluates a video across meals; session records its origin only.
        # Only explicit learn --scope session rules above have a 24-hour lifetime.
        active = list(latest.values())
        for observation in active:
            targets = {"creator": [observation["creator"]], "topic": observation["topics"]}
            for kind, labels in targets.items():
                for normalized, label in {label.strip().casefold(): label.strip() for label in labels}.items():
                    group = groups[kind].setdefault(normalized, {"target": label, "like_count": 0,
                        "dislike_count": 0, "neutral_count": 0, "videos": set()})
                    group[observation["reaction"] + "_count"] += 1
                    group["videos"].add(observation["bvid"])
        inferred = {"role": "ranking_hint_only"}
        for kind, name in (("topic", "topics"), ("creator", "creators")):
            inferred[name] = []
            for normalized in sorted(groups[kind]):
                group = groups[kind][normalized]
                count = len(group["videos"])
                inferred[name].append({key: value for key, value in group.items() if key != "videos"})
                inferred[name][-1].update(distinct_video_count=count,
                                         confidence="medium" if count >= 2 else "weak")
        return {"defaults": {**DEFAULTS, **profile.get("defaults", {})}, "explicit_rules": explicit,
                "session_rules": temporary, "inferred": inferred,
                "feedback_video_count": len(latest), "active_feedback_video_count": len(active),
                "learning_history_count": len(profile.get("learning_history", []))}

    def status(self, now):
        return {"state_dir": str(self.directory), "profile": self.profile(now),
                "candidate_count": len(self.candidates(now)["items"]),
                "event_count": len(self.events(now)["events"]),
                "learning_summary": self.taste(now)}

    def record(self, bvid, event, now, session=None, text=None, reaction=None):
        check_bvid(bvid)
        if event not in EVENTS:
            raise StoreError("Invalid event")
        if reaction is not None and (event != "feedback" or reaction not in STANCES):
            raise StoreError("Reaction is only supported for feedback and must be like, dislike or neutral")
        if event == "feedback":
            require_text(text, "Feedback text")
        elif text is not None:
            require_text(text, "Feedback text")
        candidates = self.candidates(now)
        candidate = next((item for item in candidates["items"] if item["bvid"] == bvid), None)
        if candidate is None:
            raise StoreError(f"{bvid}: not in candidate store; ingest verified metadata first")
        events = self.events(now)
        profile = self.profile(now) if reaction is not None else None
        entry = {"bvid": bvid, "event": event, "at": stamp(now)}
        if session:
            entry["session"] = session
        if text is not None:
            entry["text"] = text
        if event == "watched":
            candidate["watched_at"] = stamp(now)
        if reaction is not None:
            entry["reaction"] = reaction
            candidate["taste_reaction"] = reaction
            observations = profile.get("feedback_observations", [])
            previous = next((item for item in observations if item["bvid"] == bvid), {})
            observation = {**previous, "bvid": bvid, "creator": candidate["creator"],
                           "topics": candidate["topics"], "reaction": reaction, "session": session,
                           "at": stamp(now), "text": text}
            profile["feedback_observations"] = ([item for item in observations if item["bvid"] != bvid]
                                                + [observation])[-300:]
            atomic_json(self.path("profile"), profile)
        if event == "watched" or reaction is not None:
            # Retain user decisions even after the bounded event log rolls over.
            atomic_json(self.path("candidates"), candidates)
        events["events"] = (events["events"] + [entry])[-500:]
        atomic_json(self.path("events"), events)
        return entry


def positive_int(value):
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return number


def main(argv=None):
    default_root = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", type=Path, default=default_root / "skill-data" / "meal-video")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init")
    commands.add_parser("status")
    ingest = commands.add_parser("ingest")
    ingest.add_argument("json_file", type=Path)
    pool = commands.add_parser("pool")
    pool.add_argument("--limit", type=positive_int, default=20)
    pool.add_argument("--session")
    pool.add_argument("--include-recent", action="store_true")
    record = commands.add_parser("record")
    record.add_argument("bvid")
    record.add_argument("--event", choices=sorted(EVENTS - {"feedback"}), required=True)
    record.add_argument("--session")
    feedback = commands.add_parser("feedback")
    feedback.add_argument("bvid")
    feedback.add_argument("--text", required=True)
    feedback.add_argument("--session")
    feedback.add_argument("--reaction", choices=sorted(STANCES))
    learn = commands.add_parser("learn")
    learn.add_argument("--kind", choices=sorted(KINDS), required=True)
    learn.add_argument("--target", required=True)
    learn.add_argument("--stance", choices=sorted(STANCES), required=True)
    learn.add_argument("--scope", choices=["long-term", "session"], required=True)
    learn.add_argument("--text", required=True)
    learn.add_argument("--session")
    taste = commands.add_parser("taste")
    taste.add_argument("--session")
    args = parser.parse_args(argv)
    store = Store(args.state_dir)
    now = utc_now()
    try:
        if args.command == "init":
            result = store.initialize()
        elif args.command == "status":
            result = store.status(now)
        elif args.command == "ingest":
            result = store.ingest(read_json(args.json_file), now)
        elif args.command == "pool":
            result = store.pool(now, args.limit, args.session, args.include_recent)
        elif args.command == "learn":
            result = store.learn(args.kind, args.target, args.stance, args.scope, args.text, now, args.session)
        elif args.command == "taste":
            result = store.taste(now, args.session)
        else:
            result = store.record(args.bvid, "feedback" if args.command == "feedback" else args.event,
                                  now, args.session, getattr(args, "text", None),
                                  getattr(args, "reaction", None))
        print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
        return 0
    except (StoreError, OSError, ValueError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    sys.exit(main())
