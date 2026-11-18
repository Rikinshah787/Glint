"""Session topic splitting: a long session splits when the last 10 minutes moved to unrelated work."""
import os, sys, tempfile
from pathlib import Path

os.environ["GLINT_HOME"] = tempfile.mkdtemp(prefix="glint-session-test-")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import engine as E

def feed(c, start, minutes, app, host=None):
    t = start
    for _ in range(int(minutes * 60 / 15)):
        c.cur_host = host
        t += 15
        c.track_topic(app, 15, t)
    return t

c = E.Capturer()
c.session_started = 1000.0
t = feed(c, 1000, 25, "Visual Studio Code")
assert not c.topic_changed(t), "same work never splits"
t = feed(c, t, 4, "Google Chrome", "github.com")
assert not c.topic_changed(t), "a short detour is not enough evidence"
t = feed(c, t, 10, "Google Chrome", "web.whatsapp.com")
assert c.topic_changed(t), "10 focused minutes on something unrelated splits"

c2 = E.Capturer()
c2.session_started = 1000.0
t = feed(c2, 1000, 15, "Google Chrome", "github.com")
t = feed(c2, t, 10, "Google Chrome", "www.github.com")
assert not c2.topic_changed(t), "same site (www. stripped) is the same topic"

c3 = E.Capturer()
c3.session_started = 1000.0
t = feed(c3, 1000, 5, "Notes")
t = feed(c3, t, 10, "Slack")
assert not c3.topic_changed(t), "young sessions never split"
# Truncated on-device replies keep every complete field.
import ai
assert ai._salvage_json('{"title":"A","summary":"cut mid') == {"title": "A"}
assert ai._salvage_json('x {"title":"A","insights":[{"headline":"h","implies":"cu') == {
    "title": "A", "insights": [{"headline": "h"}]}
assert ai._salvage_json('{"a":"q\\"x,y","b":[1,2]} tail') == {"a": 'q"x,y', "b": [1, 2]}
assert ai._salvage_json("no json") is None
print("sessions: ok")
