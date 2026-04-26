#!/usr/bin/python3
# coding: utf-8

# built-in imports first (no local dependencies)
import logging
from datetime import datetime, timedelta, timezone
import time
from urllib.parse import urlparse, parse_qs, urlunparse
import json
from traceback import format_exc
from mimetypes import guess_type

import flask
from flask_cors import cross_origin
from markupsafe import escape
from werkzeug.exceptions import NotFound, HTTPException

# WSGI application object - must be at module level for Vercel
app = flask.Flask(
    import_name=__name__,
    template_folder='theme/default/templates',
    static_folder=None
)

# ========== Init (lazy for Vercel) ==========

# region init

# Default placeholders - will be replaced during lazy init
c = None
d = None
p = None
l = None
version_str = 'unknown'
version = (0, 0, 0)
init_error = None

_initialized = False

def _do_init():
    global _initialized, c, d, p, l, version_str, version, init_error
    if _initialized or init_error:
        return init_error is None
    try:
        from toml import load as load_toml
        from config import Config as config_init
        import utils as u
        from data import Data as data_init
        import plugin as pl

        # get version info
        with open(u.get_path('pyproject.toml'), 'r', encoding='utf-8') as f:
            file: dict = load_toml(f).get('tool', {}).get('sleepy-plugin', {})
            version_str = file.get('version-str', 'unknown')
            version = tuple(file.get('version', (0, 0, 0)))
            f.close()

        # init flask app
        app.json.ensure_ascii = False

        # init logger
        l = logging.getLogger(__name__)
        logging.basicConfig(level=logging.DEBUG)
        root_logger = logging.getLogger()
        root_logger.handlers.clear()
        shandler = logging.StreamHandler()
        shandler.setFormatter(u.CustomFormatter(colorful=False))
        root_logger.addHandler(shandler)

        # init config
        c = config_init().config

        # continue init logger
        root_logger.level = logging.DEBUG if c.main.debug else logging.INFO
        root_logger.handlers.clear()
        shandler = logging.StreamHandler()
        shandler.setFormatter(u.CustomFormatter(colorful=c.main.colorful_log, timezone=c.main.timezone))
        root_logger.addHandler(shandler)
        if c.main.log_file:
            log_file_path = u.get_path(c.main.log_file)
            l.info(f'Saving logs to {log_file_path}')
            fhandler = logging.FileHandler(log_file_path, encoding='utf-8', errors='ignore')
            fhandler.setFormatter(u.CustomFormatter(colorful=False, timezone=c.main.timezone))
            root_logger.addHandler(fhandler)

        l.info(f'{"="*15} Application Startup {"="*15}')
        l.info(f'Sleepy Server version {version_str} ({".".join(str(i) for i in version)})')

        if c.main.debug:
            app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 0
        else:
            app.config['SEND_FILE_MAX_AGE_DEFAULT'] = timedelta(seconds=c.main.cache_age)

        logging.getLogger('werkzeug').disabled = True
        from flask import cli
        cli.show_server_banner = lambda *_: None

        # init data
        d = data_init(config=c, app=app)

        if c.metrics.enabled:
            l.info('[metrics] metrics enabled, open /api/metrics to see the count.')

        # init plugin
        p = pl.PluginInit(version=version, config=c, data=d, app=app)
        p.load_plugins()

        p.trigger_event(pl.AppInitializedEvent())
        _initialized = True
        return True

    except Exception as init_err:
        init_error = init_err
        print(f'Initialization Error: {init_err}\n{format_exc()}', flush=True)
        logging.error(f'Init failed: {e}')
        return False

# endregion init

# ========== Theme ==========

# region theme


def render_template(filename: str, _dirname: str = 'templates', _theme: str | None = None, **context) -> str | None:
    _theme = _theme or flask.g.theme
    content = d.get_cached_text('theme', f'{_theme}/{_dirname}/{filename}')
    if content is not None:
        return flask.render_template_string(content, **context)
    content = d.get_cached_text('theme', f'default/{_dirname}/{filename}')
    if content is not None:
        return flask.render_template_string(content, **context)
    return None


@app.route('/static/<path:filename>', endpoint='static')
def static_proxy(filename: str):
    return flask.redirect(f'/static-themed/{flask.g.theme}/{filename}', 302)


@app.route('/static-themed/<theme>/<path:filename>')
def static_themed(theme: str, filename: str):
    try:
        resp = flask.send_from_directory('theme', f'{theme}/static/{filename}')
        return resp
    except NotFound:
        if theme != 'default':
            return flask.redirect(f'/static-themed/default/{filename}', 302)
        else:
            return flask.abort(404)


