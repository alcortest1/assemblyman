import importlib.util
import pathlib
import unittest


SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "livekit_watch_and_open.py"
SPEC = importlib.util.spec_from_file_location("livekit_watch_and_open", SCRIPT)
WATCHER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(WATCHER)


class PickRoomTests(unittest.TestCase):
    def test_picks_publishing_room_from_protobuf_json(self):
        rooms = [
            {
                "name": "EMPTY1",
                "num_participants": 0,
                "num_publishers": 0,
                "creation_time": "200",
            },
            {
                "name": "PHONE1",
                "num_participants": 1,
                "num_publishers": 1,
                "creation_time": "100",
            },
        ]

        room, occupied = WATCHER.pick(rooms)

        self.assertEqual(room["name"], "PHONE1")
        self.assertTrue(occupied)

    def test_accepts_legacy_camel_case_room_json(self):
        rooms = [
            {
                "name": "PHONE2",
                "numParticipants": 1,
                "numPublishers": 1,
                "creationTime": "100",
            }
        ]

        room, occupied = WATCHER.pick(rooms)

        self.assertEqual(room["name"], "PHONE2")
        self.assertTrue(occupied)

    def test_picks_newest_empty_room(self):
        rooms = [
            {"name": "OLDER1", "creation_time": "100"},
            {"name": "NEWER1", "creation_time": "200"},
        ]

        room, occupied = WATCHER.pick(rooms)

        self.assertEqual(room["name"], "NEWER1")
        self.assertFalse(occupied)


if __name__ == "__main__":
    unittest.main()
