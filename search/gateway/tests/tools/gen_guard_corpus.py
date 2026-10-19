"""Regenerate tests/fixtures/guard_corpus.json:  python tests/tools/gen_guard_corpus.py <out.json>"""
import json
import sys

R = []


def ref(group, why, *urls):
    for u in urls:
        R.append({"url": u, "expect": "refuse", "why": why, "group": group})


def ok(group, *urls):
    for u in urls:
        R.append({"url": u, "expect": "allow", "group": group})


ref("scheme", "is not http(s)", "file:///etc/passwd", "ftp://example.com/", "gopher://example.com/",
    "javascript:alert(1)", "data:text/html,hi", "ws://example.com/", "wss://example.com/",
    "dict://example.com:11211/", "ldap://example.com/", "jar:http://example.com!/", "netdoc:///etc/passwd")
ref("scheme", "", "//example.com/x", "example.com", "/relative/path", "")
ref("userinfo", "credentials", "http://user:pw@example.com/", "http://user@example.com/",
    "https://:pw@example.com/", "http://example.com@10.0.0.1/", "http://a@b@example.com/",
    "http://user%40x@example.com/")
ref("port", "port", "http://example.com:6379/", "https://example.com:8443/", "http://example.com:22/",
    "http://example.com:5432/", "http://example.com:8080/", "http://example.com:0/")
ref("port", "", "http://example.com:99999/", "http://example.com:-1/", "http://example.com:80:80/",
    "http://example.com:8o/")
ref("localhost", "localhost", "http://localhost/", "http://LOCALHOST/", "http://localhost./",
    "https://localhost/x")
ref("localhost", "", "http://localhost../")
ref("localhost", "internal hostname", "http://foo.localhost/", "http://a.b.localhost./")
ref("single-label", "single-label", "http://search-redis/", "http://searxng/", "http://vpn/",
    "http://intranet/", "http://openbrain-db/", "http://gateway/", "http://llama-cpp/", "http://metadata/",
    "http://router./")
for s in [".local", ".localdomain", ".internal", ".lan", ".home", ".home.arpa", ".arpa", ".intranet",
          ".corp", ".private", ".docker", ".test", ".invalid", ".ts.net"]:
    ref("internal-suffix", "internal hostname", "http://host" + s + "/")
ref("internal-suffix", "internal hostname", "http://1.0.0.127.in-addr.arpa/", "http://printer.LOCAL./",
    "http://box.tail1234.ts.net/", "http://metadata.google.internal/",
    "http://kubernetes.default.svc.cluster.local/", "http://host.docker.internal/")
ref("wildcard-dns", "wildcard-DNS", "http://127.0.0.1.nip.io/", "http://10.0.0.7.nip.io/",
    "http://app.192.168.1.1.nip.io/", "http://10-0-0-1.sslip.io/", "http://x.xip.io/", "http://localtest.me/",
    "http://sub.localtest.me/", "http://lvh.me/", "http://a.lvh.me/", "http://a.vcap.me/", "http://x.traefik.me/",
    "http://lacolhost.com/", "http://make-127.0.0.1-rr.1u.ms/", "http://nip.io/", "http://fbi.com/")
v4 = ["0.0.0.0", "0.1.2.3", "10.0.0.1", "10.255.255.255", "100.64.0.1", "100.127.255.255", "127.0.0.1",
      "127.255.255.254", "169.254.169.254", "169.254.0.1", "172.16.0.1", "172.31.255.255", "192.0.0.1",
      "192.0.0.170", "192.168.0.1", "192.168.192.4", "198.18.0.1", "198.19.255.255", "224.0.0.1",
      "239.255.255.250", "240.0.0.1", "255.255.255.255", "192.0.2.10", "198.51.100.1", "203.0.113.5"]
ref("v4-literal", "target is a", *["http://%s/" % a for a in v4])
ref("v4-numeric", "target is a", "http://2130706433/", "http://0x7f000001/", "http://0x7f.1/",
    "http://0177.0.0.1/", "http://0177.1/", "http://127.1/", "http://127.0.1/", "http://017700000001/",
    "http://10.1/", "http://3232235521/", "http://0xA9FEA9FE/", "http://0xa9.0xfe.0xa9.0xfe/",
    "http://127.0.0.1./", "http://0/", "http://0x0/", "http://00000000/", "http://167772161/",
    "http://0x0a.0x00.0x00.0x01/")
ref("v4-numeric", "numeric but is not a valid IPv4", "http://1.2.3.999/", "http://08.1.1.1/",
    "http://1.2.3.4.5/", "http://4294967296/", "http://256.1.1.1/", "http://1.2.3.0x100/",
    "http://0x100000000/")
ref("unicode", "target is a", "http://１２７.０.０.１/",
    "http://127。0。0。1/", "http://127．0．0．1/")
ref("unicode", "localhost", "http://ｌｏｃａｌｈｏｓｔ/")
ref("encoding", "", "http://%31%32%37.0.0.1/", "http://exa%00mple.com/", "http://example.com%2F@10.0.0.1/")
v6 = ["[::1]", "[::]", "[::ffff:127.0.0.1]", "[::ffff:7f00:1]", "[::ffff:10.0.0.1]", "[::ffff:192.168.192.4]",
      "[::ffff:169.254.169.254]", "[::127.0.0.1]", "[::10.0.0.1]", "[2002:7f00:1::]", "[2002:c0a8:101::1]",
      "[2002:a00:1::1]", "[2001::1]", "[2001:0:4136:e378:8000:63bf:3fff:fdd2]", "[64:ff9b::7f00:1]",
      "[64:ff9b::a00:1]", "[64:ff9b::a9fe:a9fe]", "[64:ff9b:1::1]", "[fc00::1]", "[fd12:3456::1]",
      "[fdff:ffff::1]", "[fe80::1]", "[febf::1]", "[fec0::1]", "[ff02::1]", "[ff05::2]", "[2001:db8::1]",
      "[100::1]", "[0:0:0:0:0:ffff:7f00:0001]", "[::ffff:0:7f00:1]", "[::ffff:0:a00:1]",
      "[::ffff:0:5db8:d70e]"]