@app.route('/default/<path:filename>')
def static_default_theme(filename: str):
    if not filename.endswith('.js'):
        filename += '.js'
    return flask.send_from_directory('theme/default', filename)

# endregion theme

# ========== Error Handler ==========

# region errorhandler


@app.errorhandler(HTTPException)
def http_error_handler(e: HTTPException):
    return flask.jsonify({
        'success': False, 'code': e.code,
        'message': e.name, 'details': e.description
    }), e.code


@app.errorhandler(Exception)
def error_handler(e: Exception):
    if isinstance(e, HTTPException):
        return http_error_handler(e)
    l.error(f'Unhandled Error: {e}\n{format_exc()}' if l else str(e))
    if p:
        evt = p.trigger_event(pl.UnhandledErrorEvent(e))
        if evt.interception:
            return evt.interception
    return flask.jsonify({'success': False, 'message': str(e)}), 500

# endregion errorhandler

# ========== Request Inject ==========

# region inject


@app.before_request
def before_request():
    if not _do_init():
        return flask.jsonify({'code': -1, 'msg': f'Init failed: {init_error}'}), 500

    flask.g.perf = u.perf_counter()
    fip = flask.request.headers.get('X-Real-IP') or flask.request.headers.get('X-Forwarded-For')
    flask.g.ipstr = ((flask.request.remote_addr or '') + (f' / {fip}' if fip else ''))

    if flask.request.args.get('theme'):
        theme = flask.request.args.get('theme', 'default')
        parsed = urlparse(flask.request.full_path)
        params = parse_qs(parsed.query)
        if 'theme' in params:
            del params['theme']
        new_params = []
        for key, value in params.items():
            if isinstance(value, list):
                new_params.extend([f"{key}={v}" for v in value])
            else:
                new_params.append(f"{key}={value}")
        new_params_str = '&'.join(new_params)
        new_parsed = parsed._replace(query=new_params_str)
        new_url = urlunparse(new_parsed)
        resp = flask.redirect(new_url, 302)
        resp.set_cookie('sleepy-theme', theme, samesite='Lax')
        return resp

    elif flask.request.cookies.get('sleepy-theme'):
        flask.g.theme = flask.request.cookies.get('sleepy-theme')
    else:
        flask.g.theme = c.page.theme
    flask.g.secret = c.main.secret

    evt = p.trigger_event(pl.BeforeRequestHook())
    if evt and evt.interception:
        return evt.interception


@app.after_request
def after_request(resp: flask.Response):
    if init_error:
        return resp
    path = flask.request.path
    if c.metrics.enabled:
        d.record_metrics(path)
    l.info(f'[Request] {flask.g.ipstr} | {path} -> {resp.status_code} ({flask.g.perf()}ms)')
    evt = p.trigger_event(pl.AfterRequestHook(resp))
    if evt.interception:
        evt.response = flask.Response(evt.interception[0], evt.interception[1])
    evt.response.headers.add('X-Powered-By', 'Sleepy-Project (https://github.com/sleepy-project)')
    evt.response.headers.add('Sleepy-Version', f'{version_str} ({".".join(str(i) for i in version)})')
    return evt.response

# endregion inject

# ========== Routes ==========

# region routes-special


@app.route('/')
def index():
    more_text: str = c.page.more_text
    if c.metrics.enabled:
        daily, weekly, monthly, yearly, total = d.metric_data_index
        more_text = more_text.format(
            visit_daily=daily, visit_weekly=weekly, visit_monthly=monthly,
            visit_yearly=yearly, visit_total=total
        )
    main_card: str = render_template(
        'main.index.html', _dirname='cards', username=c.page.name,
        status=d.status_dict[1],
        last_updated=datetime.fromtimestamp(d.last_updated, timezone.utc).strftime(f'%Y-%m-%d %H:%M:%S') + ' (UTC+8)'
    )
    more_info_card: str = render_template(
        'more_info.index.html', _dirname='cards', more_text=more_text,
        username=c.page.name, learn_more_link=c.page.learn_more_link,
        learn_more_text=c.page.learn_more_text, available_themes=u.themes_available()
    )
    cards = {'main': main_card, 'more-info': more_info_card}
    for name, values in p.index_cards.items():
        value = ''
        for v in values:
            if hasattr(v, '__call__'):
                value += f'{v()}<br/>\n'
            else:
                value += f'{v}<br/>\n'
        cards[name] = value
    injects: list[str] = []
    for i in p.index_injects:
        if hasattr(i, '__call__'):
            injects.append(str(i()))
        else:
            injects.append(str(i))
    evt = p.trigger_event(pl.IndexAccessEvent(
        page_title=c.page.title, page_desc=c.page.desc, page_favicon=c.page.favicon,
        page_background=c.page.background, cards=cards, injects=injects
    ))
    if evt.interception:
        return evt.interception
    return render_template(
        'index.html', page_title=evt.page_title, page_desc=evt.page_desc,
        page_favicon=evt.page_favicon, page_background=evt.page_background,
        cards=evt.cards, inject='\n'.join(evt.injects)
    ) or flask.abort(404)


