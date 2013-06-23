import logging

from pylons import tmpl_context as c
from pylons.i18n import _

from adhocracy import model
from adhocracy.lib import event, helpers as h, tiles
from adhocracy.lib import pager
from adhocracy.lib.auth import guard
from adhocracy.lib.base import BaseController
from adhocracy.lib.templating import render

log = logging.getLogger(__name__)


class EventController(BaseController):

    @guard.perm('event.index_all')
    def all(self, format='html'):
        query = model.meta.Session.query(model.Event)\
            .join(model.Instance).filter(model.Instance.hidden == False)\
            .order_by(model.Event.time.desc())\
            .limit(50)

        if format == 'rss':
            events = query.all()
            return event.rss_feed(events,
                                  _('%s News' % h.site.name()),
                                  h.base_url(instance=None),
                                  _("News from %s") % h.site.name())

        c.event_pager = pager.events(query.all(), u'events', count=50)
        return render('/event/all.html')
