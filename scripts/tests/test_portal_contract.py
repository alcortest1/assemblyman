import base64
from html.parser import HTMLParser
import importlib.util
import json
import pathlib
import re
import unittest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
PORTAL_SERVER_SCRIPT = REPO_ROOT / "scripts" / "portal_dev_server.py"
SPEC = importlib.util.spec_from_file_location("portal_dev_server", PORTAL_SERVER_SCRIPT)
PORTAL_SERVER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PORTAL_SERVER)


class IdCollector(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = []

    def handle_starttag(self, _tag, attrs):
        attributes = dict(attrs)
        if "id" in attributes:
            self.ids.append(attributes["id"])


def decode_claims(token):
    payload = token.split(".")[1]
    payload += "=" * (-len(payload) % 4)
    return json.loads(base64.urlsafe_b64decode(payload))


class PortalContractTests(unittest.TestCase):
    def test_local_token_matches_browser_media_permissions(self):
        token = PORTAL_SERVER.mint(
            "test-key",
            "test-secret",
            "ABCDEF",
            "portal-test",
            ttl=60,
        )

        claims = decode_claims(token)

        self.assertEqual(claims["sub"], "portal-test")
        self.assertEqual(claims["video"]["room"], "ABCDEF")
        self.assertTrue(claims["video"]["roomJoin"])
        self.assertTrue(claims["video"]["canSubscribe"])
        self.assertEqual(
            claims["video"]["canPublishSources"],
            ["camera", "microphone"],
        )

    def test_html_ids_are_unique_and_cover_app_lookups(self):
        parser = IdCollector()
        parser.feed((REPO_ROOT / "web" / "index.html").read_text())
        ids = set(parser.ids)
        duplicates = sorted({element_id for element_id in parser.ids if parser.ids.count(element_id) > 1})
        app = (REPO_ROOT / "web" / "app.js").read_text()
        looked_up = set(re.findall(r"\$\('([^']+)'\)", app))

        self.assertEqual(duplicates, [])
        self.assertEqual(sorted(looked_up - ids), [])

    def test_demo_code_obeys_phone_room_code_contract(self):
        app = (REPO_ROOT / "web" / "app.js").read_text()
        match = re.search(r"var DEFAULT_CODE = '([^']+)'", app)
        self.assertIsNotNone(match)
        canonical = "".join(
            character
            for character in match.group(1).upper()
            if character in PORTAL_SERVER.ROOM_CHARS
        )

        self.assertEqual(len(canonical), PORTAL_SERVER.ROOM_CODE_LENGTH)


class VercelConfigTests(unittest.TestCase):
    """Guards the deploy config, which fails late and only on Vercel's side.

    A bad `source` does not break anything locally — the dev server ignores vercel.json
    entirely — so the first sign is a failed deployment on a pull request. These are cheap
    checks for the two ways that has actually happened.
    """

    def setUp(self):
        self.config = json.loads((REPO_ROOT / "vercel.json").read_text())
        self.sources = [entry["source"] for entry in self.config["headers"]]

    def test_header_sources_avoid_patterns_vercel_rejects(self):
        # `source` is path-to-regexp, not a regular expression. Alternation, and groups
        # nested inside groups, are rejected at deploy time — "/(app.js|styles/(.*))" is
        # what broke it, with the unhelpful message "Header at index N has invalid source".
        for index, source in enumerate(self.sources):
            with self.subTest(index=index, source=source):
                self.assertNotIn("|", source, "alternation is not valid in a Vercel source")
                depth = 0
                for character in source:
                    if character == "(":
                        depth += 1
                        self.assertLessEqual(depth, 1, "groups may not be nested")
                    elif character == ")":
                        depth -= 1
                self.assertEqual(depth, 0, "unbalanced parentheses")

    def test_portal_code_is_served_without_caching(self):
        # Everything the portal's behaviour lives in has to revalidate, or a deploy ships
        # while browsers keep running the previous build. livekit-bridge.js was added later
        # than this rule and went uncovered until the pattern above was split up.
        must_revalidate = {
            source
            for entry in self.config["headers"]
            for source in [entry["source"]]
            if any(
                header["key"] == "Cache-Control" and "max-age=0" in header["value"]
                for header in entry["headers"]
            )
        }

        for path in ("/index.html", "/app.js", "/livekit-bridge.js"):
            with self.subTest(path=path):
                self.assertIn(path, must_revalidate)
        self.assertTrue(
            any(source.startswith("/styles/") for source in must_revalidate),
            "stylesheets must revalidate too",
        )

    def test_every_portal_entry_point_exists(self):
        # A header for a file that is not deployed is a rule that silently never matches.
        web = REPO_ROOT / "web"
        for source in self.sources:
            if "(" in source or ":" in source:
                continue
            with self.subTest(source=source):
                self.assertTrue((web / source.lstrip("/")).is_file(), f"{source} is not in web/")


if __name__ == "__main__":
    unittest.main()