ref("v6-literal", "target is a", *["http://%s/" % a for a in v6])
ref("v6-literal", "", "http://[fe80::1%25eth0]/", "http://[::1%25lo]/", "http://[::1/", "http://[:::1]/",
    "http://[1::2::3]/", "http://[gggg::1]/")
ref("malformed", "", "http://", "http:///x", "not a url", "http://exa mple.com/",
    "http://example.com\\@evil.example/", "http://ex..ample.com/", "http://-/x", "http://example.com\t/",
    "http://exa\nmple.com/", "http://.example.com/", "http://_/x", "http://example.com/\x7f")
ok("public-name", "https://example.com/", "http://example.com/path?q=1&r=2", "https://EXAMPLE.com./x",
   "https://sub.example.co.uk/a/b", "https://example.com:443/", "http://example.com:80/", "https://example.com:80/", "http://example.com:443/",
   "https://xn--bcher-kva.example/", "https://bücher.example/", "https://example.com/a%20b",
   "https://example.com/#frag", "https://www.wikipedia.org/wiki/Bread", "https://news.ycombinator.com/item?id=1",
   "http://localhostexample.com/", "http://local.example.com/", "http://internal.example.com/",
   "http://test.example.com/", "https://my.lan.example.org/", "http://nip.io.example.com/")
ok("public-v4", "https://93.184.215.14/", "http://1.1.1.1/", "https://8.8.8.8/dns", "http://100.63.255.255/",
   "http://100.128.0.1/", "http://172.15.255.255/", "http://172.32.0.1/", "http://198.17.255.255/",
   "http://198.20.0.1/", "http://11.0.0.1/", "http://192.169.0.1/", "http://126.255.255.255/",
   "http://128.0.0.1/", "http://169.253.255.255/", "http://223.255.255.254/", "http://1572394766/",
   "http://0x5db8d70e/")
ok("public-v6", "http://[2606:2800:21f:cb07:6820:80da:af6b:8b2c]/", "https://[2001:4860:4860::8888]/",
   "http://[2a00:1450:4001:82a::200e]/", "http://[::ffff:93.184.215.14]/", "http://[64:ff9b::5db8:d70e]/",
   "http://[2002:5db8:d70e::1]/")
seen = set()
out = []
for r in R:
    if r["url"] in seen:
        continue
    seen.add(r["url"])
    out.append(r)
PUB = "93.184.215.14"
V6 = "2606:2800:21f:cb07:6820:80da:af6b:8b2c"
addr = [
    {"host": "public.example.com", "answers": [PUB], "expect": "pin", "pin": PUB},
    {"host": "dual.example.com", "answers": [V6, PUB], "expect": "pin", "pin": PUB},
    {"host": "v6only.example.com", "answers": [V6], "expect": "pin", "pin": V6},
    {"host": "private.example.com", "answers": ["10.1.2.3"], "expect": "refuse"},
    {"host": "rebind.example.com", "answers": [PUB, "127.0.0.1"], "expect": "refuse"},
    {"host": "rebind6.example.com", "answers": [PUB, "::1"], "expect": "refuse"},
    {"host": "mapped.example.com", "answers": ["::ffff:10.0.0.1"], "expect": "refuse"},
    {"host": "meta.example.com", "answers": ["169.254.169.254"], "expect": "refuse"},
    {"host": "cgnat.example.com", "answers": ["100.100.100.100"], "expect": "refuse"},
    {"host": "ula.example.com", "answers": ["fd00::1"], "expect": "refuse"},
    {"host": "zone.example.com", "answers": ["fe80::1%eth0"], "expect": "refuse"},
    {"host": "garbage.example.com", "answers": ["not-an-ip"], "expect": "refuse"},
    {"host": "nxdomain.example.com", "answers": [], "expect": "refuse"},
    {"host": "zero.example.com", "answers": ["0.0.0.0"], "expect": "refuse"},
]
doc = {
    "about": "The search gate's target-guard corpus (search/gateway/src/gateway/egress/guard.py). "
             "'urls': each URL is judged by check_url with ports {80,443}; expect refuse/allow; 'why', when "
             "non-empty, is a substring of the refusal reason. 'answers': resolved address sets judged by "
             "check_addresses (any non-public answer refuses the name; the pin prefers IPv4). "
             "tests/tools/ts_parity.ts runs the same URLs through OB1 ingest-egress.ts urlBlockReason (the guard "
             "this ports) to show the port refuses everything the original refuses.",
    "urls": out,
    "answers": addr,
}
with open(sys.argv[1], "w", encoding="utf-8", newline="\n") as f:
    f.write(json.dumps(doc, indent=1, ensure_ascii=True) + "\n")
print(len(out), sum(1 for r in out if r["expect"] == "refuse"), sum(1 for r in out if r["expect"] == "allow"),
      len(addr))
