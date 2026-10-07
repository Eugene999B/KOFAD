from django.conf import settings
from django.contrib.sessions.backends.db import SessionStore
from django.contrib.sessions.middleware import SessionMiddleware
from django.http import HttpResponse
from django.test import RequestFactory, TestCase

from .maintenance import mark_backup_downloaded, recent_backup_downloaded


class ReadOnlySessionTests(TestCase):
    def test_poll_does_not_overwrite_concurrent_backup_confirmation(self):
        session = SessionStore()
        session["access_version"] = 1
        session.save()
        request = RequestFactory().get("/backup/status/")
        request.COOKIES[settings.SESSION_COOKIE_NAME] = session.session_key

        def poll(req):
            self.assertEqual(req.session.get("access_version"), 1)
            # The download finishes after this poll has read its older snapshot.
            download_session = SessionStore(session_key=session.session_key)
            mark_backup_downloaded(download_session)
            download_session.save()
            return HttpResponse("read-only poll")

        SessionMiddleware(poll)(request)
        fresh = SessionStore(session_key=session.session_key)
        self.assertTrue(recent_backup_downloaded(fresh))