@app.route('/favicon.ico')
def favicon():
    evt = p.trigger_event(pl.FaviconAccessEvent(c.page.favicon))
    if evt.interception:
        return evt.interception
    if evt.favicon_url == '/favicon.ico':
        return serve_public('favicon.ico')
    else:
        return flask.redirect(evt.favicon_url, 302)


@app.route('/'+'git'+'hub')
def git_hub():
    return flask.redirect('ht'+'tps:'+'//git'+'hub.com/'+'slee'+'py-'+'project/sle'+'epy', 301)


@app.route('/none')
def none():
    return '', 204


@app.route('/api/meta')
@cross_origin(c.main.cors_origins)
def metadata():
    meta = {
        'success': True, 'version': version, 'version_str': version_str,
        'timezone': c.main.timezone,
        'page': {
            'name': c.page.name, 'title': c.page.title, 'desc': c.page.desc,
            'favicon': c.page.favicon, 'background': c.page.background, 'theme': c.page.theme
        },
        'status': {
            'device_slice': c.status.device_slice, 'refresh_interval': c.status.refresh_interval,
            'not_using': c.status.not_using, 'sorted': c.status.sorted, 'using_first': c.status.using_first
        },
        'metrics': c.metrics.enabled
    }
    evt = p.trigger_event(pl.MetadataAccessEvent(meta))
    if evt.interception:
        return evt.interception
    return evt.metadata


@app.route('/api/metrics')
@cross_origin(c.main.cors_origins)
def metrics():
    evt = p.trigger_event(pl.MetricsAccessEvent(d.metrics_resp))
    if evt.interception:
        return evt.interception
    return evt.metrics_response

# endregion routes-special

# ----- Status -----

# region routes-status

@app.route('/api/status/query')
@cross_origin(c.main.cors_origins)
def query_route():
    return query()

def query():
    st: int = d.status_id
    try:
        stinfo = c.status.status_list[st].model_dump()
    except:
        stinfo = {'id': -1, 'name': '[未知]', 'desc': f'未知的标识符 {st}，可能是配置问题。', 'color': 'error'}
    ret = {
        'success': True, 'time': datetime.now().timestamp(),
        'status': stinfo, 'device': d.device_list, 'last_updated': d.last_updated
    }
    if u.tobool(flask.request.args.get('meta', False)) if flask.request else False:
        ret['meta'] = metadata()
    if u.tobool(flask.request.args.get('metrics', False)) if flask.request else False:
        ret['metrics'] = d.metrics_resp
    evt = p.trigger_event(pl.QueryAccessEvent(ret))
    return evt.query_response


def _event_stream(event_id: int, ipstr: str):
    last_updated = None
    last_heartbeat = time.time()
    l.info(f'[SSE] Event stream connected: {ipstr}')
    while True:
        current_time = time.time()
        current_updated = d.last_updated
        if last_updated != current_updated:
            last_updated = current_updated
            last_heartbeat = current_time
            update_data = json.dumps(query(), ensure_ascii=False)
            event_id += 1
            yield f'id: {event_id}\nevent: update\ndata: {update_data}\n\n'
        elif current_time - last_heartbeat >= 30:
            event_id += 1
            yield f'id: {event_id}\nevent: heartbeat\ndata:\n\n'
            last_heartbeat = current_time
        time.sleep(1)


@app.route('/api/status/events')
@cross_origin(c.main.cors_origins)
def events():
    try:
        last_event_id = int(flask.request.headers.get('Last-Event-ID', '0'))
    except ValueError:
        raise u.APIUnsuccessful(400, 'Invaild Last-Event-ID header, it must be int!')
    evt = p.trigger_event(pl.StreamConnectedEvent(last_event_id))
    if evt.interception:
        return evt.interception
    ipstr: str = flask.g.ipstr
    response = flask.Response(_event_stream(last_event_id, ipstr), mimetype='text/event-stream', status=200)
    response.headers['Cache-Control'] = 'no-cache'
    response.headers['X-Accel-Buffering'] = 'no'
    response.call_on_close(lambda: (
        l.info(f'[SSE] Event stream disconnected: {ipstr}'),
        p.trigger_event(pl.StreamDisconnectedEvent())
    ))
    return response


