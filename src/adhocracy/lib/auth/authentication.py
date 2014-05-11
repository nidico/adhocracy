import logging

from repoze.who.plugins.basicauth import BasicAuthPlugin
from repoze.who.plugins.sa import SQLAlchemyAuthenticatorPlugin
from repoze.who.plugins.sa import SQLAlchemyUserMDPlugin
from repoze.who.plugins.friendlyform import FriendlyFormPlugin

from repoze.what.middleware import setup_auth as setup_what
from repoze.what.plugins.sql.adapters import SqlPermissionsAdapter

from adhocracy.config import get_json as config_get_list
import adhocracy.model as model
from . import welcome
from authorization import InstanceGroupSourceAdapter
from instance_auth_tkt import InstanceAuthTktCookiePlugin

from sqlalchemy.orm.exc import NoResultFound, MultipleResultsFound
from pylons import config
from webob import Request

log = logging.getLogger(__name__)


LOGIN_CONFIG = {
    'local': {
        'type': 'local',
        'name': 'Local login',
        'logo': '/images/logo_adhocracy_code.png',
    },
    'shibboleth': {
        'type': 'shibboleth',
        'name': 'Shibboleth',
        'logo': 'FIXME',
    },
    'openid': {
        'type': 'openid',
        'name': 'OpenID',
        'logo': '/openid-selector/images.large/openid.gif',
        'openid_url': None,
    },
    'google': {
        'type': 'openid',
        'name': 'Google',
        'logo': '/openid-selector/images.large/google.gif',
        'openid_url': 'https://www.google.com/accounts/o8/id',
    },
    'yahoo': {
        'type': 'openid',
        'name': 'Yahoo',
        'logo': '/openid-selector/images.large/yahoo.gif',
        'openid_url': 'https://me.yahoo.com',
    },
    'facebook': {
        'type': 'velruse',
        'name': 'Facebook',
        'logo': '/velruse/facebook_login.png',
    },
}


class _EmailBaseSQLAlchemyPlugin(object):
    default_translations = {
        'user_name': 'user_name',
        'email': 'email',
        'validate_password': 'validate_password'
    }

    def get_user(self, login):
        local_login_types = config_get_list('adhocracy.login_type.local',
                                            config=config)
        allow_name = 'username+password' in local_login_types
        allow_email = 'email+password' in local_login_types

        if allow_name:
            if allow_email:
                login_type = u'email' if u'@' in login else u'user_name'
            else:
                login_type = u'user_name'
        else:
            if allow_email:
                login_type = u'email'
            else:
                return None

        login_attr = getattr(self.user_class, self.translations[login_type])
        query = self.dbsession.query(self.user_class)
        query = query.filter(login_attr == login)

        try:
            return query.one()
        except (NoResultFound, MultipleResultsFound):
            # As recommended in the docs for repoze.who, it's important to
            # verify that there's only _one_ matching userid.
            return None


class EmailSQLAlchemyAuthenticatorPlugin(_EmailBaseSQLAlchemyPlugin,
                                         SQLAlchemyAuthenticatorPlugin):

    def authenticate(self, environ, identity):
        if not ("login" in identity and "password" in identity):
            return None

        user = self.get_user(identity['login'])

        if user:
            validator = getattr(user, self.translations['validate_password'])
            if validator(identity['password']):
                return user.user_name


class EmailSQLAlchemyUserMDPlugin(_EmailBaseSQLAlchemyPlugin,
                                  SQLAlchemyUserMDPlugin):
    pass


class AlternateLoginFriendlyFormPlugin(FriendlyFormPlugin):
    def __init__(self, get_user, *args, **kwargs):
        self._get_user = get_user
        super(AlternateLoginFriendlyFormPlugin, self).__init__(*args, **kwargs)

    def identify(self, environ):
        if environ['PATH_INFO'] == self.login_handler_path:
            request = Request(environ, charset=self.charset)
            form = dict(request.POST)
            if form.get('have_password') == 'false':
                environ['PATH_INFO'] = '/user/nopassword'
                login = form.get('login')
                environ['_adhocracy_nopassword_user'] = self._get_user(login)
                return None

        return super(AlternateLoginFriendlyFormPlugin, self).identify(environ)


def setup_auth(app, config):
    groupadapter = InstanceGroupSourceAdapter()
    # groupadapter.translations.update({'sections': 'groups'})
    permissionadapter = SqlPermissionsAdapter(model.Permission,
                                              model.Group,
                                              model.meta.Session)
    # permissionadapter.translations.update(permission_translations)

    group_adapters = {'sql_auth': groupadapter}
    permission_adapters = {'sql_auth': permissionadapter}

    basicauth = BasicAuthPlugin('Adhocracy HTTP Authentication')
    auth_tkt = InstanceAuthTktCookiePlugin(
        config,
        config.get('adhocracy.auth.secret', config['beaker.session.secret']),
        cookie_name='adhocracy_login', timeout=86400 * 2,
        reissue_time=3600,
        secure=config.get('adhocracy.protocol', 'http') == 'https'
    )

    sqlauth = EmailSQLAlchemyAuthenticatorPlugin(model.User,
                                                 model.meta.Session)
    sql_user_md = SQLAlchemyUserMDPlugin(model.User, model.meta.Session)

    login_urls = [
        '/login',
        '/perform_login',
        '/post_login',
        '/logout',
        '/post_logout',
    ]
    login_options = dict(
        login_counter_name='_login_tries',
        rememberer_name='auth_tkt',
        charset='utf-8',
    )
    if config.get('adhocracy.login_style') == 'alternate':
        form = AlternateLoginFriendlyFormPlugin(sqlauth.get_user,
                                                *login_urls, **login_options)
    else:
        form = FriendlyFormPlugin(*login_urls, **login_options)

    identifiers = [('form', form),
                   ('basicauth', basicauth),
                   ('auth_tkt', auth_tkt)]
    authenticators = [('sqlauth', sqlauth), ('auth_tkt', auth_tkt)]
    challengers = [('form', form), ('basicauth', basicauth)]
    mdproviders = [('sql_user_md', sql_user_md)]

    welcome.setup_auth(config, identifiers, authenticators)

    log_stream = None
    # log_stream = sys.stdout

    # If a webserver already sets a HTTP_REMOTE_USER environment variable,
    # repoze.who merely acts as a pass through and doesn't set up the proper
    # environment (e.g. environ['repoze.who.api'] is missing).
    #
    # This happens for example in the case of Shibboleth based authentication -
    # we weren't able to prevent mod_shibboleth from setting the header.
    # Therefore the remote user key to look for is not set to HTTP_REMOTE_USER,
    # but to the non-existing DONT_USE_HTTP_REMOTE_USER environment variable.

    REMOTE_USER_KEY = 'DONT_USE_HTTP_REMOTE_USER'

    return setup_what(app, group_adapters, permission_adapters,
                      identifiers=identifiers,
                      authenticators=authenticators,
                      challengers=challengers,
                      mdproviders=mdproviders,
                      log_stream=log_stream,
                      log_level=logging.DEBUG,
                      # kwargs passed to repoze.who.plugins.testutils:
                      skip_authentication=config.get('skip_authentication'),
                      remote_user_key=REMOTE_USER_KEY)
