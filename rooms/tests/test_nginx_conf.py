"""nginx guards that only production exercises, checked on the files (audit 2026-10-18)."""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
VHOST = (ROOT / "deploy/nginx/facilitation-api.conf").read_text(encoding="utf-8")
LIMITS = (ROOT / "deploy/nginx/facilitation-limits.conf").read_text(encoding="utf-8")
DEPLOY = (ROOT / ".github/workflows/deploy.yml").read_text(encoding="utf-8")


def _block(header):
    start = VHOST.index(header)
    return VHOST[start:VHOST.index("\n    }\n", start)]


class NginxConfTests(unittest.TestCase):
    def test_media_serves_images_only_sandboxed(self):
        block = _block("location ~* ^/media/")
        self.assertIn(r"\.(?:png|jpe?g|webp)$", block)
        self.assertIn("""Content-Security-Policy "default-src 'none'; sandbox\"""", block)
        self.assertIn('X-Content-Type-Options "nosniff"', block)
        self.assertIn("return 404;", _block("location /media/ {"))

    def test_admin_login_is_rate_limited(self):
        self.assertIn("limit_req zone=facilitation_admin_login", _block("location = /admin/login/"))
        self.assertRegex(LIMITS, r"limit_req_zone \$binary_remote_addr zone=facilitation_admin_login:\S+ rate=10r/m;")

    def test_websockets_are_capped_per_ip(self):
        self.assertIn("limit_conn facilitation_ws_conn", _block("    location /ws/ {"))
        self.assertIn("zone=facilitation_ws_conn", LIMITS)

    def test_deploy_installs_the_zones_before_the_vhost(self):
        limits = DEPLOY.index("deploy/nginx/facilitation-limits.conf > /etc/nginx/conf.d/facilitation-limits.conf")
        vhost = DEPLOY.index("deploy/nginx/facilitation-api.conf > /etc/nginx/sites-available/")
        self.assertLess(limits, vhost)
        self.assertTrue(re.search(r"nginx -t && systemctl reload nginx", DEPLOY))