@app.route('/api/status/set')
@cross_origin(c.main.cors_origins)
@u.require_secret()
def set_status():
    status = escape(flask.request.args.get('status'))
    try:
        status = int(status)
    except:
        raise u.APIUnsuccessful(400, 'argument \'status\' must be int')
    if not status == d.status_id:
        old_status = d.status
        new_status = d.get_status(status)
        evt = p.trigger_event(pl.StatusUpdatedEvent(
            old_exists=old_status[0], old_status=old_status[1],
            new_exists=new_status[0], new_status=new_status[1]
        ))
        if evt.interception:
            return evt.interception
        status = evt.new_status.id
        d.status_id = status
    return {'success': True, 'set_to': status}


@app.route('/api/status/list')
@cross_origin(c.main.cors_origins)
def get_status_list():
    evt = p.trigger_event(pl.StatuslistAccessEvent(c.status.status_list))
    if evt.interception:
        return evt.interception
    return {'success': True, 'status_list': [i.model_dump() for i in evt.status_list]}

# endregion routes-status

# ----- Device -----

# region routes-device


@app.route('/api/device/set', methods=['GET', 'POST'])
@cross_origin(c.main.cors_origins)
@u.require_secret()
def device_set():
    if flask.request.method == 'GET':
        args = dict(flask.request.args)
        device_id = args.pop('id', None)
        device_show_name = args.pop('show_name', None)
        device_using = u.tobool(args.pop('using', None))
        device_status = args.pop('status', None) or args.pop('app_name', None)
        args.pop('secret', None)
        evt = p.trigger_event(pl.DeviceSetEvent(
            device_id=device_id, show_name=device_show_name,
            using=device_using, status=device_status, fields=args
        ))
        if evt.interception:
            return evt.interception
        d.device_set(id=evt.device_id, show_name=evt.show_name, using=evt.using, status=evt.status, fields=evt.fields)
    elif flask.request.method == 'POST':
        try:
            req: dict = flask.request.get_json()
            evt = p.trigger_event(pl.DeviceSetEvent(
                device_id=req.get('id'), show_name=req.get('show_name'),
                using=req.get('using'), status=req.get('status') or req.get('app_name'),
                fields=req.get('fields') or {}
            ))
            if evt.interception:
                return evt.interception
            d.device_set(id=evt.device_id, show_name=evt.show_name, using=evt.using, status=evt.status, fields=evt.fields)
        except Exception as e:
            if isinstance(e, u.APIUnsuccessful):
                raise e
            else:
                raise u.APIUnsuccessful(400, f'missing param or wrong param type: {e}')
    else:
        raise u.APIUnsuccessful(405, '/api/device/set only supports GET and POST method!')
    return {'success': True}


@app.route('/api/device/remove')
@cross_origin(c.main.cors_origins)
@u.require_secret()
def device_remove():
    device_id = flask.request.args.get('id')
    if not device_id:
        raise u.APIUnsuccessful(400, 'Missing device id!')
    device = d.device_get(device_id)
    if device:
        evt = p.trigger_event(pl.DeviceRemovedEvent(
            exists=True, device_id=device_id, show_name=device.show_name,
            using=device.using, status=device.status, fields=device.fields
        ))
    else:
        evt = p.trigger_event(pl.DeviceRemovedEvent(
            exists=False, device_id=device_id, show_name=None,
            using=None, status=None, fields=None
        ))
    if evt.interception:
        return evt.interception
    d.device_remove(evt.device_id)
    return {'success': True}


@app.route('/api/device/clear')
@cross_origin(c.main.cors_origins)
@u.require_secret()
def device_clear():
    evt = p.trigger_event(pl.DeviceClearedEvent(d._raw_device_list))
    if evt.interception:
        return evt.interception
    d.device_clear()
    return {'success': True}


@app.route('/api/device/screenshot', methods=['POST'])
@cross_origin(c.main.cors_origins)
@u.require_secret()
def device_screenshot():
    device_id = flask.request.form.get('device_id')
    if not device_id:
        raise u.APIUnsuccessful(400, 'Missing device_id')
    if 'screenshot' not in flask.request.files:
        raise u.APIUnsuccessful(400, 'Missing screenshot file')
    file = flask.request.files['screenshot']
    if file.filename == '':
        raise u.APIUnsuccessful(400, 'Empty filename')
    import os
    screenshot_dir = u.get_path('data/screenshots')
    os.makedirs(screenshot_dir, exist_ok=True)
    import time
    filename = f'{device_id}_{int(time.time())}.png'
    filepath = os.path.join(screenshot_dir, filename)
    file.save(filepath)
    d.device_set(device_id, '', '', '', fields={'screenshot': filename})
    return {'success': True, 'screenshot': filename}


@app.route('/api/device/screenshot/<filename>')
@cross_origin(c.main.cors_origins)
def get_screenshot(filename):
    import os
    screenshot_path = u.get_path(f'data/screenshots/{filename}')
    if not os.path.exists(screenshot_path):
        raise u.APIUnsuccessful(404, 'Screenshot not found')
    return flask.send_file(screenshot_path, mimetype='image/png')


@app.route('/api/device/private')
@u.require_secret()
@cross_origin(c.main.cors_origins)
def device_private_mode():
    private = u.tobool(flask.request.args.get('private'))
    if private == None:
        raise u.APIUnsuccessful(400, '\'private\' arg must be boolean')
    elif not private == d.private_mode:
        evt = p.trigger_event(pl.PrivateModeChangedEvent(d.private_mode, private))
        if evt.interception:
            return evt.interception
        d.private_mode = evt.new_status
    return {'success': True}

# endregion routes-device

# ----- Panel (Admin) -----

# region routes-panel


@app.route('/panel')
@u.require_secret(redirect_to='/panel/login')
def admin_panel():
    cards = {}
    for name, card in p.panel_cards.items():
        if hasattr(card['content'], '__call__'):
            cards[name] = card.copy()
            cards[name]['content'] = card['content']()
        else:
            cards[name] = card
    inject = ''
    for i in p.panel_injects:
        if hasattr(i, '__call__'):
            inject += str(i()) + '\n'
        else:
            inject += str(i) + '\n'
    return render_template(
        'panel.html', c=c, current_theme=flask.g.theme,
        available_themes=u.themes_available(), cards=cards, inject=inject
    ) or flask.abort(404)


@app.route('/panel/login')
def login():
    cookie_token = flask.request.cookies.get('sleepy-secret')
    if cookie_token == c.main.secret:
        return flask.redirect('/panel')
    return render_template('login.html', c=c, current_theme=flask.g.theme) or flask.abort(404)


@app.route('/panel/auth', methods=['POST'])
@u.require_secret()
def auth():
    response = flask.make_response({
        'success': True, 'code': 'OK', 'message': 'Login successful'
    })
    max_age = 30 * 24 * 60 * 60
    response.set_cookie('sleepy-secret', c.main.secret, max_age=max_age, httponly=True, samesite='Lax')
    l.debug('[Panel] Login successful, cookie set')
    return response


@app.route('/panel/logout')
def logout():
    response = flask.make_response(flask.redirect('/panel/login'))
    response.delete_cookie('sleepy-secret')
    l.debug('[Panel] Logout successful')
    return response


@app.route('/panel/verify', methods=['GET', 'POST'])
@cross_origin(c.main.cors_origins)
@u.require_secret()
def verify_secret():
    l.debug('[Panel] Secret verified')
    return {'success': True, 'code': 'OK', 'message': 'Secret verified'}

# endregion routes-panel


@app.route('/<path:path_name>')
def serve_public(path_name: str):
    l.debug(f'Serving static file: {path_name}')
    file = d.get_cached_file('data/public', path_name) or d.get_cached_file('public', path_name)
    if file:
        mime = guess_type(path_name)[0] or 'text/plain'
        return flask.send_file(file, mimetype=mime)
    else:
        return flask.abort(404)

# endregion routes

# ========== End ==========

# region run

# Add init error handler if init failed
if init_error:
    @app.route('/<path:path>')
    def init_error_page(path=None):
        return f'''<html><body>
<h2>Sleepy Application Initialization Failed</h2>
<p>Error: {init_error}</p>
<p>Please check Vercel logs for details.</p>
</body></html>''', 500

try:
    p.trigger_event(pl.AppStartedEvent())
except NameError:
    pass

if __name__ == '__main__':
    try:
        l.info(f'Hi {c.page.name}!')
        listening = f'{f"[{c.main.host}]" if ":" in c.main.host else c.main.host}:{c.main.port}'
        l.info(f'Listening service on: http://{listening}{" (debug enabled)" if c.main.debug else ""}')
        app.run(host=c.main.host, port=c.main.port, debug=c.main.debug, use_reloader=False, threaded=True)
    except Exception as e:
        print(f'Critical error: {e}', flush=True)

# endregion run
